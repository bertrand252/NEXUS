"""
Scheduler background sederhana: tiap CHECK_INTERVAL_SECONDS, cek scanner_cache
buat ticker Strong dengan score tertinggi (cuma #1, bukan semua), rangkum
alasan lewat Groq, render chart + garis support/resistance, kirim ke Telegram
sebagai foto. Jalan di process yang sama kayak FastAPI lewat asyncio.create_task
— gak butuh cron/Celery/proses terpisah, paling sederhana buat single-user tool.
"""
import asyncio
from datetime import date, datetime, time, timedelta, timezone
from html import escape as _esc
import yfinance as yf
from config import supabase, WIB, today_wib
from routers.scanner import _get_history, _get_history_intraday
from routers.mentor_calls import refresh_mentor_calls
from routers.daily_briefing import _generate_briefing
from levels import support_resistance, detect_trend_channel, find_smart_tp, rr_label, determine_trend
from chart_render import render_chart
from scoring import bsjp_intraday_score, bpjs_momentum_score
from intraday import daily_session_stats, session_takeoff
from groq_client import analyze_alert, pick_alert_candidate, pick_bpjs_candidate, assess_running_positions
from forex_factory import get_forex_events
from telegram_bot import send_alert_photo, send_alert, get_channel_updates, delete_message
from telegram_scrape import fetch_channel_posts
from routers.intel import submit_intel, IntelInput
from routers.settings import DEFAULTS as SETTINGS_DEFAULTS
from market_calendar import is_trading_day, upcoming_holidays
from config import TELEGRAM_CHANNEL_IDS, TELEGRAM_SCRAPE_CHANNELS
from logger import get_logger

log = get_logger("scheduler")

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
            try:
                sig_res = (
                    supabase.table("signal_alerts").select("telegram_message_id")
                    .eq("ticker", row["ticker"]).in_("status", ["waiting_entry", "open"])
                    .order("alerted_at", desc=True).limit(1).execute()
                )
                if sig_res.data and sig_res.data[0].get("telegram_message_id"):
                    delete_message(sig_res.data[0]["telegram_message_id"])  # unsend — udah gak valid lagi
            except Exception:
                pass
            send_alert(
                f"⚪ <b>Update — {_esc(row['ticker'])}</b>\n\n"
                f"Udah gak Strong lagi (sekarang {_esc(row['signal'])}, score {row['total_score']}/100)."
            )
            _dedup_mark("invalidated", row["ticker"])


SOURCE_LABEL = {
    "gap": "gap belum keisi", "fibonacci": "Fibonacci retracement",
    "fibonacci_extension": "Fibonacci extension", "swing": "swing high/low",
}


TREND_LABEL = {"bullish": "🟢 Bullish", "bearish": "🔴 Bearish", "sideways": "⚪ Sideways"}


def _source_note(sources: list[str] | None, timeframes: list[str] | None = None, role_reversal: bool = False) -> str:
    if not sources:
        return ""
    labels = ", ".join(SOURCE_LABEL.get(s, s) for s in sources)
    tf_note = f", konfirmasi {'+'.join(timeframes)}" if timeframes and len(timeframes) > 1 else ""
    rr_note = ", pernah jadi S+R (role reversal)" if role_reversal else ""
    return f" [{labels}{tf_note}{rr_note}]"


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
    tp1_note = _source_note(levels.get("tp1_sources"), levels.get("tp1_timeframes"), levels.get("tp1_role_reversal", False))
    sl_note = _source_note(levels.get("sl_sources"), role_reversal=levels.get("sl_role_reversal", False))
    tp2_line = ""
    if levels.get("tp2"):
        tp2_note = _source_note(levels.get("tp2_sources"))
        tp2_line = (
            f"🎯 <b>TARGET 2 (TP2)</b> Rp{levels['tp2']:,.0f} (+{levels.get('reward_pct_tp2', 0)}%) "
            f"· RR 1:{levels.get('rr_ratio_tp2', 0)}{tp2_note}\n"
        )
    trend = TREND_LABEL.get(levels.get("trend"), "")
    trend_line = f"📈 <b>Trend</b> (weekly): {trend}\n\n" if trend else "\n"
    return (
        f"🔥 <b>SWING SIGNAL — {_esc(ticker)}</b>\n"
        f"Score {total_score}/100 · 🎯 Gaya: Swing\n"
        f"{trend_line}"
        f"✅ <b>BUY</b> Rp{levels['entry_low']:,.0f} – Rp{levels['entry_high']:,.0f}\n"
        f"🎯 <b>TARGET 1 (TP1)</b> Rp{levels['resistance']:,.0f} (+{levels['reward_pct']}%){_esc(tp1_note)}\n"
        f"{tp2_line}"
        f"⛔ <b>STOP LOSS (CL)</b> Rp{levels['stop_loss']:,.0f} (-{levels['risk_pct']}%){_esc(sl_note)}\n"
        f"⚖️ <b>Risk:Reward (TP1)</b> 1:{levels['rr_ratio']} — {levels['rr_label']}\n\n"
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

# Sanity bound TERAKHIR buat risk/reward — kejadian nyata (PACK): TP2 +275%,
# SL -58.88% kekirim ke Telegram, user komplain "gamasuk akal". Root cause:
# _apply_smart_tp cuma nge-cap SL versi smart_tp (15%, lihat komentar MPIX di
# bawah), TAPI fallback ke support_resistance() 20-hari BIASA gak ada cap sama
# sekali — kalau saham lagi volatile/tipis, 20-day low/Fibonacci extension bisa
# jauh BANGET dari harga sekarang, jauh di luar rentang wajar Swing beneran
# (user: TP wajar 10-50%, SL wajar 10-20%). Dicek di SATU tempat (gate RR),
# bukan di tiap sumber TP/SL — biar kepenuhi gak peduli levels-nya dari smart_tp
# atau fallback 20-hari.
MAX_RISK_PCT = 20
MAX_REWARD_PCT = 50


_levels_cache: dict[str, dict] = {}  # {ticker: levels lengkap} dari _gather_candidates, dibaca check_and_alert()


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

    # syarat RR minimal — DIHITUNG PAKE ANALISA LENGKAP (multi-timeframe: gap+
    # fibonacci+swing, daily/weekly/monthly), BUKAN cuma window 20 hari kasar
    # kayak sebelumnya. User eksplisit minta ini biar gak ada mismatch antara
    # yang lolos filter vs yang keliatan di caption final (kejadian nyata:
    # MPIX lolos gate RR 20-hari, tapi RR versi lengkapnya "Buruk"). Konsekuensi
    # sadar: 2 yfinance call ekstra (weekly+monthly) PER kandidat di pool
    # (~20 ticker) — lebih lambat & lebih rawan rate-limit Yahoo, tapi user
    # rencana pindah ke Invezgo dalam waktu dekat jadi ini gak jadi masalah
    # jangka panjang. Levels yang udah dihitung lengkap disimpen di
    # _levels_cache (BUKAN di dalem candidate dict — candidates itu dikirim
    # mentah ke json.dumps() di pick_alert_candidate, levels yang gede bakal
    # ikut bengkakin token prompt Groq kalau nempel di situ) biar
    # check_and_alert() gak perlu hitung ulang buat pemenang.
    _levels_cache.clear()
    filtered = []
    for c in candidates:
        try:
            hist = _get_history(c["ticker"])
            lv = support_resistance(hist)
            _apply_smart_tp(lv, c["ticker"], hist)
        except Exception:
            continue  # gagal fetch, skip — jangan asumsiin RR-nya oke
        if lv["rr_ratio"] < MIN_RR_RATIO:
            continue
        if lv["risk_pct"] > MAX_RISK_PCT or lv["reward_pct"] > MAX_REWARD_PCT:
            continue  # SL/TP kejauhan dari harga sekarang buat swing beneran (lihat komentar MAX_RISK_PCT) — skip, jangan kirim angka ngaco
        c["rr_ratio"] = lv["rr_ratio"]
        _levels_cache[c["ticker"]] = lv
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
            if row.get("telegram_message_id"):
                delete_message(row["telegram_message_id"])  # unsend — udah gak relevan, kepentok harga lari duluan
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

        if row.get("telegram_message_id"):
            delete_message(row["telegram_message_id"])  # unsend alert asli — posisi udah ditutup, biar chat gak numpuk

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


def _apply_smart_tp(levels: dict, ticker: str, hist_daily) -> None:
    """Override resistance/stop_loss di `levels` (dari support_resistance(),
    20 hari doang) pake TP1/TP2/SL multi-timeframe (find_smart_tp — gap+
    fibonacci+swing, daily+weekly+monthly). Modif `levels` in-place, fallback
    diem-diem ke nilai 20-hari lama kalau fetch weekly/monthly gagal — jangan
    gagalin alert-nya cuma gara-gara timeframe tambahan gak kebuka."""
    price_now = float(hist_daily["Close"].iloc[-1])
    try:
        # weekly & monthly "max" — dari AWAL saham listing, bukan cuma
        # beberapa tahun (user eksplisit minta ini, bukan 20 hari/2 bulan doang)
        weekly = yf.Ticker(f"{ticker}.JK").history(period="max", interval="1wk").dropna(subset=["Close"])
        monthly = yf.Ticker(f"{ticker}.JK").history(period="max", interval="1mo").dropna(subset=["Close"])
        smart = find_smart_tp({"daily": hist_daily, "weekly": weekly, "monthly": monthly}, price_now)
        levels["trend"] = determine_trend(weekly if len(weekly) >= 50 else hist_daily)
    except Exception:
        return  # gagal fetch timeframe tambahan — biarin levels tetep pake fallback 20-hari

    tp1, tp2, sl_anchor = smart["tp1"], smart["tp2"], smart["sl_anchor"]
    if tp1:
        levels["resistance"] = tp1["price"]
        levels["tp1_sources"] = tp1["sources"]
        levels["tp1_timeframes"] = tp1["timeframes"]
        levels["tp1_role_reversal"] = tp1.get("role_reversal", False)
    if tp2:
        levels["tp2"] = tp2["price"]
        levels["tp2_sources"] = tp2["sources"]

    # SL cuma dipake kalau jaraknya MASUK AKAL (<=15% dari harga sekarang) —
    # multi-timeframe (apalagi monthly) bisa nemu support "struktural" yang
    # jauh banget (kejadian beneran: MPIX ketemu support Rp50 dari histori
    # 1 tahun padahal harga Rp118, itu bukan stop-loss, itu psikologi
    # investor jangka panjang). Kalau kejauhan, tetep pake fallback 20-hari
    # dari support_resistance() yang emang didesain buat SL ketat.
    if sl_anchor and (price_now - sl_anchor["price"]) / price_now <= 0.15:
        levels["stop_loss"] = round(sl_anchor["price"] * 0.98, 2)  # 2% buffer di bawah level
        levels["sl_sources"] = sl_anchor["sources"]
        levels["sl_role_reversal"] = sl_anchor.get("role_reversal", False)

    # recompute risk/reward/RR pake angka yang baru (bisa berubah dari yang
    # dihasilin support_resistance() tadi)
    risk_pct = round((price_now - levels["stop_loss"]) / price_now * 100, 2)
    reward_pct = round((levels["resistance"] - price_now) / price_now * 100, 2)
    rr_ratio = round(reward_pct / risk_pct, 2) if risk_pct > 0 else 0.0
    levels["risk_pct"], levels["reward_pct"], levels["rr_ratio"] = risk_pct, reward_pct, rr_ratio
    levels["rr_label"] = rr_label(rr_ratio)
    if tp2:
        reward_pct_tp2 = round((tp2["price"] - price_now) / price_now * 100, 2)
        levels["reward_pct_tp2"] = reward_pct_tp2
        levels["rr_ratio_tp2"] = round(reward_pct_tp2 / risk_pct, 2) if risk_pct > 0 else 0.0


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
        pick = pick_alert_candidate(candidates, macro_events, upcoming_holidays(within_days=3))
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
        # levels udah lengkap dihitung pas _gather_candidates (RR gate sekarang
        # pake analisa lengkap juga) — reuse dari cache, jangan hitung ulang 2x
        levels = _levels_cache.get(ticker)
        if levels is None:
            levels = support_resistance(hist)
            _apply_smart_tp(levels, ticker, hist)
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
    message_id = send_alert_photo(chart_png, caption)
    if not message_id:
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
            "telegram_message_id": message_id,  # dipake buat unsend pas posisi ditutup
        }).execute()
    except Exception:
        pass  # tabel belum di-setup / gagal simpen — jangan gagalin alert-nya cuma gara-gara ini


def _check_watchlist_alerts() -> None:
    """Ticker di watchlist user yang breakout+volume kekonfirmasi (technical_score
    >= threshold) dapet notif ringan sendiri — independen dari check_and_alert()
    (yang cuma milih 1 "pick terbaik" se-market), ini soal relevansi personal:
    ticker yang user pantau sendiri.

    Dedup pake `watchlist.last_alerted_score` (BUKAN _dedup_seen/"today" yang
    reset tiap tengah malam WIB) — soalnya scanner_cache cuma ke-refresh MANUAL,
    gak otomatis tiap jam. Kejadian nyata: check jam 22:54 & 00:09 (abis lewat
    tengah malam WIB) technical_score PERSIS SAMA (scanner_cache belum di-
    refresh sama sekali di antara 2 waktu itu), tapi dedup harian reset duluan
    -> alert IDENTIK kekirim 2x. Sekarang cuma alert kalau score BENERAN beda
    dari terakhir kali dialert, gak peduli lewat tengah malam berapa kali."""
    try:
        watch_res = supabase.table("watchlist").select("ticker,last_alerted_score").execute()
        watch_by_ticker = {r["ticker"]: r for r in watch_res.data}
    except Exception:
        return
    if not watch_by_ticker:
        return

    try:
        scan_res = (
            supabase.table("scanner_cache")
            .select("ticker,technical_score")
            .in_("ticker", list(watch_by_ticker))
            .execute()
        )
    except Exception:
        return

    for row in scan_res.data:
        ticker = row["ticker"]
        score = row.get("technical_score") or 0
        if score < BREAKOUT_TECHNICAL_THRESHOLD:
            continue
        if watch_by_ticker[ticker].get("last_alerted_score") == score:
            continue  # sinyal SAMA PERSIS kayak alert terakhir, scanner_cache belum berubah
        text = (
            f"⭐ <b>WATCHLIST — {_esc(ticker)}</b>\n\n"
            f"Breakout + volume kekonfirmasi (technical {score}/20)."
        )
        if send_alert(text):
            try:
                supabase.table("watchlist").update({"last_alerted_score": score}).eq("ticker", ticker).execute()
            except Exception:
                pass


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
    jumlah Strong signal hari ini, win rate NEXUS kalau datanya udah ada.
    Skip diem-diem kalau hari ini bursa tutup (weekend/libur) — gak ada
    yang perlu direkap, spam doang kalau tetep dikirim. Swing (check_and_alert)
    TETEP jalan kayak biasa, ini cuma soal notif rutin doang."""
    if not is_trading_day(today_wib()):
        return
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
        # tiap check DIBUNGKUS SENDIRI-SENDIRI (sebelumnya 3 dari 4 gak
        # ke-bungkus sama sekali — kalau salah satu raise exception gak
        # terduga, SELURUH loop ini mati diem-diem, gak ada lagi Swing/
        # watchlist/econ/BPJS check sampe Railway di-restart manual)
        for check in (check_and_alert, _check_watchlist_alerts, _check_economic_reminders, _check_bpjs):
            try:
                check()
            except Exception:
                log.exception(f"{check.__name__} gagal")
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
            log.exception("_send_night_recap gagal")
        try:
            _send_running_positions_update()
        except Exception:
            log.exception("_send_running_positions_update gagal")


BSJP_SCREENER_HOUR = 15
BSJP_SCREENER_MINUTE = 30  # SEBELUM market tutup, bukan sesudah — BSJP beli-nya
                            # maksimal ~15:57, jadi alert-nya kudu ada buffer buat
                            # dibaca+eksekusi, bukan telat udah gak bisa beli


MAX_BSJP_PER_DAY = 2  # user eksplisit minta dibatesin — jangan kirim SEMUA yang lolos syarat,
                        # cuma yang PALING kuat, sama semangatnya kayak cap Swing 1-2/minggu

BSJP_POOL_LIMIT = 20  # pool kandidat Stage-1 (proxy EOD murah), BUKAN final list —
                        # Stage-2 intraday di bawah yang mutusin siapa beneran "terbang" sesi 2


def _check_bsjp_screener() -> None:
    """2 tahap, sama pola _gather_candidates (screen murah ke semua 951 ->
    hitung berat cuma ke pool kecil): Stage-1 `scoring.py::bsjp_criteria`
    (proxy EOD, udah jalan di scanner_cache.cocok_bsjp) nyaring pool kandidat
    murah dari 951 ticker. Stage-2 di sini beneran ngukur intraday sesi 1 vs
    sesi 2 (teknik asli mentor: sahamnya "terbang" di sesi 2, sesi 1 spike
    cuma pendukung) via intraday.py + scoring.py::bsjp_intraday_score — cuma
    jalan ke pool kecil (BSJP_POOL_LIMIT), sequential (bukan ThreadPoolExecutor,
    sekelas jumlah ticker sama _gather_candidates).

    User eksplisit gak ada indikator resmi baku dari mentor buat BSJP — kalau
    Stage-2 gak nemu yang beneran skor >0 (sesi 2 gak "terbang"), JANGAN
    kirim apa-apa. Lebih baik diam daripada maksain alert dari proxy EOD
    doang yang belum tentu bener."""
    if _dedup_seen("bsjp", "screener"):
        return
    settings = _load_settings()
    if not settings["notif_strong_signal"]:
        return

    try:
        pool_res = (
            supabase.table("scanner_cache").select("ticker,price,volume_ratio")
            .eq("cocok_bsjp", True).order("volume_ratio", desc=True).limit(BSJP_POOL_LIMIT)
            .execute()
        )
    except Exception:
        return
    if not pool_res.data:
        return

    scored = []
    for row in pool_res.data:
        ticker = row["ticker"]
        try:
            hist_15m = _get_history_intraday(ticker)
            days = daily_session_stats(hist_15m)
            takeoff = session_takeoff(days, session="s2")
        except Exception:
            continue
        if takeoff is None:
            continue
        value_traded_idr = row["price"] * days[-1]["s2_volume"]
        score = bsjp_intraday_score(takeoff, value_traded_idr)
        if score > 0:
            scored.append({"ticker": ticker, "price": row["price"], "takeoff": takeoff, "score": score})

    if not scored:
        return  # sesi 2 gak ada yang "terbang" beneran — diam, jangan maksain

    scored.sort(key=lambda c: c["score"], reverse=True)
    scored = scored[:MAX_BSJP_PER_DAY]

    lines = ["🌆 <b>BSJP — Beli Sore Jual Pagi</b>\n", "Terkonfirmasi \"terbang\" di sesi 2 hari ini:"]
    for c in scored:
        t = c["takeoff"]
        support_note = " (+ sesi 1 juga spike, pendukung)" if t.get("s1_spike_supporting") else ""
        lines.append(
            f"✅ <b>{_esc(c['ticker'])}</b> — Rp{c['price']:,.0f} "
            f"(sesi 2: volume {t['volume_ratio']}x rata-rata, harga +{t['price_change_pct']}%){support_note}"
        )
    lines.append("\n📌 Sinyal relatif dari data intraday hari ini, bukan indikator resmi mentor.")
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
            log.exception("_check_bsjp_screener gagal")


MARKET_OPEN = time(9, 0)
MARKET_CLOSE = time(15, 50)

BPJS_POOL_LIMIT = 15  # lebih kecil dari pool Swing (20) — dipanggil berkali-kali/hari
                        # (tiap jam pas market buka), bukan 1x/hari kayak Swing/BSJP
MAX_BPJS_PER_DAY = 1  # 1 pick terbaik/hari, gaya judgment-call kayak Swing (bukan checklist kayak BSJP)


def _in_market_hours() -> bool:
    """09:00-15:50 WIB + hari trading — beda dari Swing (_in_offhours_window,
    JUSTRU di luar jam market) & BSJP (1x jam 15:30 doang). User eksplisit:
    BPJS "jam open market sampe close, sebelum bsjp"."""
    now = _now_wib()
    return is_trading_day(now.date()) and MARKET_OPEN <= now.time() < MARKET_CLOSE


def _gather_bpjs_candidates(pool_limit: int = BPJS_POOL_LIMIT) -> list[dict]:
    """Stage-1 murah: scanner_cache yang ada aktivitas hari ini (volume_ratio
    >=1.3, longgar — ponytail heuristic cuma nyaring saham 'lagi hidup') ATAU
    ada call mentor aktif, urut volume_ratio desc. Stage-2: sequential loop
    (sekelas jumlah ticker _gather_candidates) ambil intraday, ukur sesi yang
    LAGI JALAN sekarang (kasar: jam >=13:00 WIB anggap sesi 2, selain itu
    sesi 1 — lunch break self-correct lewat MIN_SESSION_BARS di
    intraday.py::session_takeoff, bar belum cukup ya None)."""
    mentor_by_ticker = _active_mentor_calls()
    news_by_ticker = _recent_news_by_ticker()

    try:
        scan_res = (
            supabase.table("scanner_cache").select("ticker,price,volume_ratio")
            .gte("volume_ratio", 1.3).order("volume_ratio", desc=True).limit(pool_limit)
            .execute()
        )
        scan_by_ticker = {r["ticker"]: r for r in scan_res.data}
    except Exception:
        scan_by_ticker = {}

    pool = set(scan_by_ticker) | set(mentor_by_ticker)
    session = "s2" if _now_wib().time() >= time(13, 0) else "s1"

    candidates = []
    for ticker in pool:
        scan = scan_by_ticker.get(ticker)
        mentor = mentor_by_ticker.get(ticker)
        momentum_score = 0.0
        try:
            price = scan["price"] if scan else _get_history(ticker)["Close"].iloc[-1]
            hist_15m = _get_history_intraday(ticker)
            days = daily_session_stats(hist_15m)
            takeoff = session_takeoff(days, session=session)
            if takeoff is not None:
                value_traded_idr = price * days[-1][f"{session}_volume"]
                momentum_score = bpjs_momentum_score(takeoff, value_traded_idr)
        except Exception:
            pass
        if momentum_score <= 0 and not mentor:
            continue
        candidates.append({
            "ticker": ticker,
            "momentum_score": momentum_score,
            "session": session,
            "mentor_call": ({"status": mentor["status"], "buy_price": mentor["buy_price"]} if mentor else None),
            "berita": news_by_ticker.get(ticker),
        })

    candidates.sort(key=lambda c: c["momentum_score"], reverse=True)
    return candidates


def _build_bpjs_caption(ticker: str, candidate: dict, pick: dict, levels: dict) -> str:
    """Vibe lebih ringkas dari Swing (_build_caption) — BPJS gak punya sourcing
    TP1/TP2 multi-timeframe (horizonnya cuma 1-2 hari, `find_smart_tp` didesain
    buat horizon Swing mingguan-bulanan, dipaksain ke sini jadi confluence yang
    gak relevan). Entry/TP/SL dari support_resistance() 20-hari biasa, cukup
    buat ke-track menang/kalah di signal_alerts."""
    faktor = pick.get("faktor_pendukung") or []
    faktor_line = f"📌 <b>Faktor pendukung:</b> {_esc('; '.join(faktor))}\n\n" if faktor else ""
    session_label = "Sesi 2 (siang-sore)" if candidate["session"] == "s2" else "Sesi 1 (pagi)"
    mentor_line = "\n👤 Ada call aktif mentor trading." if candidate.get("mentor_call") else ""
    return (
        f"⚡ <b>BPJS — Day Trade — {_esc(ticker)}</b>\n\n"
        f"Momentum terdeteksi di {session_label}, skor relatif {candidate['momentum_score']}x rata-rata sesi.{mentor_line}\n\n"
        f"✅ <b>BUY</b> Rp{levels['entry_low']:,.0f}-Rp{levels['entry_high']:,.0f}\n"
        f"🎯 <b>TARGET</b> Rp{levels['resistance']:,.0f} (+{levels['reward_pct']}%)\n"
        f"⛔ <b>STOP LOSS</b> Rp{levels['stop_loss']:,.0f} (-{levels['risk_pct']}%)\n"
        f"⚖️ Risk:Reward — {levels['rr_label']}\n\n"
        f"{faktor_line}"
        f"📊 {_esc(pick.get('alasan_singkat', ''))}\n\n"
        f"⚠️ Judgment call gabungan teknikal+berita, BUKAN indikator resmi mentor. "
        f"Harapan lanjut naik 1-2 hari ke depan, bukan jaminan."
    )


def _check_bpjs() -> None:
    """Gate berurutan: jam market -> dedup 1x/hari -> toggle notif (reuse
    Swing) -> pool kandidat -> Groq pilih -> GUARD PYTHON (bukan cuma prompt,
    pola sama Keputusan Desain #7 — Swing punya breakout_confirmed check,
    ini versinya BPJS) -> entry/TP/SL simpel -> kirim -> catat."""
    if not _in_market_hours():
        return
    if _dedup_seen("bpjs", "picked"):
        return
    settings = _load_settings()
    if not settings["notif_strong_signal"]:
        return

    candidates = _gather_bpjs_candidates()
    if not candidates:
        return

    try:
        pick = pick_bpjs_candidate(candidates)
    except Exception:
        return

    ticker = pick.get("pilih")
    if not ticker:
        return

    candidate = next((c for c in candidates if c["ticker"] == ticker), None)
    if candidate is None or (candidate["momentum_score"] <= 0 and not candidate.get("mentor_call")):
        return  # jaga-jaga Groq halusinasi ticker di luar pool / ngelanggar instruksi sendiri

    try:
        hist = _get_history(ticker)
        levels = support_resistance(hist)
    except Exception:
        return
    if levels["risk_pct"] > MAX_RISK_PCT or levels["reward_pct"] > MAX_REWARD_PCT:
        return  # SL/TP kejauhan dari harga sekarang, sama sanity check kayak Swing — jangan kirim angka ngaco

    caption = _build_bpjs_caption(ticker, candidate, pick, levels)
    message_id = send_alert(caption)
    if not message_id:
        return

    _dedup_mark("bpjs", "picked")

    try:
        supabase.table("signal_alerts").insert({
            "ticker": ticker,
            "entry_price": float(hist["Close"].iloc[-1]),
            "entry_low": levels["entry_low"],
            "entry_high": levels["entry_high"],
            "target": levels["resistance"],
            "stop_loss": levels["stop_loss"],
            "status": "waiting_entry",
            "telegram_message_id": message_id,
            "source": "bpjs",
        }).execute()
    except Exception:
        pass


PRE_MARKET_HOUR = 8
PRE_MARKET_MINUTE = 45  # 15 menit sebelum market IDX buka jam 09:00

SENTIMENT_EMOJI = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪", "mixed": "🟡"}


def _send_morning_briefing() -> None:
    """"Sarapan pagi" — sintesis ulang daily_briefing (bukan cuma baca cache
    jam 6 pagi, biar nangkep berita yang masuk di antara jam 6-08:45) terus
    kirim ke Telegram: ringkasan + tanggal penting + rekomendasi (ini yang
    jadi "watchlist hari ini"). Skip diem-diem kalau hari ini bursa tutup —
    gak ada gunanya "watchlist hari ini" kalau market gak buka."""
    if not is_trading_day(today_wib()):
        return
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
            log.exception("_send_morning_briefing gagal")


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
            log.exception("get_channel_updates gagal")
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
                log.exception("submit_intel gagal (channel listener)")  # gagal simpen/ringkas 1 pesan, lanjut ke update berikutnya


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
                log.exception(f"fetch_channel_posts gagal (@{username})")
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
                    log.exception(f"submit_intel gagal (scrape @{username})")
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
        # tiap step DIBUNGKUS SENDIRI-SENDIRI (bukan 1 try/except ngelingkupin
        # semua) — biar 1 step gagal (misal refresh_mentor_calls network error)
        # gak nge-block step selanjutnya (misal _check_signal_outcomes yang
        # nutup posisi trading, itu lebih penting daripada mentor sheet)
        for step in (refresh_mentor_calls, _generate_briefing, _check_entry_zone_touches,
                     _check_signal_outcomes, _check_portfolio_risk):
            try:
                step()
            except Exception:
                log.exception(f"{step.__name__} gagal (morning routine)")
