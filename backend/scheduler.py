"""
Scheduler background sederhana: tiap CHECK_INTERVAL_SECONDS, cek scanner_cache
buat ticker Strong dengan score tertinggi (cuma #1, bukan semua), rangkum
alasan lewat Groq, render chart + garis support/resistance, kirim ke Telegram
sebagai foto. Jalan di process yang sama kayak FastAPI lewat asyncio.create_task
— gak butuh cron/Celery/proses terpisah, paling sederhana buat single-user tool.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone
import yfinance as yf
from config import supabase
from routers.scanner import _get_history
from routers.mentor_calls import refresh_mentor_calls
from routers.daily_briefing import _generate_briefing
from levels import support_resistance
from chart_render import render_chart
from groq_client import analyze_alert, pick_alert_candidate
from forex_factory import get_forex_events
from telegram_bot import send_alert_photo, send_alert, get_channel_updates
from telegram_scrape import fetch_channel_posts
from routers.intel import submit_intel, IntelInput
from routers.settings import DEFAULTS as SETTINGS_DEFAULTS
from config import TELEGRAM_CHANNEL_IDS, TELEGRAM_SCRAPE_CHANNELS

TELEGRAM_SCRAPE_INTERVAL_SECONDS = 10 * 60  # preview publik gak realtime kayak bot API, polling 10 menit cukup

_last_seen_post_id: dict[str, int] = {}

MORNING_ROUTINE_HOUR = 6  # 06:00 waktu lokal server, sebelum market IDX buka jam 09:00

CHECK_INTERVAL_SECONDS = 60 * 60  # 1 jam — dedup per-ticker jadi gak ngaruh ke spam, cuma ke seberapa cepet nyampe

_alerted_today: set[str] = set()
_invalidated_today: set[str] = set()
_watchlist_alerted_today: set[str] = set()
_econ_reminded_today: set[str] = set()
_alerted_date: date | None = None


def _load_settings() -> dict:
    """Baca app_settings (single-row, id=1) — fallback ke default kalau tabel
    belum di-setup / Supabase error, biar semua scheduled check tetep jalan
    (behavior default) walau user belum sempet jalanin SQL setup."""
    try:
        res = supabase.table("app_settings").select("*").eq("id", 1).limit(1).execute()
        if res.data:
            row = res.data[0]
            return {**SETTINGS_DEFAULTS, **{k: row[k] for k in SETTINGS_DEFAULTS if k in row}}
    except Exception:
        pass
    return SETTINGS_DEFAULTS


def _reset_if_new_day() -> None:
    global _alerted_date, _alerted_today, _invalidated_today, _watchlist_alerted_today, _econ_reminded_today
    today = date.today()
    if _alerted_date != today:
        _alerted_date = today
        _alerted_today = set()
        _invalidated_today = set()
        _watchlist_alerted_today = set()
        _econ_reminded_today = set()
        # ponytail: dedup in-memory, bukan tabel Supabase — kalau backend restart
        # di hari yang sama, ticker yang udah dialert bisa ke-alert ulang sekali.
        # Upgrade ke tabel log kalau ini beneran ganggu di pemakaian nyata.


def _check_invalidated() -> None:
    """Ticker yang tadinya di-alert Strong hari ini, cek ulang statusnya —
    kalau udah gak Strong lagi, kirim 1 notif teks (bukan foto), sekali aja
    per ticker per hari."""
    pending = _alerted_today - _invalidated_today
    if not pending:
        return
    try:
        res = supabase.table("scanner_cache").select("ticker,total_score,signal").in_("ticker", list(pending)).execute()
    except Exception:
        return
    for row in res.data:
        if row["signal"] != "Strong":
            send_alert(
                f"⚪ Update: {row['ticker']} udah gak Strong lagi "
                f"(sekarang {row['signal']}, score {row['total_score']}/100)."
            )
            _invalidated_today.add(row["ticker"])


def _build_caption(ticker: str, total_score: int, levels: dict, reasoning: dict, faktor_pendukung: list[str]) -> str:
    faktor_line = f"Faktor pendukung: {'; '.join(faktor_pendukung)}\n\n" if faktor_pendukung else ""
    return (
        f"🔴 STRONG SIGNAL — {ticker} (score {total_score}/100)\n\n"
        f"{faktor_line}"
        f"Kenapa kuat: {reasoning.get('alasan_strong', '-')}\n\n"
        f"Entry: Rp{levels['entry_low']:,.0f} - Rp{levels['entry_high']:,.0f}\n"
        f"Stop loss: Rp{levels['stop_loss']:,.0f}\n"
        f"Risk: {levels['risk_pct']}%\n"
        f"Alasan risk: {reasoning.get('alasan_risk', '-')}"
    )


def _recent_news_by_ticker(days: int = 3) -> dict[str, list[dict]]:
    """{ticker: [{tanggal, sentiment, poin_penting, sektor_terkait}]} dari
    daily_market_intel N hari terakhir — dipakai buat cross-check berita di
    seleksi kandidat alert, bukan fetch baru (data udah ada dari intel pipeline)."""
    since = (date.today() - timedelta(days=days)).isoformat()
    try:
        res = supabase.table("daily_market_intel").select("tanggal,summary_ai").gte("tanggal", since).execute()
    except Exception:
        return {}

    by_ticker: dict[str, list[dict]] = {}
    for row in res.data:
        summary = row.get("summary_ai")
        if not summary:
            continue
        for t in summary.get("saham_disebut", []):
            by_ticker.setdefault(t.upper(), []).append({
                "tanggal": row["tanggal"],
                "sentiment": summary.get("sentiment"),
                "poin_penting": summary.get("poin_penting", []),
                "sektor_terkait": summary.get("sektor_terkait", []),
            })
    return by_ticker


def _active_mentor_calls() -> dict[str, dict]:
    """{ticker: row} buat mentor_calls yang status-nya masih "Running" (bukan
    yang udah closed/TP/CL)."""
    try:
        res = supabase.table("mentor_calls").select("*").execute()
    except Exception:
        return {}
    return {r["ticker"]: r for r in res.data if "run" in (r.get("status") or "").lower()}


def _macro_sector_set(macro_events: list[dict]) -> set[str]:
    """Sektor spesifik yang kena macro event High/Medium minggu ini. "All
    Sectors" (mapping default event USD, hampir selalu ada tiap minggu)
    sengaja DIKELUARIN — itu bukan faktor pendukung yang informatif buat 1
    ticker spesifik, cuma bikin macro_sector_match kebobolan match ke semua
    kandidat."""
    sectors = set()
    for e in macro_events:
        for s in (e.get("idx_sector_impact") or "").split(","):
            s = s.strip()
            if s and s not in ("—", "All Sectors"):
                sectors.add(s)
    return sectors


BREAKOUT_TECHNICAL_THRESHOLD = 12  # technical_score minimal (dari 20) buat dianggap "breakout+volume kekonfirmasi"


def _gather_candidates(macro_events: list[dict], settings: dict, pool_limit: int = 20) -> list[dict]:
    """Pool kandidat alert: filosofi "buy on breakout+volume, bukan buy on news"
    (berita/hype publik biasanya udah telat, smart money masuk duluan sebelum
    ramai diberitakan) — jadi syarat UTAMA masuk pool itu Technical Score tinggi
    (breakout resistance 20 hari + volume gede, lihat scoring.py::technical_score),
    BUKAN ada berita pendukung. Mentor call aktif tetap ikut union (itu analisa
    manusia beneran, beda kelas sama hype berita). Berita/macro tetap di-enrich
    di bawah, tapi cuma jadi konteks tambahan buat Groq — bahkan kalau beritanya
    udah rame duluan padahal belum breakout, itu jadi WARNING telat, bukan
    pendukung (lihat pick_alert_candidate). Baseline total_score minimal-nya
    dari `settings["alert_threshold"]` — user-configurable via Settings
    (slider "Alert Threshold"), bukan hardcoded lagi."""
    try:
        scan_res = (
            supabase.table("scanner_cache")
            .select("ticker,total_score,signal,sector,technical_score")
            .gte("total_score", settings["alert_threshold"])
            .execute()
        )
        scan_by_ticker = {r["ticker"]: r for r in scan_res.data}
    except Exception:
        scan_by_ticker = {}

    mentor_by_ticker = _active_mentor_calls()
    news_by_ticker = _recent_news_by_ticker()
    macro_sectors = _macro_sector_set(macro_events)

    breakout_tickers = {
        t for t, r in scan_by_ticker.items()
        if (r.get("technical_score") or 0) >= BREAKOUT_TECHNICAL_THRESHOLD
    }
    pool = (breakout_tickers | set(mentor_by_ticker)) - _alerted_today

    candidates = []
    for ticker in pool:
        scan = scan_by_ticker.get(ticker)
        mentor = mentor_by_ticker.get(ticker)
        berita = news_by_ticker.get(ticker)
        sector = scan["sector"] if scan else None
        macro_match = bool(sector) and sector in macro_sectors
        candidates.append({
            "ticker": ticker,
            "total_score": scan["total_score"] if scan else None,
            "signal": scan["signal"] if scan else None,
            "technical_score": scan["technical_score"] if scan else None,
            "breakout_confirmed": ticker in breakout_tickers,
            "sector": sector,
            "macro_sector_match": macro_match,
            "mentor_call": (
                {"status": mentor["status"], "buy_price": mentor["buy_price"]} if mentor else None
            ),
            "berita": berita,
        })

    # urut dari breakout+volume paling kuat (technical_score), BUKAN total_score —
    # total_score masih kecampur Accumulation Score yang mock, technical_score
    # murni breakout+volume yang REAL
    candidates.sort(key=lambda c: c["technical_score"] or 0, reverse=True)
    return candidates[:pool_limit]


def _fetch_fundamental_summary(ticker: str) -> dict | None:
    """Ringkasan fundamental buat 1 ticker doang (pemenang fase 1) — pola sama
    kayak scanner.py::get_stock_detail, tapi cuma dipanggil 1x per alert biar
    gak nambah beban rate-limit yfinance ke seluruh pool kandidat."""
    try:
        info = yf.Ticker(f"{ticker}.JK").info
    except Exception:
        return None
    return {
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "summary": (info.get("longBusinessSummary") or "")[:500],
    }


SIGNAL_TIMEOUT_DAYS = 14  # kalau 14 hari gak kena TP/SL, tutup posisi & itung menang/kalah dari tanda outcome_pct


def _check_signal_outcomes() -> None:
    """Cek tiap signal_alerts yang masih 'open' — udah kena target (tp_hit),
    stop_loss (sl_hit), atau timeout (>14 hari, belum kena dua-duanya). Dipanggil
    tiap pagi dari run_morning_routine(), sebelum market buka (data closing
    kemarin udah final)."""
    try:
        res = supabase.table("signal_alerts").select("*").eq("status", "open").execute()
    except Exception:
        return  # tabel belum di-setup, skip diem-diem

    now = datetime.now(timezone.utc)
    for row in res.data:
        try:
            hist = _get_history(row["ticker"])
            price_now = float(hist["Close"].iloc[-1])
        except Exception:
            continue  # gagal fetch harga ticker ini, coba lagi besok

        alerted_at = datetime.fromisoformat(row["alerted_at"])
        days_open = (now - alerted_at).days

        status = None
        if price_now >= row["target"]:
            status = "tp_hit"
        elif price_now <= row["stop_loss"]:
            status = "sl_hit"
        elif days_open > SIGNAL_TIMEOUT_DAYS:
            status = "timeout"

        if not status:
            continue

        outcome_pct = round((price_now - row["entry_price"]) / row["entry_price"] * 100, 2)
        try:
            supabase.table("signal_alerts").update({
                "status": status,
                "closed_at": now.isoformat(),
                "close_price": price_now,
                "outcome_pct": outcome_pct,
            }).eq("id", row["id"]).execute()
        except Exception:
            pass


def check_and_alert() -> None:
    _reset_if_new_day()
    settings = _load_settings()
    if not settings["notif_strong_signal"]:
        return  # user matiin "Strong signal alerts" di Settings

    _check_invalidated()

    macro_events = [e for e in get_forex_events() if e["impact"] in ("High", "Medium")]
    candidates = _gather_candidates(macro_events, settings)
    if not candidates:
        return

    try:
        pick = pick_alert_candidate(candidates, macro_events)
    except Exception:
        return  # Groq gagal, coba lagi interval berikutnya

    ticker = pick.get("pilih")
    if not ticker:
        return  # sengaja gak ada yang meyakinkan — mending gak ada call daripada call asal

    candidate = next((c for c in candidates if c["ticker"] == ticker), None)
    if candidate is None or candidate.get("signal") not in ("Strong", "Moderate"):
        return  # jaga-jaga kalau Groq halusinasi ticker di luar pool / gak penuhi syarat skor
    if not candidate.get("breakout_confirmed") and not candidate.get("mentor_call"):
        return  # jaga-jaga kalau Groq ngelanggar instruksi sendiri (pilih modal berita doang, gak breakout)

    try:
        score_res = (
            supabase.table("scanner_cache")
            .select("total_score,volume_score,price_score,accumulation_score,technical_score")
            .eq("ticker", ticker)
            .limit(1)
            .execute()
        )
        score_row = score_res.data[0]
    except Exception:
        return

    try:
        hist = _get_history(ticker)
        levels = support_resistance(hist)
        chart_png = render_chart(ticker, hist, levels["support"], levels["resistance"])
        score_breakdown = {
            "volume_score": score_row["volume_score"],
            "price_score": score_row["price_score"],
            "accumulation_score": score_row["accumulation_score"],
            "technical_score": score_row["technical_score"],
        }
        context = {
            "faktor_pendukung": pick.get("faktor_pendukung", []),
            "berita": candidate.get("berita"),
            "mentor_call": candidate.get("mentor_call"),
            "event_ekonomi_global": macro_events,
            "fundamental": _fetch_fundamental_summary(ticker),
        }
        reasoning = analyze_alert(ticker, score_breakdown, levels, context)
    except Exception:
        return  # gagal di yfinance/Groq/render — coba lagi interval berikutnya, jangan tandain alerted

    caption = _build_caption(ticker, score_row["total_score"], levels, reasoning, pick.get("faktor_pendukung", []))
    if not send_alert_photo(chart_png, caption):
        return  # Telegram belum di-connect di Settings, atau gagal kirim

    _alerted_today.add(ticker)

    try:
        supabase.table("signal_alerts").insert({
            "ticker": ticker,
            "entry_price": float(hist["Close"].iloc[-1]),
            "target": levels["resistance"],
            "stop_loss": levels["stop_loss"],
        }).execute()
    except Exception:
        pass  # tabel belum di-setup / gagal simpen — jangan gagalin alert-nya cuma gara-gara ini


def _check_watchlist_alerts() -> None:
    """Ticker di watchlist user yang breakout+volume kekonfirmasi hari ini
    (technical_score >= threshold) dapet notif ringan sendiri — independen
    dari check_and_alert() (yang cuma milih 1 "pick terbaik" se-market),
    ini soal relevansi personal: ticker yang user pantau sendiri."""
    try:
        watch_res = supabase.table("watchlist").select("ticker").execute()
        tickers = [r["ticker"] for r in watch_res.data]
    except Exception:
        return
    if not tickers:
        return

    try:
        scan_res = (
            supabase.table("scanner_cache")
            .select("ticker,technical_score")
            .in_("ticker", tickers)
            .execute()
        )
    except Exception:
        return

    for row in scan_res.data:
        ticker = row["ticker"]
        if ticker in _watchlist_alerted_today:
            continue
        if (row.get("technical_score") or 0) >= BREAKOUT_TECHNICAL_THRESHOLD:
            if send_alert(f"⭐ Watchlist: {ticker} breakout+volume kekonfirmasi (technical {row['technical_score']}/20)."):
                _watchlist_alerted_today.add(ticker)


def _check_economic_reminders() -> None:
    """Gated `notif_economic_events` di Settings. Event High impact hari ini
    (dari Forex Factory, udah di-cache 5 menit di forex_factory.py), dedup
    in-memory biar gak nge-spam tiap jam buat event yang sama."""
    settings = _load_settings()
    if not settings["notif_economic_events"]:
        return

    today = date.today().isoformat()
    try:
        events = [e for e in get_forex_events() if e["impact"] == "High" and e["date"] == today]
    except Exception:
        return
    new_events = [e for e in events if e["event"] not in _econ_reminded_today]
    if not new_events:
        return

    lines = ["📅 Event Ekonomi High Impact Hari Ini:"]
    for e in new_events:
        lines.append(f"{e['flag']} {e['time_wib']} WIB — {e['event']} ({e['currency']})")
    if send_alert("\n".join(lines)):
        for e in new_events:
            _econ_reminded_today.add(e["event"])


def _check_portfolio_risk() -> None:
    """Gated `notif_portfolio_risk` di Settings. Reuse holdings terakhir yang
    kesimpen otomatis pas user klik "Jalankan Simulasi" di Portfolio Simulation
    (routers/portfolio.py::simulate_portfolio) — gak ada UI simpan-portofolio
    terpisah, ini "nangkep" opportunistic."""
    settings = _load_settings()
    if not settings["notif_portfolio_risk"]:
        return

    try:
        res = supabase.table("portfolio_holdings").select("holdings").eq("id", 1).limit(1).execute()
    except Exception:
        return
    if not res.data or not res.data[0].get("holdings"):
        return

    from routers.portfolio import _simulate
    try:
        result = _simulate(res.data[0]["holdings"])
    except Exception:
        return

    if result.get("overall_risk") == "high":
        send_alert(f"⚠️ Portfolio Risk HIGH\n\n{result.get('portfolio_impact_summary', '-')}")


NIGHT_RECAP_HOUR = 20  # 20:00 waktu lokal server, abis market tutup


def _send_night_recap() -> None:
    """Gated `notif_daily_recap` di Settings. Recap ringan: closing IHSG,
    jumlah Strong signal hari ini, win rate NEXUS kalau datanya udah ada."""
    settings = _load_settings()
    if not settings["notif_daily_recap"]:
        return

    from routers.scanner import get_ihsg
    from routers.signal_track import get_signal_track_stats

    try:
        ihsg = get_ihsg()
    except Exception:
        ihsg = None

    try:
        strong_res = supabase.table("scanner_cache").select("ticker").eq("signal", "Strong").execute()
        strong_count = len(strong_res.data)
    except Exception:
        strong_count = 0

    stats = get_signal_track_stats()

    lines = ["🌙 Recap Malam Ini"]
    if ihsg:
        lines.append(f"IHSG: {ihsg['price']:,.0f} ({ihsg['change_pct']:+.2f}%)")
    lines.append(f"Strong signal hari ini: {strong_count} ticker")
    if stats.get("win_rate_pct") is not None:
        lines.append(f"Win rate NEXUS: {stats['win_rate_pct']}% ({stats['tp_hit']} TP / {stats['sl_hit']} SL)")
    send_alert("\n".join(lines))


async def run_scheduler() -> None:
    while True:
        check_and_alert()
        _check_watchlist_alerts()
        _check_economic_reminders()
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def run_night_recap() -> None:
    while True:
        now = datetime.now()
        target = now.replace(hour=NIGHT_RECAP_HOUR, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            _send_night_recap()
        except Exception:
            pass


async def run_telegram_channel_listener() -> None:
    """Long-poll Telegram getUpdates buat channel_post dari TELEGRAM_CHANNEL_IDS,
    forward teksnya langsung ke submit_intel() (manggil function-nya langsung,
    bukan HTTP ke diri sendiri — sama proses). Skip diem-diem kalau belum ada
    channel yang di-setup di .env."""
    if not TELEGRAM_CHANNEL_IDS:
        return

    offset = None
    while True:
        try:
            updates = await asyncio.to_thread(get_channel_updates, offset)  # blocking (long-poll 25s), jangan nahan event loop
        except Exception:
            await asyncio.sleep(10)
            continue

        for u in updates:
            offset = u["update_id"] + 1
            post = u.get("channel_post")
            if not post:
                continue
            chat_id = str(post["chat"]["id"])
            if chat_id not in TELEGRAM_CHANNEL_IDS:
                continue
            text = post.get("text") or post.get("caption") or ""
            if not text.strip():
                continue
            try:
                submit_intel(IntelInput(sumber=post["chat"].get("title", "Telegram Channel"), isi_teks=text))
            except Exception:
                pass  # gagal simpen/ringkas 1 pesan, lanjut ke update berikutnya


async def run_telegram_scrape_listener() -> None:
    """Poll preview publik Telegram (t.me/s/<username>) buat channel yang kita
    cuma subscriber biasa (gak bisa jadiin bot admin). Forward post baru ke
    /intel. Skip diem-diem kalau TELEGRAM_SCRAPE_CHANNELS kosong."""
    if not TELEGRAM_SCRAPE_CHANNELS:
        return
    while True:
        for username in TELEGRAM_SCRAPE_CHANNELS:
            try:
                posts = await asyncio.to_thread(fetch_channel_posts, username)
            except Exception:
                continue
            if not posts:
                continue

            if username not in _last_seen_post_id:
                # run pertama kali liat channel ini — catet baseline doang,
                # jangan forward histori lama yang udah numpuk di halaman preview
                _last_seen_post_id[username] = max(p["post_id"] for p in posts)
                continue

            last_seen = _last_seen_post_id[username]
            new_posts = sorted((p for p in posts if p["post_id"] > last_seen), key=lambda p: p["post_id"])
            for p in new_posts:
                try:
                    submit_intel(IntelInput(sumber=f"Telegram @{username}", isi_teks=p["text"]))
                except Exception:
                    pass
                _last_seen_post_id[username] = p["post_id"]
        await asyncio.sleep(TELEGRAM_SCRAPE_INTERVAL_SECONDS)


async def run_morning_routine() -> None:
    """Sekali tiap hari jam MORNING_ROUTINE_HOUR: refresh mentor_calls dari
    Google Sheets, terus sintesis daily_briefing dari intel yang numpuk
    beberapa hari terakhir — biar pas dibuka paginya udah fresh, gak nunggu."""
    while True:
        now = datetime.now()
        target = now.replace(hour=MORNING_ROUTINE_HOUR, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            refresh_mentor_calls()
        except Exception:
            pass  # gagal hari ini, coba lagi besok — jangan crash scheduler
        try:
            _generate_briefing()
        except Exception:
            pass
        try:
            _check_signal_outcomes()
        except Exception:
            pass
        try:
            _check_portfolio_risk()
        except Exception:
            pass
