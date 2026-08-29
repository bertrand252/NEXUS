"""
Scheduler background sederhana: tiap CHECK_INTERVAL_SECONDS, cek scanner_cache
buat ticker Strong dengan score tertinggi (cuma #1, bukan semua), rangkum
alasan lewat Groq, render chart + garis support/resistance, kirim ke Telegram
sebagai foto. Jalan di process yang sama kayak FastAPI lewat asyncio.create_task
— gak butuh cron/Celery/proses terpisah, paling sederhana buat single-user tool.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone
from html import escape as _esc
import yfinance as yf
from config import supabase, WIB, today_wib
from routers.scanner import _get_history
from routers.mentor_calls import refresh_mentor_calls
from routers.daily_briefing import _generate_briefing
from levels import support_resistance, detect_trend_channel
from chart_render import render_chart
from groq_client import analyze_alert, pick_alert_candidate, assess_running_positions
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

# Swing/Invest/BSJP itu gak urgent (gak butuh real-time siang hari) — alert-nya
# sengaja dibatesin ke jam market TUTUP, biar gak nge-ganggu pas lagi mantau
# market beneran (kalau nanti Scalping/BPJS dibangun, itu baru butuh real-time
# jam market — kebalikannya window ini)
ALERT_OFFHOURS_START = 17  # jam 17:00
ALERT_OFFHOURS_END = 8     # jam 08:00 — window nginep lewat tengah malam


def _now_wib() -> datetime:
    """Server (Railway) jalan di UTC, BUKAN WIB — datetime.now() polos bakal
    ngasih jam 7 lebih awal dari yang dimaksud (ketauan dari bug: BSJP yang
    dimaksud jam 15:30 WIB kekirim jam 22:30 WIB = 15:30 UTC). SEMUA logic
    penjadwalan (jam berapa kirim apa) wajib pake ini, bukan datetime.now()."""
    return datetime.now(WIB)


def _in_offhours_window() -> bool:
    hour = _now_wib().hour
    return hour >= ALERT_OFFHOURS_START or hour < ALERT_OFFHOURS_END


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


def _dedup_seen(category: str, key: str) -> bool:
    """Dedup di tabel Supabase (alert_dedup), BUKAN in-memory — dedup in-memory
    kena reset tiap Railway redeploy/restart, bikin alert yang sama kekirim
    ulang tiap kali ada deploy (ketauan dari laporan user, event ekonomi yang
    sama kekirim berkali-kali gara-gara sesi ngoding aktif push berkali-kali).
    Scoped per (category, key, dedup_date=hari ini WIB)."""
    try:
        res = (
            supabase.table("alert_dedup").select("id")
            .eq("category", category).eq("key", key).eq("dedup_date", today_wib().isoformat())
            .limit(1).execute()
        )
        return bool(res.data)
    except Exception:
        return False  # tabel belum di-setup / gagal query — jangan block alert cuma gara-gara ini


def _dedup_seen_keys(category: str) -> set[str]:
    """Semua key yang udah ke-dedup hari ini buat 1 category (dipake
    _check_invalidated buat tau semua ticker yang udah dialert)."""
    try:
        res = (
            supabase.table("alert_dedup").select("key")
            .eq("category", category).eq("dedup_date", today_wib().isoformat())
            .execute()
        )
        return {r["key"] for r in res.data}
    except Exception:
        return set()


def _dedup_mark(category: str, key: str) -> None:
    try:
        supabase.table("alert_dedup").insert({
            "category": category, "key": key, "dedup_date": today_wib().isoformat(),
        }).execute()
    except Exception:
        pass  # tabel belum ada, atau race/duplicate — gak fatal, worst case kirim dobel sesekali


def _dedup_count_since(category: str, since: date) -> int:
    """Jumlah alert kategori X dalam N hari terakhir — dipake buat cap
    MINGGUAN (Swing itu 1-2x/minggu, bukan harian — gerakannya gak secepat
    itu, kalau ngirim tiap hari itu lebih ke perilaku Scalping)."""
    try:
        res = (
            supabase.table("alert_dedup").select("id", count="exact")
            .eq("category", category).gte("dedup_date", since.isoformat())
            .execute()
        )
        return res.count or 0
    except Exception:
        return 0


def _check_invalidated() -> None:
    """Ticker yang tadinya di-alert Strong hari ini, cek ulang statusnya —
    kalau udah gak Strong lagi, kirim 1 notif teks (bukan foto), sekali aja
    per ticker per hari."""
    pending = _dedup_seen_keys("alerted") - _dedup_seen_keys("invalidated")
    if not pending:
        return
    try:
        res = supabase.table("scanner_cache").select("ticker,total_score,signal").in_("ticker", list(pending)).execute()
    except Exception:
        return
    for row in res.data:
        if row["signal"] != "Strong":
            send_alert(
                f"⚪ <b>Update — {_esc(row['ticker'])}</b>\n\n"
                f"Udah gak Strong lagi (sekarang {_esc(row['signal'])}, score {row['total_score']}/100)."
            )
            _dedup_mark("invalidated", row["ticker"])


def _build_caption(ticker: str, total_score: int, levels: dict, reasoning: dict, faktor_pendukung: list[str]) -> str:
    """Format HTML (parse_mode diaktifin di telegram_bot.py) — desain terinspirasi
    channel signal yang biasa dipake user (bold header + emoji per section), TAPI
    bukan niru persis, cuma referensi visual biar gak "jadul". Semua teks dinamis
    (Groq/faktor pendukung) di-escape (_esc) — biar gak accidentally ngerusak
    parsing HTML Telegram kalau isinya kebetulan ada karakter < > &."""
    faktor_line = (
        f"📌 <b>Faktor pendukung:</b> {_esc('; '.join(faktor_pendukung))}\n\n"
        if faktor_pendukung else ""
    )
    return (
        f"🔥 <b>SWING SIGNAL — {_esc(ticker)}</b>\n"
        f"Score {total_score}/100 · 🎯 Gaya: Swing\n\n"
        f"✅ <b>BUY</b> Rp{levels['entry_low']:,.0f} – Rp{levels['entry_high']:,.0f}\n"
        f"🎯 <b>TARGET (TP)</b> Rp{levels['resistance']:,.0f} (+{levels['reward_pct']}%)\n"
        f"⛔ <b>STOP LOSS (CL)</b> Rp{levels['stop_loss']:,.0f} (-{levels['risk_pct']}%)\n"
        f"⚖️ <b>Risk:Reward</b> 1:{levels['rr_ratio']} — {levels['rr_label']}\n\n"
        f"{faktor_line}"
        f"📊 <b>Kenapa kuat:</b>\n{_esc(reasoning.get('alasan_strong', '-'))}\n\n"
        f"⚠️ <b>Alasan Risk/TP:</b>\n{_esc(reasoning.get('alasan_risk', '-'))}"
    )


def _recent_news_by_ticker(days: int = 3) -> dict[str, list[dict]]:
    """{ticker: [{tanggal, sentiment, poin_penting, sektor_terkait}]} dari
    daily_market_intel N hari terakhir — dipakai buat cross-check berita di
    seleksi kandidat alert, bukan fetch baru (data udah ada dari intel pipeline)."""
    since = (today_wib() - timedelta(days=days)).isoformat()
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
MIN_RR_RATIO = 1.5  # riset: 1:1.5-1:2 standar minimum umum, Swing spesifik idealnya 1:3+ — mulai
                     # dari 1.5 (gak terlalu ketat dulu), bisa dinaikin kalau kandidat kebanyakan lolos


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
            .select("ticker,total_score,signal,sector,technical_score,cocok_compression,sideways_days")
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
    pool = (breakout_tickers | set(mentor_by_ticker)) - _dedup_seen_keys("alerted")

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
            # setup mentor user — breakout dari saham yang SEBELUMNYA compression+
            # sideways lama itu sinyal lebih kuat (lihat scoring.py::compression_setup)
            "compression_setup": bool(scan and scan.get("cocok_compression")),
            "sideways_days_before": scan.get("sideways_days") if scan else None,
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
    candidates = candidates[:pool_limit]

    # syarat RR minimal — breakout+volume doang gak cukup kalau harga udah
    # deket resistance (upside dikit) sementara SL masih jauh (downside gede).
    # Dicek di sini (bukan pas gathering) biar yfinance call-nya cuma buat
    # kandidat yang UDAH lolos pool_limit, bukan semua ticker breakout.
    filtered = []
    for c in candidates:
        try:
            hist = _get_history(c["ticker"])
            lv = support_resistance(hist)
        except Exception:
            continue  # gagal fetch, skip — jangan asumsiin RR-nya oke
        if lv["rr_ratio"] < MIN_RR_RATIO:
            continue
        c["rr_ratio"] = lv["rr_ratio"]
        filtered.append(c)
    return filtered


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
ENTRY_WAIT_TIMEOUT_DAYS = 5  # kalau 5 hari harga gak pernah masuk zona entry, anggep "missed" (bukan "invalid" —
                              # bukan salah call, cuma harganya keburu lari sebelum sempet ke-entry)


def _check_entry_zone_touches() -> None:
    """signal_alerts baru masuk status 'waiting_entry' (belum 'open' beneran)
    — cek range harian (Low-High) tiap ticker overlap sama zona entry_low-
    entry_high gak. Kalau overlap: naikin status ke 'open' (baru mulai
    ditrack TP/SL/timeout dari sini) + notif excited "ENTRY ZONE!" biar user
    tau sekarang saatnya masuk. Kalau udah >5 hari gak pernah overlap:
    status 'missed' (bukan 'invalid' — panggilannya belum tentu salah, cuma
    harganya udah lari duluan sebelum sempet dikoreksi)."""
    try:
        res = supabase.table("signal_alerts").select("*").eq("status", "waiting_entry").execute()
    except Exception:
        return
    now = datetime.now(timezone.utc)
    for row in res.data:
        try:
            hist = _get_history(row["ticker"])
            day_low = float(hist["Low"].iloc[-1])
            day_high = float(hist["High"].iloc[-1])
        except Exception:
            continue

        touched = day_low <= row["entry_high"] and day_high >= row["entry_low"]
        if touched:
            try:
                supabase.table("signal_alerts").update({"status": "open"}).eq("id", row["id"]).execute()
            except Exception:
                continue
            send_alert(
                f"🎯 <b>ENTRY ZONE! — {_esc(row['ticker'])}</b>\n\n"
                f"Harga masuk zona entry Rp{row['entry_low']:,.0f} – Rp{row['entry_high']:,.0f}. "
                f"Saatnya masuk kalau masih sesuai rencana lu 🚀\n\n"
                f"🎯 TP: Rp{row['target']:,.0f}\n⛔ SL: Rp{row['stop_loss']:,.0f}"
            )
            continue

        alerted_at = datetime.fromisoformat(row["alerted_at"])
        if (now - alerted_at).days > ENTRY_WAIT_TIMEOUT_DAYS:
            try:
                supabase.table("signal_alerts").update({"status": "missed", "closed_at": now.isoformat()}).eq("id", row["id"]).execute()
            except Exception:
                continue
            send_alert(
                f"💨 <b>MISSED — {_esc(row['ticker'])}</b>\n\n"
                f"Harga gak pernah masuk zona entry Rp{row['entry_low']:,.0f} – Rp{row['entry_high']:,.0f} "
                f"dalam {ENTRY_WAIT_TIMEOUT_DAYS} hari — keburu lari duluan, bukan berarti panggilannya salah."
            )


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

        sign = "+" if outcome_pct >= 0 else ""
        if status == "tp_hit":
            send_alert(f"🎯 <b>TP TERCAPAI — {_esc(row['ticker'])}</b>\n\nProfit {sign}{outcome_pct}% (Rp{row['entry_price']:,.0f} → Rp{price_now:,.0f}).")
        elif status == "sl_hit":
            send_alert(f"⛔ <b>STOP LOSS KENA — {_esc(row['ticker'])}</b>\n\n{sign}{outcome_pct}% (Rp{row['entry_price']:,.0f} → Rp{price_now:,.0f}).")
        elif status == "timeout":
            send_alert(f"⏱ <b>TIMEOUT — {_esc(row['ticker'])}</b> ({SIGNAL_TIMEOUT_DAYS} hari)\n\n{sign}{outcome_pct}% (Rp{row['entry_price']:,.0f} → Rp{price_now:,.0f}), gak kena TP/SL.")


MAX_ALERTS_PER_WEEK = 2  # Swing itu 1-2 saham TERBAIK per MINGGU, bukan harian —
                           # gerakannya gak secepat itu. Ngirim tiap hari/tiap jam
                           # itu perilaku Scalping, bukan Swing (user eksplisit koreksi
                           # ini — awalnya sempet dibatesin 2/hari doang, masih kebanyakan)


def check_and_alert() -> None:
    if not _in_offhours_window():
        return  # Swing itu non-urgent, sengaja cuma alert pas market tutup (17:00-08:00)

    settings = _load_settings()
    if not settings["notif_strong_signal"]:
        return  # user matiin "Strong signal alerts" di Settings

    if _dedup_count_since("alerted", today_wib() - timedelta(days=7)) >= MAX_ALERTS_PER_WEEK:
        return  # udah kena limit 2 alert minggu ini

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
        channel = detect_trend_channel(hist)
        chart_png = render_chart(ticker, hist, levels["support"], levels["resistance"], channel)
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

    _dedup_mark("alerted", ticker)

    try:
        supabase.table("signal_alerts").insert({
            "ticker": ticker,
            "entry_price": float(hist["Close"].iloc[-1]),
            "entry_low": levels["entry_low"],
            "entry_high": levels["entry_high"],
            "target": levels["resistance"],
            "stop_loss": levels["stop_loss"],
            "status": "waiting_entry",  # belum "open" beneran — nunggu harga kesentuh zona entry dulu
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
        if _dedup_seen("watchlist", ticker):
            continue
        if (row.get("technical_score") or 0) >= BREAKOUT_TECHNICAL_THRESHOLD:
            text = (
                f"⭐ <b>WATCHLIST — {_esc(ticker)}</b>\n\n"
                f"Breakout + volume kekonfirmasi (technical {row['technical_score']}/20)."
            )
            if send_alert(text):
                _dedup_mark("watchlist", ticker)


def _check_economic_reminders() -> None:
    """Gated `notif_economic_events` di Settings. Event High impact hari ini
    (dari Forex Factory, udah di-cache 5 menit di forex_factory.py), dedup
    di Supabase biar gak nge-spam tiap jam (atau tiap redeploy) buat event
    yang sama."""
    settings = _load_settings()
    if not settings["notif_economic_events"]:
        return

    today = today_wib().isoformat()
    try:
        events = [e for e in get_forex_events() if e["impact"] == "High" and e["date"] == today]
    except Exception:
        return
    new_events = [e for e in events if not _dedup_seen("econ", e["event"])]
    if not new_events:
        return

    lines = ["📅 <b>Event Ekonomi High Impact Hari Ini</b>\n"]
    for e in new_events:
        lines.append(f"{e['flag']} <b>{e['time_wib']} WIB</b> — {_esc(e['event'])} ({_esc(e['currency'])})")
    if send_alert("\n".join(lines)):
        for e in new_events:
            _dedup_mark("econ", e["event"])


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
        send_alert(f"🚨 <b>PORTFOLIO RISK: HIGH</b>\n\n{_esc(result.get('portfolio_impact_summary', '-'))}")


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

    lines = ["🌙 <b>Recap Malam Ini</b>\n"]
    if ihsg:
        arrow = "🟢" if ihsg["change_pct"] >= 0 else "🔴"
        lines.append(f"{arrow} IHSG: <b>{ihsg['price']:,.0f}</b> ({ihsg['change_pct']:+.2f}%)")
    lines.append(f"📈 Strong signal hari ini: <b>{strong_count}</b> ticker")
    if stats.get("win_rate_pct") is not None:
        lines.append(f"🎯 Win rate NEXUS: <b>{stats['win_rate_pct']}%</b> ({stats['tp_hit']} TP / {stats['sl_hit']} SL)")
    send_alert("\n".join(lines))


def _send_running_positions_update() -> None:
    """Digest posisi 'running' (signal_alerts status='open') — dikirim tiap
    malam bareng Recap Malam. Beda dari _check_signal_outcomes() (yang OTOMATIS
    nutup posisi kalau harga literal nyentuh TP/SL) — ini progress report buat
    yang MASIH open: masih layak dipegang (dari berita/konteks terbaru, bukan
    cuma angka harga doang), atau ada red flag baru yang lebih urgent dari
    stop-loss teknikal."""
    settings = _load_settings()
    if not settings["notif_strong_signal"]:
        return
    try:
        res = supabase.table("signal_alerts").select("*").eq("status", "open").execute()
    except Exception:
        return
    if not res.data:
        return

    news_by_ticker = _recent_news_by_ticker(days=2)
    positions = []
    for row in res.data:
        try:
            hist = _get_history(row["ticker"])
            price_now = float(hist["Close"].iloc[-1])
        except Exception:
            continue
        pnl_pct = round((price_now - row["entry_price"]) / row["entry_price"] * 100, 2)
        positions.append({
            "ticker": row["ticker"], "entry_price": row["entry_price"], "price_now": price_now,
            "pnl_pct": pnl_pct, "target": row["target"], "stop_loss": row["stop_loss"],
            "berita": news_by_ticker.get(row["ticker"]),
        })
    if not positions:
        return

    try:
        verdicts = {v["ticker"]: v for v in assess_running_positions(positions).get("verdicts", [])}
    except Exception:
        verdicts = {}

    lines = ["📋 <b>Update Posisi Running</b>\n"]
    for p in positions:
        v = verdicts.get(p["ticker"], {})
        verdict = v.get("verdict", "lanjut")
        alasan = _esc(v.get("alasan") or "-")
        sign = "+" if p["pnl_pct"] >= 0 else ""
        if verdict == "urgent_cl":
            lines.append(
                f"🚨 <b>{_esc(p['ticker'])}</b> ({sign}{p['pnl_pct']}%) — "
                f"<b>PERTIMBANGKAN CUT LOSS</b>\n{alasan}\n"
            )
        else:
            lines.append(
                f"🟢 <b>{_esc(p['ticker'])}</b> ({sign}{p['pnl_pct']}%) — "
                f"lanjut, target TP Rp{p['target']:,.0f}\n{alasan}\n"
            )
    send_alert("\n".join(lines))


async def run_scheduler() -> None:
    while True:
        check_and_alert()
        _check_watchlist_alerts()
        _check_economic_reminders()
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def run_night_recap() -> None:
    while True:
        now = _now_wib()
        target = now.replace(hour=NIGHT_RECAP_HOUR, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            _send_night_recap()
        except Exception:
            pass
        try:
            _send_running_positions_update()
        except Exception:
            pass


BSJP_SCREENER_HOUR = 15
BSJP_SCREENER_MINUTE = 30  # SEBELUM market tutup, bukan sesudah — BSJP beli-nya
                            # maksimal ~15:57, jadi alert-nya kudu ada buffer buat
                            # dibaca+eksekusi, bukan telat udah gak bisa beli


def _check_bsjp_screener() -> None:
    """BSJP itu screener checklist pass/fail (lihat scoring.py::bsjp_criteria),
    BUKAN 1 "pick terbaik" kayak Swing — jadi kirim SEMUA ticker yang lolos
    sekaligus, gak lewat Groq/pick_alert_candidate (gak butuh judgment call
    multi-faktor, syaratnya udah jelas AND semua)."""
    if _dedup_seen("bsjp", "screener"):
        return
    settings = _load_settings()
    if not settings["notif_strong_signal"]:
        return

    try:
        res = supabase.table("scanner_cache").select("ticker,price").eq("cocok_bsjp", True).execute()
    except Exception:
        return
    if not res.data:
        return

    lines = ["🌆 <b>BSJP — Screener Beli Sore Jual Pagi</b>\n", "Ticker yang lolos syarat hari ini:"]
    for row in res.data:
        lines.append(f"✅ <b>{_esc(row['ticker'])}</b> — Rp{row['price']:,.0f}")
    lines.append("\n📌 Syarat: breakout ≥5% + volume ≥2x rata-rata 20 hari + minat institusi.")
    lines.append("⏰ <b>Buruan, beli maksimal jam 15:57 buat kejar BSJP hari ini.</b>")

    if send_alert("\n".join(lines)):
        _dedup_mark("bsjp", "screener")


async def run_bsjp_screener() -> None:
    while True:
        now = _now_wib()
        target = now.replace(hour=BSJP_SCREENER_HOUR, minute=BSJP_SCREENER_MINUTE, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            _check_bsjp_screener()
        except Exception:
            pass


PRE_MARKET_HOUR = 8
PRE_MARKET_MINUTE = 45  # 15 menit sebelum market IDX buka jam 09:00

SENTIMENT_EMOJI = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪", "mixed": "🟡"}


def _send_morning_briefing() -> None:
    """"Sarapan pagi" — sintesis ulang daily_briefing (bukan cuma baca cache
    jam 6 pagi, biar nangkep berita yang masuk di antara jam 6-08:45) terus
    kirim ke Telegram: ringkasan + tanggal penting + rekomendasi (ini yang
    jadi "watchlist hari ini")."""
    try:
        briefing = _generate_briefing()
    except Exception:
        return

    sentiment = briefing.get("market_sentiment", "")
    lines = [f"☀️ <b>Sarapan Pagi</b> {SENTIMENT_EMOJI.get(sentiment, '')} {_esc(sentiment)}".strip()]
    lines.append(_esc(briefing.get("ringkasan", "-")))

    berita = briefing.get("berita") or {}
    BERITA_SECTIONS = [("positive", "🟢 Positive"), ("negative", "🔴 Negative"), ("netral", "⚪ Netral")]
    for key, label in BERITA_SECTIONS:
        items = berita.get(key) or []
        if not items:
            continue
        lines.append(f"\n<b>{label}</b>")
        for it in items:
            lines.append(f"• <b>{_esc(it.get('saham', '-'))}</b>: {_esc(it.get('berita', '-'))}")

    tanggal_penting = briefing.get("tanggal_penting") or []
    if tanggal_penting:
        lines.append("\n📅 <b>Tanggal Penting</b>")
        for e in tanggal_penting:
            lines.append(f"• {_esc(e.get('saham'))} — {_esc(e.get('jenis'))} · {_esc(e.get('tanggal'))}")

    rekomendasi = briefing.get("rekomendasi") or []
    if rekomendasi:
        lines.append("\n⭐ <b>Watchlist Hari Ini</b>")
        for r in rekomendasi:
            lines.append(f"• <b>{_esc(r.get('saham'))}</b>: {_esc(r.get('alasan'))}")

    send_alert("\n".join(lines))


async def run_pre_market_briefing() -> None:
    while True:
        now = _now_wib()
        target = now.replace(hour=PRE_MARKET_HOUR, minute=PRE_MARKET_MINUTE, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            _send_morning_briefing()
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
        now = _now_wib()
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
            _check_entry_zone_touches()
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
