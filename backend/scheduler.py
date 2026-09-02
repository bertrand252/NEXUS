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
from routers.scanner import _get_history, _get_history_intraday, refresh_scanner_data, refresh_fundamentals_data
from routers.mentor_calls import refresh_mentor_calls
from routers.daily_briefing import _generate_briefing
from levels import support_resistance, detect_trend_channel, find_smart_tp, rr_label, determine_trend, well_defended_support, detect_chart_pattern, apply_buy_on_weakness_support
from chart_render import render_chart
from scoring import bsjp_intraday_score, bpjs_momentum_score, volume_dry_up, is_market_uptrend, ma_alignment, adx, bollinger_signal
from intraday import daily_session_stats, session_takeoff
from groq_client import analyze_alert, pick_alert_candidate, pick_bpjs_candidate, assess_running_positions, generate_postmortem, evaluate_portfolio_rotation, ask_hold_or_exit
from forex_factory import get_forex_events
from telegram_bot import (
    send_alert_photo, send_alert, get_channel_updates, delete_message,
    send_alert_with_buttons, answer_callback_query, edit_message_text,
)
from telegram_scrape import fetch_channel_posts
from routers.intel import submit_intel, IntelInput
from routers.settings import DEFAULTS as SETTINGS_DEFAULTS
from market_calendar import is_trading_day, upcoming_holidays
from config import TELEGRAM_CHANNEL_IDS, TELEGRAM_SCRAPE_CHANNELS
from logger import get_logger
import invezgo_client
from invezgo_client import trim_financial_statement

log = get_logger("scheduler")

TELEGRAM_SCRAPE_INTERVAL_SECONDS = 10 * 60  # preview publik gak realtime kayak bot API, polling 10 menit cukup

_last_seen_post_id: dict[str, int] = {}

MORNING_ROUTINE_HOUR = 6  # 06:00 waktu lokal server, sebelum market IDX buka jam 09:00

CHECK_INTERVAL_SECONDS = 60 * 60  # 1 jam — dedup per-ticker jadi gak ngaruh ke spam, cuma ke seberapa cepet nyampe

ENTRY_ZONE_WATCH_INTERVAL_SECONDS = 15 * 60  # user eksplisit minta — jangan nunggu run_morning_routine
                                               # besok pagi buat notif ENTRY ZONE, pool-nya kecil (cuma
                                               # yang status='waiting_entry', max ~5-10 row) jadi murah

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


def _next_target(now: datetime, hour: int, minute: int, weekday: int | None) -> datetime:
    """weekday=None -> next occurrence hari ini/besok jam hour:minute.
    weekday=0-6 (Senin=0) -> next occurrence hari itu jam hour:minute."""
    if weekday is None:
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        return target
    days_until = (weekday - now.weekday()) % 7
    target = (now + timedelta(days=days_until)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=7)
    return target


def _is_due_now(now: datetime, hour: int, minute: int, weekday: int | None) -> bool:
    """True kalau target hour:minute (hari ini, atau hari `weekday` KALAU
    hari ini emang hari itu) udah lewat/pas SEKARANG — dipake buat catch-up
    pas restart abis target lewat (lihat _run_scheduled)."""
    return (weekday is None or now.weekday() == weekday) and \
        now >= now.replace(hour=hour, minute=minute, second=0, microsecond=0)


async def _run_scheduled(hour: int, minute: int, category: str, func, weekday: int | None = None) -> None:
    """Loop harian (weekday=None) atau mingguan (weekday=0-6) jam hour:minute
    WIB — BEDA dari pola while-True-sleep-until-target polos yang dipake
    tiap loop terjadwal sebelum ini. Bug NYATA ketemu 2026-09-02 (user lapor
    Sarapan Pagi gak kekirim, generate_briefing()-nya sendiri gak error kalau
    dites manual): Railway REDEPLOY (sesi ngoding aktif bisa push berkali-
    kali sehari) reset SEMUA state in-memory TERMASUK posisi loop ini. Pola
    lama, kalau restart kejadian PAS ABIS target hari ini/minggu ini lewat
    (`now >= target` -> langsung `target += 1 hari/minggu`), hari ini
    DISKIP DIEM-DIEM, gak ada log/error yang nunjukin. Fix: dedup ke
    `alert_dedup` (Supabase, SELAMAT dari redeploy, pola sama kayak
    _dedup_seen dipake alert biasa) buat tau APAKAH target udah kejalanin
    hari ini — kalau target udah lewat TAPI belum ke-dedup, jalanin SEKARANG
    (catch-up), bukan nunggu siklus berikutnya."""
    while True:
        now = _now_wib()
        # BUG: _next_target() SELALU balikin target > now (constructionnya
        # gitu), jadi `if now < target` di bawah ini kalau dites lawan hasil
        # _next_target bakal SELALU true — dedup catch-up di bawah gak
        # PERNAH kesampean pas restart abis target hari/minggu ini lewat,
        # persis skenario yang mau difix (loop langsung lompat ke target
        # BERIKUTNYA lewat _next_target, gak pernah ngecek "target hari ini
        # udah lewat tapi belum jalan"). Fix: cek due_today LANGSUNG dari jam
        # sekarang (bukan dari _next_target yang udah kadung ngelompatin),
        # baru sleep kalau emang belum due.
        if not _is_due_now(now, hour, minute, weekday):
            target = _next_target(now, hour, minute, weekday)
            await asyncio.sleep((target - now).total_seconds())
            now = _now_wib()
        if not _dedup_seen(category, "done"):
            try:
                func()
            except Exception:
                log.exception(f"{category} gagal")
            _dedup_mark(category, "done")
        next_target = _next_target(now + timedelta(seconds=1), hour, minute, weekday)
        await asyncio.sleep(max((next_target - _now_wib()).total_seconds(), 1))


def _check_invalidated() -> None:
    """Ticker yang lagi ada posisi Swing AKTIF (waiting_entry/open di
    signal_alerts — BUKAN cuma yang di-alert HARI INI, posisi bisa kepegang
    sampe SIGNAL_TIMEOUT_DAYS hari), cek ulang statusnya — kalau udah gak
    Strong lagi, kirim 1 notif teks (bukan foto), sekali aja per ticker per
    hari. Dulu pake _dedup_seen_keys("alerted") buat nentuin "pending" —
    BUG: dedup itu di-scope per HARI (dedup_date=hari ini), jadi abis hari
    alert-nya lewat, ticker itu gak pernah dicek ulang lagi seumur posisinya
    (posisi Swing bisa idle sampe 14 hari tanpa peringatan sinyal udah
    melemah). Fix: query langsung ke signal_alerts, sumber kebenaran posisi
    aktif yang sebenernya."""
    try:
        pos_res = (
            supabase.table("signal_alerts").select("ticker")
            .eq("source", "swing").in_("status", ["waiting_entry", "open"])
            .execute()
        )
        pending = {r["ticker"] for r in pos_res.data} - _dedup_seen_keys("invalidated")
    except Exception:
        return
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


def _build_caption(ticker: str, total_score: int, levels: dict, reasoning: dict, faktor_pendukung: list[str], bandar: dict | None = None) -> str:
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
    bandar_line = f"{_format_bandar_line(bandar)}\n" if bandar else ""
    return (
        f"🔥 <b>SWING SIGNAL — {_esc(ticker)}</b>\n"
        f"Score {total_score}/100 · 🎯 Gaya: Swing\n"
        f"{trend_line}"
        f"✅ <b>BUY</b> Rp{levels['entry_low']:,.0f} – Rp{levels['entry_high']:,.0f}\n"
        f"🎯 <b>TARGET 1 (TP1)</b> Rp{levels['resistance']:,.0f} (+{levels['reward_pct']}%){_esc(tp1_note)}\n"
        f"{tp2_line}"
        f"⛔ <b>STOP LOSS (CL)</b> Rp{levels['stop_loss']:,.0f} (-{levels['risk_pct']}%){_esc(sl_note)}\n"
        f"⚖️ <b>Risk:Reward (TP1)</b> 1:{levels['rr_ratio']} — {levels['rr_label']}\n\n"
        f"{bandar_line}"
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


def _recent_trade_calls_by_ticker(days: int = 3) -> dict[str, list[dict]]:
    """{ticker: [{tanggal, entry, target, stop_loss, alasan}]} — call harga
    spesifik (entry/target/stop-loss) dari channel sekuritas yang dipantau,
    DIPISAH dari poin_penting biar gak nyasar ke Daily Briefing (user: jangan
    taro call saham apapun di kolom berita — bukan tugas Groq nyaranin beli/
    jual dari teks berita, lihat intel.py::SUMMARIZE_SYSTEM_PROMPT). Dipake
    buat BPJS doang (bukan Swing) — konsiderasi tambahan, bukan syarat wajib."""
    since = (today_wib() - timedelta(days=days)).isoformat()
    try:
        res = supabase.table("daily_market_intel").select("tanggal,summary_ai").gte("tanggal", since).execute()
    except Exception:
        return {}

    by_ticker: dict[str, list[dict]] = {}
    for row in res.data:
        summary = row.get("summary_ai") or {}
        for call in summary.get("trade_calls", []):
            t = (call.get("saham") or "").upper()
            if not t:
                continue
            by_ticker.setdefault(t, []).append({
                "tanggal": row["tanggal"], "entry": call.get("entry"),
                "target": call.get("target"), "stop_loss": call.get("stop_loss"),
                "alasan": call.get("alasan"),
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


def _pct_change_nday(hist, n: int) -> float | None:
    """% perubahan Close dari N hari trading lalu ke sekarang. None kalau histori
    kurang dari N+1 baris (ticker baru IPO / data gak lengkap)."""
    if len(hist) < n + 1:
        return None
    old, new = float(hist["Close"].iloc[-(n + 1)]), float(hist["Close"].iloc[-1])
    return (new - old) / old * 100


BREAKOUT_TECHNICAL_THRESHOLD = 12  # technical_score minimal (dari 20) buat dianggap "breakout+volume kekonfirmasi"
MIN_RR_RATIO = 1.5  # riset: 1:1.5-1:2 standar minimum umum, Swing spesifik idealnya 1:3+ — mulai
                     # dari 1.5 (gak terlalu ketat dulu), bisa dinaikin kalau kandidat kebanyakan lolos

MIN_CONVICTION_SWING = 4  # 1-5 dari Groq sendiri (pick_alert_candidate) — dipegang berminggu-minggu,
                           # gate lebih ketat dari BPJS. Mulai dari 4, turunin ke 3 kalau kebanyakan skip.
MIN_CONVICTION_BPJS = 3  # day-trade, gate lebih longgar sesuai sifatnya (lihat docstring pick_bpjs_candidate)

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
_invezgo_enrich_cache: dict[str, dict] = {}  # {ticker: {"date": str, "fields": dict}} — 1x fetch/ticker/hari, lihat komentar di _gather_candidates


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

    # "buy on weakness" candidates BUKAN diseleksi dari total_score — mereka
    # justru LEMAH di volume/price score karena emang lagi gak breakout (itu
    # POINNYA, beli deket support pas lagi lemah, bukan beli pas udah kuat).
    # Query TERPISAH tanpa gate alert_threshold, sama pola kayak mentor call.
    try:
        support_res = (
            supabase.table("scanner_cache")
            .select("ticker,total_score,signal,sector,technical_score,cocok_compression,sideways_days")
            .eq("cocok_buy_on_weakness", True)
            .execute()
        )
        for r in support_res.data:
            scan_by_ticker.setdefault(r["ticker"], r)
        support_defended_tickers = {r["ticker"] for r in support_res.data}
    except Exception:
        support_defended_tickers = set()

    mentor_by_ticker = _active_mentor_calls()
    news_by_ticker = _recent_news_by_ticker()
    macro_sectors = _macro_sector_set(macro_events)

    breakout_tickers = {
        t for t, r in scan_by_ticker.items()
        if (r.get("technical_score") or 0) >= BREAKOUT_TECHNICAL_THRESHOLD
    }
    pool = (breakout_tickers | set(mentor_by_ticker) | support_defended_tickers) - _dedup_seen_keys("alerted")

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
            "support_defended_flag": ticker in support_defended_tickers,
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

    # urut compression_setup DAN support_defended_flag duluan (dua-duanya
    # kandidat non-breakout — compression = SEBELUM breakout, support_defended
    # = "buy on weakness" gak nunggu breakout sama sekali — technical_score-nya
    # pasti kalah dari breakout biasa, jangan sampe ke-cut pool_limit gara-gara
    # itu), sideways_days_before PALING PANJANG jadi tie-breaker ke-2,
    # technical_score (breakout+volume, REAL bukan total_score yang kecampur
    # Accumulation Score mock) baru ke-3
    candidates.sort(
        key=lambda c: (c["compression_setup"], c["support_defended_flag"], c["sideways_days_before"] or 0, c["technical_score"] or 0),
        reverse=True,
    )
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
    # market uptrend — dicek SEKALI doang buat semua kandidat (bukan per-ticker),
    # elemen VCP ketiga: kompresi individual saham gak ampuh kalau IHSG lagi
    # sideways/turun (riset + backtest.py konfirmasi, lihat komentar di atas)
    try:
        ihsg_hist = yf.Ticker("^JKSE").history(period="3mo", auto_adjust=False).dropna(subset=["Close"])
        market_uptrend = is_market_uptrend(ihsg_hist)
        ihsg_20d_pct = _pct_change_nday(ihsg_hist, 20)
    except Exception:
        market_uptrend = False
        ihsg_20d_pct = None

    _levels_cache.clear()
    filtered = []
    for c in candidates:
        try:
            hist = _get_history(c["ticker"])
            price_now = float(hist["Close"].iloc[-1])
            lv = support_resistance(hist)
            _apply_smart_tp(lv, c["ticker"], hist)
            # "buy on weakness" — DIHITUNG DI SINI (bukan di bawah, setelah gate
            # RR) karena BUG ketemu: gate RR dulu selalu dites lawan support
            # trailing-20-hari, walau candidate-nya lolos lewat jalur buy-on-
            # weakness yang basis levelnya BEDA (support_price dari
            # well_defended_support) — gate & caption bisa gak nyambung.
            # Override SEBELUM gate, biar RR yang dites emang RR levelnya
            # sendiri, bukan level yang gak dipake buat SL beneran.
            buy_on_weakness = well_defended_support(hist, price_now)
            apply_buy_on_weakness_support(lv, price_now, buy_on_weakness)
        except Exception:
            continue  # gagal fetch, skip — jangan asumsiin RR-nya oke
        if lv["rr_ratio"] < MIN_RR_RATIO:
            continue
        if lv["risk_pct"] > MAX_RISK_PCT or lv["reward_pct"] > MAX_REWARD_PCT:
            continue  # SL/TP kejauhan dari harga sekarang buat swing beneran (lihat komentar MAX_RISK_PCT) — skip, jangan kirim angka ngaco
        c["rr_ratio"] = lv["rr_ratio"]
        # relative strength vs IHSG 20 hari — bedain breakout yang beneran kuat
        # SENDIRIAN dari breakout yang cuma numpang market lagi rally luas
        # (market_uptrend di atas itu boolean SATU angka buat semua kandidat,
        # gak bedain saham mana yang beneran outperform). Konteks tambahan
        # buat Groq (bukan gate keras — threshold "berapa % dianggap kuat"
        # belum divalidasi backtest, jangan sok tau angka pastinya).
        stock_20d_pct = _pct_change_nday(hist, 20)
        c["relative_strength_vs_ihsg_20d"] = (
            round(stock_20d_pct - ihsg_20d_pct, 2)
            if stock_20d_pct is not None and ihsg_20d_pct is not None else None
        )
        # VCP lengkap (compression + volume dry-up + market uptrend) — backtest
        # (data harga yang udah bener) BUKTIIN versi ini ngalahin breakout biasa
        # (+0.75% vs +0.57% expectancy), beda dari compression longgar doang
        # (+0.3%, KALAH dari breakout biasa). Makanya preferensi Groq sekarang
        # pake compression_vcp, BUKAN compression_setup polos.
        c["compression_vcp"] = bool(
            c["compression_setup"]
            and market_uptrend
            and volume_dry_up(hist, c["sideways_days_before"] or 0)
        )
        # "buy on weakness" (support_price/touches/distance_pct) — dihitung di
        # atas, SEBELUM gate RR (lihat komentar di situ), tinggal nempel ke
        # candidate dict di sini.
        c["buy_on_weakness"] = buy_on_weakness
        # chart pattern (triangle ascending/descending/symmetrical) — konteks
        # TA tambahan, user eksplisit minta "setajem mungkin". Reuse hist yang
        # udah difetch, zero API baru. None kalau bukan salah satu dari 3 pola.
        c["chart_pattern"] = detect_chart_pattern(hist)
        _levels_cache[c["ticker"]] = lv
        # trend (MA50/200 mingguan, lihat determine_trend di levels.py) UDAH
        # dihitung di _apply_smart_tp di atas tapi cuma nempel ke levels buat
        # caption doang — sekarang ikut disalurin ke Groq juga (breakout SEARAH
        # trend jangka panjang lebih reliable, breakout di tengah downtrend
        # patut dicurigai cuma bear-rally, prinsip TA klasik).
        c["trend"] = lv.get("trend")
        # ADX (kekuatan trend, bukan arah) + Bollinger squeeze/posisi + MA5/10/20
        # golden/death cross — 3 indikator TA tambahan, user eksplisit minta.
        # SEMUA konteks tambahan buat Groq (bukan gate keras, threshold pasti
        # belum divalidasi backtest), reuse hist yang udah difetch, zero API call baru.
        c["adx"] = adx(hist)
        c["bollinger"] = bollinger_signal(hist)
        try:
            ma5 = float(hist["Close"].tail(5).mean())
            ma10 = float(hist["Close"].tail(10).mean())
            ma20 = float(hist["Close"].tail(20).mean())
            c["ma_alignment"] = ma_alignment(ma5, ma10, ma20)
        except Exception:
            c["ma_alignment"] = None
        # laporan keuangan + order flow + broker net-buy — konteks tambahan buat
        # Groq (lihat pick_alert_candidate), bukan syarat wajib, diem kalau
        # Invezgo belum aktif/gagal fetch. Di-cache PER TICKER PER HARI
        # (_invezgo_enrich_cache) — check_and_alert ini jalan TIAP JAM selama
        # window off-hours (~15 jam/malam), tanpa cache bakal re-fetch 3 request
        # x tiap kandidat pool TIAP JAM padahal datanya sama aja (market udah
        # tutup, data hari itu udah final) — boros kuota Invezgo parah kalau kelewatan.
        if invezgo_client.is_configured():
            today_s = today_wib().isoformat()
            cached = _invezgo_enrich_cache.get(c["ticker"])
            if cached and cached["date"] == today_s:
                c.update(cached["fields"])
            else:
                fields = {}
                try:
                    # BUG NYATA ketemu 2026-09-02: raw financial_statement (default
                    # limit=8 kuartal, 27-37+ baris akun, ~20-26rb karakter/ticker)
                    # dikirim MENTAH buat SETIAP kandidat di pool (bisa ~20 ticker) —
                    # 1 panggilan pick_alert_candidate sampe 84rb token, abisin
                    # kuota HARIAN Groq (200rb/hari) sendirian, bikin 429 diem-diem
                    # ketelen except Exception di check_and_alert. Kemungkinan besar
                    # ini SALAH SATU akar masalah "Swing NOL alert selamanya" (bareng
                    # bug MAX_ALERTS_PER_WEEK yang udah difix). Fix: trim_financial_
                    # statement (sama pola kayak Portfolio Simulation 413 fix) + limit=3.
                    raw_fin = invezgo_client.get_financial_statement(c["ticker"], statement="IS", limit=3)
                    fields["financial_statement"] = trim_financial_statement(raw_fin)
                except Exception:
                    pass
                try:
                    pt = invezgo_client.get_price_table(c["ticker"], today_s)
                    buy_vol = sum(float(r.get("buy_volume") or 0) for r in pt)
                    sell_vol = sum(float(r.get("sell_volume") or 0) for r in pt)
                    fields["order_flow"] = {
                        "buy_volume": buy_vol, "sell_volume": sell_vol,
                        "buy_sell_ratio": round(buy_vol / sell_vol, 2) if sell_vol > 0 else None,
                    }
                except Exception:
                    pass
                try:
                    from_s = (today_wib() - timedelta(days=5)).isoformat()
                    bs = invezgo_client.get_broker_summary(c["ticker"], from_s, today_s)
                    ranked = sorted(bs, key=lambda b: float(b.get("net_value") or 0), reverse=True)
                    fields["broker_net_top"] = [
                        {"code": b["code"], "net_value": float(b["net_value"])} for b in ranked[:3]
                    ]
                except Exception:
                    pass
                # price_seasonality + sankey_chart — DULU cuma dipajang di UI StockDetail
                # (dicabut, user: gak kepake buat baca), sekarang jadi bahan konteks Groq
                # doang (pendukung sentimen, bukan syarat wajib) — lihat pick_alert_candidate.
                try:
                    season = invezgo_client.get_price_seasonality(c["ticker"])
                    rows = season if isinstance(season, list) else (season or {}).get("data") or []
                    month_now = today_wib().strftime("%B")
                    match = next(
                        (r for r in rows if str(r.get("month")).strip().lower() in (month_now.lower(), str(today_wib().month))),
                        None,
                    )
                    if match and match.get("percentage_change") is not None:
                        fields["seasonality_bulan_ini"] = round(float(match["percentage_change"]), 2)
                except Exception:
                    pass
                try:
                    sankey = invezgo_client.get_sankey_chart(c["ticker"], today_s)
                    links = (sankey or {}).get("links") or []
                    if links:
                        top = max(links, key=lambda l: abs(float(l.get("value") or 0)))
                        fields["money_flow_top"] = {
                            "source": str(top.get("source")).strip(),
                            "target": str(top.get("target")).strip(),
                            "value": float(top.get("value") or 0),
                        }
                except Exception:
                    pass
                # insider/pengendali (wajib lapor OJK) — jauh lebih kuat dari broker_net_top
                # biasa kalau kepemilikan naik konsisten (lihat sistem prompt pick_alert_candidate)
                try:
                    insider_from = (today_wib() - timedelta(days=90)).isoformat()
                    ins = invezgo_client.get_insider_activity(c["ticker"], insider_from, today_s, limit=5)
                    fields["insider_activity"] = [
                        {
                            "date": r.get("date"), "name": r.get("name"),
                            "prev_percent": r.get("prev_percent"), "next_percent": r.get("next_percent"),
                            "purpose": r.get("purpose"), "nationality": r.get("nationality"),
                        }
                        for r in (ins.get("data") or [])
                    ]
                except Exception:
                    pass
                # broker paling akumulasi dari time series beneran (bukan snapshot) + konsistensi
                # harian — reuse _detect_bandar (udah dipake Nightly Portfolio Review)
                try:
                    bandar_from = (today_wib() - timedelta(days=30)).isoformat()
                    fields["bandar"] = _detect_bandar(c["ticker"], bandar_from, today_s)
                except Exception:
                    pass
                # cross-check buy_on_weakness (pola HARGA doang) lawan tape reading
                # beneran — user eksplisit minta: bukan cuma "harga ketahan di sini"
                # dari price action, tapi BENERAN ada broker yang narik barang pas
                # nyentuh level itu. Reuse touch_dates dari well_defended_support
                # (udah dihitung di atas, gratis) buat cari running_trade cuma di
                # tanggal-tanggal itu doang (bukan scan lebar), murah + tepat sasaran.
                if c.get("buy_on_weakness"):
                    try:
                        fields["broker_defended_support"] = _broker_defended_support(
                            c["ticker"], c["buy_on_weakness"]["touch_dates"],
                        )
                    except Exception:
                        pass
                _invezgo_enrich_cache[c["ticker"]] = {"date": today_s, "fields": fields}
                c.update(fields)
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
    harganya udah lari duluan sebelum sempet dikoreksi).

    Dipanggil 2 tempat: run_morning_routine (1x/hari, safety net + ngurusin
    'missed') DAN run_entry_zone_watcher (tiap 15 menit pas market buka, biar
    notif ENTRY ZONE gak nunggu besok pagi). Pas market buka, day_low/day_high
    diambil dari bar 15-menit INTRADAY hari ini (live, ke-update tiap loop),
    bukan candle harian _get_history yang cuma final abis market tutup —
    fallback ke situ kalau intraday gagal/belum ada bar hari ini (misal
    dipanggil pas market masih tutup)."""
    try:
        res = supabase.table("signal_alerts").select("*").eq("status", "waiting_entry").execute()
    except Exception:
        return
    now = datetime.now(timezone.utc)
    for row in res.data:
        try:
            day_low = day_high = None
            try:
                hist_15m = _get_history_intraday(row["ticker"])
                today_bars = hist_15m[hist_15m.index.date == today_wib()]
                if not today_bars.empty:
                    day_low = float(today_bars["Low"].min())
                    day_high = float(today_bars["High"].max())
            except Exception:
                pass
            if day_low is None:
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


MAX_ALERTS_PER_WEEK = 5  # Swing max 5 saham TERBAIK per MINGGU (revisi user dari 2 —
                           # gak harus kirim tiap hari, tapi dikasih lebih banyak slot
                           # kalau emang ada beberapa setup meyakinkan minggu itu),
                           # bukan harian — gerakannya gak secepat itu, ngirim tiap
                           # hari/jam itu perilaku Scalping bukan Swing

MAX_CONCURRENT_SWING = 5  # portofolio SELALU dijaga maksimal 5 saham Swing RUNNING
                           # bersamaan (keputusan sadar user: lebih dari itu susah
                           # diawasin — "kaya supermarket"). BEDA dari MAX_ALERTS_
                           # PER_WEEK (itu batas SINYAL BARU per minggu, ini batas
                           # POSISI KONKUREN kapan aja). Begitu 5 slot penuh, kandidat
                           # baru yang meyakinkan gak langsung di-skip — ditawarin
                           # ROTASI (ganti posisi paling lemah) lewat tombol Telegram
                           # (lihat _propose_rotation), keputusan final tetep di user.


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
        weekly = yf.Ticker(f"{ticker}.JK").history(period="max", interval="1wk", auto_adjust=False).dropna(subset=["Close"])
        monthly = yf.Ticker(f"{ticker}.JK").history(period="max", interval="1mo", auto_adjust=False).dropna(subset=["Close"])
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


def _send_swing_alert(ticker: str, hist, levels: dict, score_row: dict, pick: dict,
                       candidate: dict, macro_events: list[dict]) -> int | None:
    """Bangun caption+chart, kirim ke Telegram, insert signal_alerts (source
    'swing'). Dipake DUA alur: check_and_alert() (flow normal, slot masih
    kosong) DAN _handle_rotation_callback() (abis user klik Terima rotasi) —
    biar logic kirim alert gak duplikat di 2 tempat."""
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
        "order_flow": candidate.get("order_flow"),
        "broker_net_top": candidate.get("broker_net_top"),
        "bandar": candidate.get("bandar"),
        "insider_activity": candidate.get("insider_activity"),
        "buy_on_weakness": candidate.get("buy_on_weakness"),
        "broker_defended_support": candidate.get("broker_defended_support"),
    }
    reasoning = analyze_alert(ticker, score_breakdown, levels, context)
    caption = _build_caption(ticker, score_row["total_score"], levels, reasoning, pick.get("faktor_pendukung", []), candidate.get("bandar"))
    message_id = send_alert_photo(chart_png, caption)
    if not message_id:
        return None  # Telegram belum di-connect di Settings, atau gagal kirim

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
            "source": "swing",
            # buat korelasiin faktor+conviction->outcome nanti (Weekly Postmortem), dulu ilang abis kekirim caption
            "faktor_pendukung": {"faktor": pick.get("faktor_pendukung", []), "conviction": pick.get("conviction")},
        }).execute()
    except Exception:
        pass  # tabel belum di-setup / gagal simpen — jangan gagalin alert-nya cuma gara-gara ini
    return message_id


def _count_open_swing_positions() -> int:
    """Posisi Swing yang lagi 'makan slot' (waiting_entry ATAU open) — source
    NULL dianggep Swing juga (row lama dari sebelum kolom `source` ada, BSJP/
    BPJS eksplisit set source='bpjs' dkk jadi gak kehitung di sini)."""
    try:
        res = supabase.table("signal_alerts").select("source").in_("status", ["waiting_entry", "open"]).execute()
        return sum(1 for r in res.data if r.get("source") in (None, "swing"))
    except Exception:
        return 0  # tabel/kolom belum ada — anggep 0 slot kepake, biar alert tetep jalan normal


def _propose_rotation(ticker: str, hist, levels: dict, score_row: dict, pick: dict,
                       candidate: dict, macro_events: list[dict]) -> None:
    """5 slot Swing lagi penuh SEMUA tapi ada kandidat baru meyakinkan —
    tawarin GANTI posisi paling lemah lewat Groq (evaluate_portfolio_rotation).
    Kalau Groq nilai worth, kirim Telegram dengan tombol Terima/Tolak —
    KEPUTUSAN FINAL tetep di user, NEXUS gak pernah auto-eksekusi rotasi.
    Dedup per hari per ticker (biar gak nawarin ulang candidate yang sama
    tiap jam selama masih pending)."""
    if _dedup_seen("rotation_proposed", ticker):
        return

    try:
        open_res = supabase.table("signal_alerts").select("*").in_("status", ["waiting_entry", "open"]).execute()
        current_rows = [r for r in open_res.data if r.get("source") in (None, "swing")]
    except Exception:
        return
    if not current_rows:
        return

    current_positions = []
    for row in current_rows:
        try:
            price_now = float(_get_history(row["ticker"])["Close"].iloc[-1])
        except Exception:
            continue
        current_positions.append({
            "ticker": row["ticker"], "entry_price": row["entry_price"], "price_now": price_now,
            "pnl_pct": round((price_now - row["entry_price"]) / row["entry_price"] * 100, 2),
            "target": row["target"], "stop_loss": row["stop_loss"],
        })
    if not current_positions:
        return

    new_candidate = {
        "ticker": ticker, "compression_vcp": candidate.get("compression_vcp"),
        "sideways_days_before": candidate.get("sideways_days_before"),
        "rr_ratio": candidate.get("rr_ratio"), "signal": candidate.get("signal"),
        "faktor_pendukung": pick.get("faktor_pendukung", []),
    }

    try:
        verdict = evaluate_portfolio_rotation(current_positions, new_candidate)
    except Exception:
        return
    if not verdict.get("rotate") or not verdict.get("drop_ticker"):
        return  # Groq nilai gak worth rotate — diem, sama kayak skip biasa

    drop_ticker = verdict["drop_ticker"]
    drop_row = next((r for r in current_rows if r["ticker"] == drop_ticker), None)
    if drop_row is None:
        return  # Groq halusinasi ticker di luar 5 posisi — jaga2

    payload = {
        "new_ticker": ticker, "new_levels": levels, "new_score_row": dict(score_row),
        "new_pick": pick, "new_candidate": candidate, "macro_events": macro_events,
        "drop_signal_alert_id": drop_row["id"], "drop_ticker": drop_ticker,
    }
    try:
        ins = supabase.table("pending_rotations").insert({
            "payload": payload, "status": "pending", "alasan": verdict.get("alasan"),
        }).execute()
        rotation_id = ins.data[0]["id"]
    except Exception:
        return  # tabel belum di-setup — jangan kirim tombol yang gak bisa dieksekusi

    _dedup_mark("rotation_proposed", ticker)
    alasan = _esc(verdict.get("alasan") or "-")
    text = (
        f"🔄 <b>USUL ROTASI SWING</b>\n\n"
        f"5 slot lagi penuh. Kandidat baru <b>{_esc(ticker)}</b> dinilai lebih "
        f"meyakinkan dari <b>{_esc(drop_ticker)}</b> yang lagi running.\n\n"
        f"{alasan}\n\n"
        f"Terima = tutup {_esc(drop_ticker)} SEKARANG (realisasi P&L saat ini, "
        f"apapun untung/rugi-nya), buka {_esc(ticker)} jadi slot baru."
    )
    buttons = [[
        {"text": "✅ Terima", "callback_data": f"rot_yes:{rotation_id}"},
        {"text": "❌ Tolak", "callback_data": f"rot_no:{rotation_id}"},
    ]]
    send_alert_with_buttons(text, buttons)


def _handle_rotation_callback(callback: dict) -> None:
    """Proses klik tombol Terima/Tolak dari _propose_rotation. callback_data
    format 'rot_yes:<id>' / 'rot_no:<id>' — id nunjuk baris pending_rotations."""
    data = callback.get("data", "")
    callback_id = callback.get("id")
    message_id = (callback.get("message") or {}).get("message_id")

    if not data.startswith(("rot_yes:", "rot_no:")):
        return  # bukan tombol rotasi

    action, _, rotation_id = data.partition(":")
    try:
        res = supabase.table("pending_rotations").select("*").eq("id", int(rotation_id)).limit(1).execute()
        row = res.data[0] if res.data else None
    except Exception:
        row = None
    if not row:
        if callback_id:
            answer_callback_query(callback_id, "Draft rotasi udah gak ketemu (mungkin expired).")
        return
    if row["status"] != "pending":
        if callback_id:
            answer_callback_query(callback_id, "Udah diproses sebelumnya.")
        return

    payload = row["payload"]

    if action == "rot_no":
        supabase.table("pending_rotations").update({"status": "rejected"}).eq("id", row["id"]).execute()
        if callback_id:
            answer_callback_query(callback_id, "Ditolak.")
        if message_id:
            edit_message_text(message_id, "❌ <b>Rotasi ditolak</b> — posisi lama tetep dipegang.")
        return

    # rot_yes: levels di payload itu SNAPSHOT pas ditawarin — kalau user baru klik
    # sekarang (bisa berjam-jam/berhari kemudian), harga udah gerak, entry_low/
    # entry_high/target/stop_loss lama bisa udah gak nyambung sama harga sekarang.
    # WAJIB recompute fresh + re-cek gate RR (pola sama kayak _gather_candidates),
    # SEBELUM nutup posisi lama — biar kalau dibatalin, slot lama gak ke-drop
    # sia-sia (dulu ketutup duluan tanpa validasi, resiko ninggalin slot bolong).
    new_ticker = payload["new_ticker"]
    try:
        hist_new = _get_history(new_ticker)
        levels = support_resistance(hist_new)
        _apply_smart_tp(levels, new_ticker, hist_new)
    except Exception:
        log.exception("gagal recompute levels abis rotasi diterima")
        if callback_id:
            answer_callback_query(callback_id, "Gagal ambil data harga terbaru, coba lagi.")
        return
    if levels["rr_ratio"] < MIN_RR_RATIO or levels["risk_pct"] > MAX_RISK_PCT or levels["reward_pct"] > MAX_REWARD_PCT:
        supabase.table("pending_rotations").update({"status": "expired"}).eq("id", row["id"]).execute()
        if callback_id:
            answer_callback_query(callback_id, "Batal — harga udah gerak jauh, RR gak masuk akal lagi.")
        if message_id:
            edit_message_text(
                message_id,
                f"⚠️ <b>Rotasi dibatalkan</b> — harga {_esc(new_ticker)} udah gerak sejak ditawarin, "
                f"RR gak masuk akal lagi. {_esc(payload['drop_ticker'])} tetep dipegang.",
            )
        return

    # tutup posisi lama SEKARANG (realisasi P&L saat ini, BUKAN nunggu TP/SL asli)
    try:
        old_res = supabase.table("signal_alerts").select("*").eq("id", payload["drop_signal_alert_id"]).limit(1).execute()
        old_row = old_res.data[0] if old_res.data else None
    except Exception:
        old_row = None

    if old_row:
        try:
            price_now = float(_get_history(old_row["ticker"])["Close"].iloc[-1])
        except Exception:
            price_now = old_row["entry_price"]
        outcome_pct = round((price_now - old_row["entry_price"]) / old_row["entry_price"] * 100, 2)
        try:
            supabase.table("signal_alerts").update({
                "status": "rotated_out", "closed_at": datetime.now(timezone.utc).isoformat(),
                "close_price": price_now, "outcome_pct": outcome_pct,
            }).eq("id", old_row["id"]).execute()
        except Exception:
            pass
        if old_row.get("telegram_message_id"):
            delete_message(old_row["telegram_message_id"])

    try:
        _send_swing_alert(
            new_ticker, hist_new, levels,
            payload["new_score_row"], payload["new_pick"], payload["new_candidate"],
            payload.get("macro_events", []),
        )
    except Exception:
        log.exception("gagal buka posisi baru abis rotasi diterima")

    supabase.table("pending_rotations").update({"status": "accepted"}).eq("id", row["id"]).execute()
    if callback_id:
        answer_callback_query(callback_id, "Diterima — rotasi dieksekusi.")
    if message_id:
        edit_message_text(
            message_id,
            f"✅ <b>Rotasi diterima</b> — {_esc(payload['drop_ticker'])} ditutup, "
            f"{_esc(payload['new_ticker'])} dibuka jadi slot baru.",
        )


def check_and_alert() -> None:
    # DIAGNOSTIC (2026-09-02): user lapor NOL alert Swing kekirim SELAMANYA
    # (signal_alerts cuma 2 baris, dua-duanya BPJS) padahal pool breakout
    # sehari-hari cukup gede (24/31 ticker Strong lolos technical_score>=12
    # pas dicek manual). Gak ada akses Railway log dari sini buat mastiin
    # gate mana yang nolak tiap malem — log.info tiap early-return biar
    # ketauan PASTI dari log run off-hours berikutnya (17:00-08:00 WIB),
    # bukan nebak. Hapus/rapihin lagi abis ketauan akar masalahnya.
    if not _in_offhours_window():
        return  # Swing itu non-urgent, sengaja cuma alert pas market tutup (17:00-08:00)

    settings = _load_settings()
    if not settings["notif_strong_signal"]:
        log.info("check_and_alert: skip, notif_strong_signal off di Settings")
        return  # user matiin "Strong signal alerts" di Settings

    # BUG NYATA ketemu 2026-09-02 (root cause "Swing NOL alert selamanya"):
    # cap ini dulu ngitung dari alert_dedup category="alerted" (gte dedup_date
    # RANGE 7 hari) — tabel itu GAK PERNAH dibersihin pas signal_alerts-nya
    # closed/dihapus manual (kejadian nyata: insiden PACK, row signal_alerts
    # dihapus tapi row alert_dedup-nya kebawa terus). Ketauan ada 16 row
    # "alerted" numpuk dari 2026-08-28/29 (MPIX/KETR/LIFE/PACK dkk, semua
    # insiden LAMA yang udah closed/dihapus) — 16 >= MAX_ALERTS_PER_WEEK=5,
    # jadi gate ini NOLAK SETIAP KALI dari 28/29 Agustus, padahal pool
    # kandidat sehat (dicek manual: 7 lolos RR gate). Fix: itung dari
    # signal_alerts LANGSUNG (source of truth yang bener2 ke-maintain —
    # closed/dihapus beneran ilang dari hitungan), bukan tabel dedup terpisah
    # yang gak ada mekanisme cleanup-nya sama sekali.
    try:
        week_ago = (today_wib() - timedelta(days=7)).isoformat()
        weekly_count = (
            supabase.table("signal_alerts").select("id", count="exact")
            .eq("source", "swing").gte("alerted_at", week_ago).execute()
        ).count or 0
    except Exception:
        weekly_count = 0
    if weekly_count >= MAX_ALERTS_PER_WEEK:
        log.info(f"check_and_alert: skip, udah kena MAX_ALERTS_PER_WEEK ({weekly_count}/{MAX_ALERTS_PER_WEEK})")
        return  # udah kena limit sinyal BARU minggu ini

    _check_invalidated()

    macro_events = [e for e in get_forex_events() if e["impact"] in ("High", "Medium")]
    candidates = _gather_candidates(macro_events, settings)
    if not candidates:
        log.info("check_and_alert: skip, _gather_candidates balikin pool kosong")
        return

    try:
        pick = pick_alert_candidate(candidates, macro_events, upcoming_holidays(within_days=3))
    except Exception:
        log.exception(f"check_and_alert: pick_alert_candidate gagal, pool={len(candidates)} kandidat")
        return  # Groq gagal, coba lagi interval berikutnya

    ticker = pick.get("pilih")
    if not ticker:
        log.info(f"check_and_alert: Groq pilih null dari {len(candidates)} kandidat — alasan: {pick.get('alasan_singkat')}")
        return  # sengaja gak ada yang meyakinkan — mending gak ada call daripada call asal
    if (pick.get("conviction") or 0) < MIN_CONVICTION_SWING:
        log.info(f"check_and_alert: {ticker} conviction {pick.get('conviction')} < {MIN_CONVICTION_SWING}, skip")
        return  # Groq sendiri gak cukup yakin (conviction rendah) — mending skip daripada kirim call ragu-ragu

    candidate = next((c for c in candidates if c["ticker"] == ticker), None)
    # signal Strong/Moderate itu dari total_score (Volume+Price+Accum+Technical
    # digabung) — buy_on_weakness JUSTRU lemah di situ (gak lagi breakout, itu
    # poinnya), jadi dikecualiin dari syarat ini, sama kayak dia dikecualiin
    # dari gate alert_threshold pas pool-building.
    if candidate is None or (candidate.get("signal") not in ("Strong", "Moderate") and not candidate.get("buy_on_weakness")):
        log.info(f"check_and_alert: {ticker} dipilih Groq tapi gak ada di pool / signal bukan Strong-Moderate / bukan buy_on_weakness")
        return  # jaga-jaga kalau Groq halusinasi ticker di luar pool / gak penuhi syarat skor
    if not candidate.get("breakout_confirmed") and not candidate.get("mentor_call") and not candidate.get("buy_on_weakness"):
        log.info(f"check_and_alert: {ticker} dipilih Groq tapi breakout_confirmed=False & gak ada mentor_call/buy_on_weakness")
        return  # jaga-jaga kalau Groq ngelanggar instruksi sendiri (pilih modal berita doang, gak breakout/support-defended)

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
        log.exception(f"check_and_alert: gagal ambil score_row buat {ticker}")
        return

    try:
        hist = _get_history(ticker)
        # levels udah lengkap dihitung pas _gather_candidates (RR gate sekarang
        # pake analisa lengkap juga) — reuse dari cache, jangan hitung ulang 2x
        levels = _levels_cache.get(ticker)
        if levels is None:
            levels = support_resistance(hist)
            _apply_smart_tp(levels, ticker, hist)
    except Exception:
        log.exception(f"check_and_alert: gagal hitung levels buat {ticker}")
        return  # gagal fetch harga — coba lagi interval berikutnya

    # portofolio SELALU dijaga max 5 saham Swing konkuren (keputusan user) —
    # kalau penuh, jangan langsung skip diem-diem kayak dulu, tawarin ROTASI
    if _count_open_swing_positions() >= MAX_CONCURRENT_SWING:
        log.info(f"check_and_alert: {ticker} lolos semua gate tapi 5 slot Swing penuh, tawarin rotasi")
        try:
            _propose_rotation(ticker, hist, levels, score_row, pick, candidate, macro_events)
        except Exception:
            log.exception("_propose_rotation gagal")
        return

    try:
        _send_swing_alert(ticker, hist, levels, score_row, pick, candidate, macro_events)
        log.info(f"check_and_alert: alert Swing {ticker} KEKIRIM")
    except Exception:
        log.exception(f"check_and_alert: gagal kirim alert buat {ticker}")
        return  # gagal di Groq/render — coba lagi interval berikutnya, jangan tandain alerted


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
    settings = _load_settings()
    if not settings["notif_watchlist"]:
        return
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


WHALE_MIN_VALUE = 500_000_000  # Rp500 juta/transaksi — ASUMSI heuristik kasar, BUKAN dari
                                 # riset/data broker beneran (belum ada API key buat liat sebaran
                                 # value_traded transaksi normal per saham). Sesuaikan begitu udah
                                 # keliatan distribusi asli, mungkin perlu beda ambang per saham
                                 # (saham gede vs kecil) bukan 1 angka flat.

SPLIT_ORDER_MIN_TRADES = 3          # minimal berapa transaksi kecil dari buyer SAMA dalam 1 menit
SPLIT_ORDER_MIN_SELLERS = 2         # minimal berapa broker LAWAN beda (bukan cuma 1 seller doang)
SPLIT_ORDER_MIN_TOTAL_VALUE = 500_000_000  # total gabungan minimal — sama ambang whale, ASUMSI juga


def _check_whale_alerts() -> None:
    """Kerangka Whale/Block Trade Alert — 2 pola deteksi dari running-trade
    Invezgo, cuma buat ticker di watchlist (BUKAN semua 951 saham, hemat kuota):
    1. Transaksi TUNGGAL abnormal gede.
    2. Split order — 1 buyer pecah order jadi beberapa transaksi kecil dalam
       menit yang sama, makan barang dari BEBERAPA broker lawan beda (ide user:
       "broker A jam 9:39 beli 3x 50rb lembar, barangnya dari broker B/C/D" —
       pola akumulasi institusi yang sengaja dipecah biar gak keliatan 1
       transaksi gede di tape). Dedup per (ticker, buyer, menit) biar gak
       ke-alert ulang tiap loop.
    Diem total kalau Invezgo belum aktif (is_configured()) ATAU market lagi
    tutup — run_scheduler manggil ini TIAP JAM 24/7, tapi transaksi whale cuma
    relevan pas market beneran buka (di luar itu gak ada transaksi baru sama
    sekali, cuma buang kuota Invezgo re-fetch data hari itu yang udah final)."""
    settings = _load_settings()
    if not settings["notif_whale_alert"]:
        return
    if not invezgo_client.is_configured() or not _in_market_hours():
        return

    try:
        watch_res = supabase.table("watchlist").select("ticker").execute()
        tickers = [r["ticker"] for r in watch_res.data]
    except Exception:
        return
    if not tickers:
        return

    today = today_wib().isoformat()
    for ticker in tickers:
        try:
            # BUG NYATA ketemu 2026-09-02 (user lapor whale detector "gak nyala"
            # sama sekali, 0 alert dari sejak dibikin): get_running_trade tanpa
            # `page` = SELALU halaman 1 (kronologis AWAL hari, dikonfirmasi lawan
            # data live JECX: page1 jam 08:58-09:04, page 18/18 jam 15:49-16:14).
            # Loop ini jalan TIAP JAM sepanjang hari tapi selalu re-fetch 5-10
            # menit PERTAMA trading yang SAMA — gak pernah liat transaksi
            # terbaru sama sekali, brp kalipun re-check. Fix: fetch halaman
            # TERAKHIR (paling baru) pake totalPage dari response, bukan page 1.
            first = invezgo_client.get_running_trade(ticker, today, limit=100, page=1)
            total_pages = first.get("totalPage") or 1
            trades = list(first.get("data") or []) if total_pages == 1 else []
            if total_pages > 1:
                last = invezgo_client.get_running_trade(ticker, today, limit=100, page=total_pages)
                trades.extend(last.get("data") or [])
                if total_pages > 2:
                    prev = invezgo_client.get_running_trade(ticker, today, limit=100, page=total_pages - 1)
                    trades.extend(prev.get("data") or [])
        except Exception:
            continue

        # pola 1: transaksi tunggal gede
        for t in trades:
            try:
                value = float(t["price"]) * float(t["volume"])
            except Exception:
                continue
            if value < WHALE_MIN_VALUE:
                continue
            key = f"{ticker}:{t.get('time')}:{t.get('price')}:{t.get('volume')}"
            if _dedup_seen("whale", key):
                continue
            side = t.get("type", "—")
            text = (
                f"🐋 <b>WHALE ALERT — {_esc(ticker)}</b>\n\n"
                f"{side} {int(t['volume']):,} lembar @ Rp{t['price']:,.0f} "
                f"(nilai ~Rp{value:,.0f}) jam {t.get('time', '—')}"
            )
            if send_alert(text):
                _dedup_mark("whale", key)

        # pola 2: split order — group by (buyer, menit)
        groups: dict[tuple[str, str], list[dict]] = {}
        for t in trades:
            buyer = t.get("buyer")
            minute = str(t.get("time", ""))[:5]  # "HH:MM:SS" -> "HH:MM"
            if not buyer or not minute:
                continue
            groups.setdefault((buyer, minute), []).append(t)

        for (buyer, minute), group in groups.items():
            if len(group) < SPLIT_ORDER_MIN_TRADES:
                continue
            sellers = {g.get("seller") for g in group if g.get("seller")}
            if len(sellers) < SPLIT_ORDER_MIN_SELLERS:
                continue
            try:
                total_value = sum(float(g["price"]) * float(g["volume"]) for g in group)
                total_volume = sum(float(g["volume"]) for g in group)
            except Exception:
                continue
            if total_value < SPLIT_ORDER_MIN_TOTAL_VALUE:
                continue
            key = f"{ticker}:{buyer}:{minute}"
            if _dedup_seen("split_order", key):
                continue
            text = (
                f"🧩 <b>SPLIT ORDER — {_esc(ticker)}</b>\n\n"
                f"Broker {_esc(buyer)} pecah {len(group)}x order jam {minute} "
                f"(dari {len(sellers)} broker lawan: {_esc(', '.join(sorted(sellers)))}) — "
                f"total {int(total_volume):,} lembar, nilai ~Rp{total_value:,.0f}"
            )
            if send_alert(text):
                _dedup_mark("split_order", key)


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
    """Gated `notif_portfolio_risk` di Settings. Reuse holdings yang eksplisit
    disimpen user lewat tombol "Simpan sebagai Portofolio Aktif" di Portfolio
    Simulation (routers/portfolio.py::save_active_portfolio) — TERPISAH dari
    simulasi test/"what if" (routers/portfolio.py::simulate_portfolio, gak
    persist). Dipindah dari pagi ke MALAM (jalan
    bareng Recap Malam) + detail per-saham (technical + _detect_bandar), sama
    kedalaman kayak _send_running_positions_update — bedanya holdings ini APA
    YANG BENERAN DIPEGANG user (portfolio_holdings), bukan cuma posisi yang
    NEXUS sendiri alert-in (signal_alerts). portfolio_holdings gak nyimpen
    tanggal beli, jadi _detect_bandar pakai window tetap 30 hari ke belakang
    (bukan entry_date asli kayak posisi Swing)."""
    settings = _load_settings()
    if not settings["notif_portfolio_risk"]:
        return

    try:
        res = supabase.table("portfolio_holdings").select("holdings").eq("id", 1).limit(1).execute()
    except Exception:
        return
    holdings = (res.data[0].get("holdings") if res.data else None) or []
    if not holdings:
        return

    from routers.portfolio import _simulate
    try:
        result = _simulate(holdings)
    except Exception:
        return

    per_saham = {p.get("kode"): p for p in result.get("per_saham", [])}
    today = today_wib().isoformat()
    lookback_from = (today_wib() - timedelta(days=30)).isoformat()
    risk_emoji = {"low": "🟢", "medium": "🟡", "high": "🚨"}

    lines = ["💼 <b>Portfolio Review Malam</b>\n"]
    for h in holdings:
        kode = h["kode"]
        try:
            price_now = float(_get_history(kode)["Close"].iloc[-1])
            pnl_pct = round((price_now - h["avg_price"]) / h["avg_price"] * 100, 2)
            pnl_txt = f"{'+' if pnl_pct >= 0 else ''}{pnl_pct}%"
        except Exception:
            pnl_txt = "?"
        p = per_saham.get(kode, {})
        risk = p.get("risk_level", "-")
        alasan = _esc(p.get("alasan") or "-")
        lines.append(f"{risk_emoji.get(risk, '⚪')} <b>{_esc(kode)}</b> ({pnl_txt}) — risk {risk}\n{alasan}\n")

        bandar = _detect_bandar(kode, lookback_from, today)
        if bandar:
            lines.append(_format_bandar_line(bandar))

    if result.get("overall_risk") == "high":
        lines.append(f"\n🚨 <b>OVERALL RISK: HIGH</b>\n{_esc(result.get('portfolio_impact_summary', '-'))}")
    rekomendasi = result.get("rekomendasi_aksi") or []
    if rekomendasi:
        lines.append("\n📌 Rekomendasi: " + "; ".join(_esc(r) for r in rekomendasi))

    send_alert("\n".join(lines))


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


def _detect_bandar(ticker: str, from_date: str, to_date: str) -> dict | None:
    """Perkiraan broker paling banyak akumulasi + trend akumulasi/distribusi
    BELAKANGAN, dari Inventory Chart (time series net value per broker) — jauh
    lebih representatif dari broker_summary yang cuma snapshot 1 rentang
    digabung jadi 1 angka. None kalau Invezgo belum aktif/gagal fetch/gak ada
    broker net-buy signifikan.

    JUJUR soal batasnya: avg_price_estimate itu APPROKSIMASI dari 2 hari
    net-buy TERBESAR broker itu doang (bukan rata-rata seluruh periode — fetch
    running-trade per hari buat SELURUH periode sideways/holding kemahalan
    kuota), volume-weighted dari transaksi yang beneran match buyer=broker itu
    di hari-hari sample. SENGAJA GAK nyoba nebak sisa modal bandar buat
    memprediksi average-down/up — gak ada cara tau itu dari data publik
    manapun (permintaan eksplisit user), ini cuma info posisi doang.

    Insight mentor (contoh CARE & BANK): SIDEWAYS LAMA + akumulasi STEADY
    (broker net-buy KONSISTEN mayoritas hari, bukan cuma total net-buy gede)
    itu sign bagus — barang lagi "dikeringin", breakout jadi lebih ringan.
    Dicek dari `inv["price"]` (udah kebawa 1x fetch bareng, gak nambah request)
    buat sideways + % hari net-buy positif broker top buat konsistensi.

    PENTING (ketemu 2026-09-01, bug beneran sebelum fix ini): `data[].value`
    per broker dari API itu KUMULATIF sejak `from_date` (dikonfirmasi lawan
    API asli — hari pertama window nilainya ratusan juta, hari terakhir udah
    triliunan, growth curve, BUKAN oscillating kayak net-flow harian).
    Nge-sum langsung across dates (kode versi lama) itu SALAH — double
    counting parah, angka `cumulative_net_value` bisa berlipat-lipat lebih
    gede dari kenyataan. WAJIB di-diff() (delta hari-ke-hari) dulu sebelum
    diapa-apain: total periode = value TERAKHIR (bukan sum), trend/consistency
    pake delta harian (bukan raw cumulative value)."""
    if not invezgo_client.is_configured():
        return None
    try:
        inv = invezgo_client.get_inventory_chart_stock(ticker, from_date, to_date)
    except Exception:
        return None
    brokers = inv.get("broker") or []
    if not brokers:
        return None

    def _daily_deltas(data: list[dict]) -> list[dict]:
        prev = 0.0
        out = []
        for d in sorted(data, key=lambda x: x["date"]):
            v = d.get("value", 0) or 0
            out.append({"date": d["date"], "delta": v - prev})
            prev = v
        return out

    ranked = []
    for b in brokers:
        data = sorted(b.get("data") or [], key=lambda d: d["date"])
        if not data:
            continue
        total = data[-1].get("value", 0) or 0  # cumulative TERAKHIR = net value SELURUH periode, bukan di-sum lagi
        ranked.append((b.get("broker"), total, _daily_deltas(data)))
    if not ranked:
        return None
    ranked.sort(key=lambda r: r[1], reverse=True)
    top_broker, top_total, top_deltas = ranked[0]
    if top_total <= 0:
        return None  # gak ada broker yang net-BUY sepanjang periode, jangan nunjuk "bandar" ngasal

    # trend BELAKANGAN (5 hari terakhir vs 5 hari sebelumnya, semua broker
    # digabung) — ini yang jawab "market maker mulai buang atau nambah barang".
    # Pake DELTA harian (bukan raw cumulative value, lihat catatan di atas).
    daily_totals: dict[str, float] = {}
    for b in brokers:
        for d in _daily_deltas(b.get("data") or []):
            daily_totals[d["date"]] = daily_totals.get(d["date"], 0) + d["delta"]
    dates_sorted = sorted(daily_totals)
    recent_sum = sum(daily_totals[d] for d in dates_sorted[-5:])
    prior_sum = sum(daily_totals[d] for d in dates_sorted[-10:-5])
    # BUG ketemu 2026-09-02: cabang "akumulasi_melambat" tadinya cuma cek
    # `recent_sum < prior_sum * 0.5` tanpa mastiin prior_sum-nya POSITIF dulu
    # — kalau broker lagi net-JUAL di window sebelumnya (prior_sum negatif)
    # terus tambah parah jualnya (recent makin negatif), kondisi itu tetep
    # kebawa True (misal prior=-100, recent=-80 -> -80 < -50) dan salah
    # dilabelin "akumulasi melambat" padahal gak pernah ada akumulasi buat
    # "melambat" — itu distribusi yang MEMBURUK. Fix: wajib prior_sum > 0
    # dulu, "melambat" cuma masuk akal relatif dari akumulasi beneran.
    if recent_sum > 0 and recent_sum > prior_sum * 1.2:
        trend = "akumulasi_meningkat"
    elif recent_sum < 0 and prior_sum >= 0:
        trend = "distribusi_meningkat"
    elif prior_sum > 0 and recent_sum < prior_sum * 0.5:
        trend = "akumulasi_melambat"
    else:
        trend = "netral"

    top_days = sorted([d for d in top_deltas if d["delta"] > 0], key=lambda d: d["delta"], reverse=True)[:2]
    weighted_sum = weighted_vol = 0.0
    for d in top_days:
        try:
            resp = invezgo_client.get_running_trade(ticker, d["date"], limit=100)
        except Exception:
            continue
        for r in resp.get("data") or []:
            if r.get("buyer") != top_broker:
                continue
            try:
                p, v = float(r["price"]), float(r["volume"])
            except Exception:
                continue
            weighted_sum += p * v
            weighted_vol += v
    avg_price = round(weighted_sum / weighted_vol, 2) if weighted_vol > 0 else None

    # ponytail: threshold sideways 12% & konsistensi 70% heuristik arbitrer
    # (belum divalidasi statistik), tuning kalau kebanyakan false positive/negative.
    # Pake delta harian (top_deltas) — raw cumulative value SELALU > 0 abis
    # nyebrang positif sekali, itu bukan "konsisten net-buy tiap hari".
    consistency_pct = round(sum(1 for d in top_deltas if d["delta"] > 0) / len(top_deltas) * 100, 1) if top_deltas else None
    closes = [p["close"] for p in (inv.get("price") or []) if p.get("close")]
    sideways = len(closes) >= 5 and (max(closes) - min(closes)) / (sum(closes) / len(closes)) * 100 <= 12
    steady_accumulation_sideways = bool(sideways and consistency_pct is not None and consistency_pct >= 70)

    return {
        "broker": top_broker,
        "cumulative_net_value": round(top_total, 0),
        "avg_price_estimate": avg_price,
        "avg_price_sample_days": [d["date"] for d in top_days],
        "trend": trend,
        "consistency_pct": consistency_pct,
        "steady_accumulation_sideways": steady_accumulation_sideways,
    }


def _broker_defended_support(ticker: str, touch_dates: list[str]) -> dict | None:
    """Cross-check levels.py::well_defended_support (pola HARGA doang, dari
    swing-low pivot) lawan TAPE READING beneran (running-trade Invezgo) —
    user eksplisit minta: bukan cuma "harga ketahan di sini" dari price
    action, tapi BENERAN ada broker yang narik barang pas harga nyentuh
    level itu. Buat tiap touch_dates, cari broker paling DOMINAN net-buy
    hari itu dari transaksi tape reading (bukan snapshot broker_summary).
    Kalau broker YANG SAMA dominan di >=2 dari touch_dates yang dicek, itu
    konfirmasi kuat — "good entry area" beneran, bukan pola harga kebetulan.
    Cuma dicek 4 touch_dates TERBARU (bukan semua, biar gak boros kuota
    kalau support-nya udah disentuh banyak kali). None kalau Invezgo gak
    configured/gagal fetch/gak ada broker yang konsisten dominan."""
    if not invezgo_client.is_configured() or not touch_dates:
        return None
    dominant_per_date: dict[str, str] = {}
    broker_appearances: dict[str, int] = {}
    for d in touch_dates[-4:]:
        try:
            resp = invezgo_client.get_running_trade(ticker, d, limit=200)
        except Exception:
            continue
        net_by_broker: dict[str, float] = {}
        for r in resp.get("data") or []:
            try:
                vol = float(r.get("volume") or 0)
            except Exception:
                continue
            buyer, seller = r.get("buyer"), r.get("seller")
            if buyer:
                net_by_broker[buyer] = net_by_broker.get(buyer, 0) + vol
            if seller:
                net_by_broker[seller] = net_by_broker.get(seller, 0) - vol
        if not net_by_broker:
            continue
        top_broker = max(net_by_broker, key=net_by_broker.get)
        if net_by_broker[top_broker] <= 0:
            continue  # gak ada yang net-buy dominan hari itu, skip
        dominant_per_date[d] = top_broker
        broker_appearances[top_broker] = broker_appearances.get(top_broker, 0) + 1

    if not broker_appearances:
        return None
    consistent_broker = max(broker_appearances, key=broker_appearances.get)
    appearances = broker_appearances[consistent_broker]
    if appearances < 2:
        return None  # cuma dominan 1x, belum cukup buat bilang "konsisten defend"
    return {
        "broker": consistent_broker,
        "appearances": appearances,
        "of_dates_checked": len(dominant_per_date),
    }


_BANDAR_TREND_LABELS = {
    "akumulasi_meningkat": "📈 akumulasi MENINGKAT belakangan ini",
    "distribusi_meningkat": "📉 mulai ADA DISTRIBUSI belakangan ini",
    "akumulasi_melambat": "⚠️ akumulasi MELAMBAT belakangan ini",
    "netral": "netral",
}


def _format_bandar_line(bandar: dict) -> str:
    """Baris digest buat 1 hasil _detect_bandar — dipakai _send_running_positions_update
    (posisi Swing NEXUS) & _check_portfolio_risk (holdings asli user)."""
    trend_label = _BANDAR_TREND_LABELS.get(bandar["trend"], bandar["trend"])
    avg_txt = f", avg beli ~Rp{bandar['avg_price_estimate']:,.0f} (perkiraan)" if bandar["avg_price_estimate"] else ""
    line = f"   🏦 Broker {_esc(bandar['broker'])} paling akumulasi{avg_txt} — {trend_label}\n"
    if bandar.get("steady_accumulation_sideways"):
        line += f"   🎯 Sideways + akumulasi STEADY ({bandar['consistency_pct']}% hari net-buy) — breakout berpotensi lebih ringan\n"
    return line


def _send_running_positions_update() -> None:
    """Digest posisi 'running' (signal_alerts status='open') — dikirim tiap
    malam bareng Recap Malam. Beda dari _check_signal_outcomes() (yang OTOMATIS
    nutup posisi kalau harga literal nyentuh TP/SL) — ini progress report buat
    yang MASIH open: masih layak dipegang (dari berita/konteks terbaru, bukan
    cuma angka harga doang), atau ada red flag baru yang lebih urgent dari
    stop-loss teknikal. Plus broker summary OTOMATIS tiap malam per posisi
    (lihat _detect_bandar) — jawab "market maker mulai buang atau nambah
    barang", pake data Invezgo asli."""
    settings = _load_settings()
    if not settings["notif_strong_signal"]:
        log.info("_send_running_positions_update: skip, notif_strong_signal off di Settings")
        return
    try:
        res = supabase.table("signal_alerts").select("*").eq("status", "open").execute()
    except Exception:
        log.exception("_send_running_positions_update: gagal query signal_alerts")
        return
    if not res.data:
        log.info("_send_running_positions_update: NOL posisi 'open' (semua source)")
        return
    log.info(f"_send_running_positions_update: {len(res.data)} posisi open ditemuin ({[r['ticker'] for r in res.data]})")

    news_by_ticker = _recent_news_by_ticker(days=2)
    positions = []
    for row in res.data:
        try:
            hist = _get_history(row["ticker"])
            price_now = float(hist["Close"].iloc[-1])
        except Exception:
            continue
        pnl_pct = round((price_now - row["entry_price"]) / row["entry_price"] * 100, 2)
        # BUG ketemu 2026-09-02: kolom "created_at" GAK ADA di signal_alerts
        # (kolom timestamp aslinya "alerted_at", dikonfirmasi lawan skema live)
        # — .get("created_at") selalu None, entry_date SELALU jatuh ke fallback
        # 30 hari generik, gak peduli posisi baru masuk kemarin atau 3 minggu
        # lalu. Efeknya _detect_bandar di bawah selalu pake window 30 hari
        # yang gak nyambung sama entry beneran, bukan crash (silent, gak
        # ketauan dari log).
        entry_date = str(row.get("alerted_at") or "")[:10] or (today_wib() - timedelta(days=30)).isoformat()
        positions.append({
            "ticker": row["ticker"], "entry_price": row["entry_price"], "price_now": price_now,
            "pnl_pct": pnl_pct, "target": row["target"], "stop_loss": row["stop_loss"],
            "berita": news_by_ticker.get(row["ticker"]), "entry_date": entry_date,
        })
    if not positions:
        log.info("_send_running_positions_update: NOL posisi berhasil dihitung pnl-nya (semua gagal fetch harga?)")
        return

    try:
        verdicts = {v["ticker"]: v for v in assess_running_positions(positions).get("verdicts", [])}
    except Exception:
        verdicts = {}

    today = today_wib().isoformat()
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
        # broker summary otomatis per posisi open — cuma jalan kalau Invezgo
        # aktif, diem total kalau enggak (lihat _detect_bandar). Jawab "market
        # maker mulai buang atau nambah barang" + perkiraan bandar (informasi
        # doang, BUKAN dasar keputusan average-down/up — gak ada cara tau sisa
        # modal bandar dari data publik manapun)
        bandar = _detect_bandar(p["ticker"], p["entry_date"], today)
        if bandar:
            lines.append(_format_bandar_line(bandar))
    log.info(f"_send_running_positions_update: kirim update {len(positions)} posisi")
    send_alert("\n".join(lines))


async def run_entry_zone_watcher() -> None:
    """Cek zona entry TIAP 15 MENIT pas market buka (bukan nunggu
    run_morning_routine besok pagi) — biar notif "ENTRY ZONE!" nyampe deket
    real-time pas harga beneran kesentuh. Diem total di luar jam market (gak
    ada transaksi baru buat dicek, cuma buang resource). Reuse
    _check_entry_zone_touches yang sama (udah dites market-hours-aware:
    pake intraday 15m kalau ada bar hari ini, fallback EOD kalau enggak)."""
    while True:
        await asyncio.sleep(ENTRY_ZONE_WATCH_INTERVAL_SECONDS)
        if not _in_market_hours():
            continue
        try:
            _check_entry_zone_touches()
        except Exception:
            log.exception("_check_entry_zone_touches gagal (entry zone watcher)")


async def run_scheduler() -> None:
    while True:
        # tiap check DIBUNGKUS SENDIRI-SENDIRI (sebelumnya 3 dari 4 gak
        # ke-bungkus sama sekali — kalau salah satu raise exception gak
        # terduga, SELURUH loop ini mati diem-diem, gak ada lagi Swing/
        # watchlist/econ check sampe Railway di-restart manual). _check_bpjs
        # PINDAH ke run_bpjs_watcher sendiri (15 menit, bukan numpang di sini
        # 1 jam) — momentum hari ini makin cepet kedeteksi makin bagus,
        # beda dari Swing yang emang gak urgent.
        for check in (check_and_alert, _check_watchlist_alerts, _check_economic_reminders, _check_whale_alerts):
            try:
                check()
            except Exception:
                log.exception(f"{check.__name__} gagal")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


BPJS_WATCH_INTERVAL_SECONDS = 15 * 60  # user eksplisit minta lebih sering dari 1 jam — momentum
                                         # hari ini (BPJS) makin cepet kedeteksi makin awal bisa masuk,
                                         # beda dari Swing yang sengaja gak urgent (jalan off-hours doang)


async def run_bpjs_watcher() -> None:
    """Cek kandidat BPJS TIAP 15 MENIT pas market buka (sebelumnya numpang
    di run_scheduler 1 jam sekali, gak ada alasan teknis kuat kenapa harus
    sejarang itu — biaya per-cek murah, cuma ~15 ticker intraday + 1 Groq
    call kalau ada kandidat, jauh dari skala 951-ticker scan). Diem total
    di luar jam market. _check_bpjs sendiri udah idempotent (dedup MARK
    cuma abis sukses kirim, bukan abis nyoba) jadi aman dipanggil berkali-
    kali sehari tanpa alert dobel."""
    while True:
        await asyncio.sleep(BPJS_WATCH_INTERVAL_SECONDS)
        if not _in_market_hours():
            continue
        try:
            _check_bpjs()
        except Exception:
            log.exception("_check_bpjs gagal (bpjs watcher)")


def _run_night_recap_steps() -> None:
    try:
        _send_night_recap()
    except Exception:
        log.exception("_send_night_recap gagal")
    try:
        _send_running_positions_update()
    except Exception:
        log.exception("_send_running_positions_update gagal")
    try:
        _check_portfolio_risk()
    except Exception:
        log.exception("_check_portfolio_risk gagal")


async def run_night_recap() -> None:
    await _run_scheduled(NIGHT_RECAP_HOUR, 0, "night_recap", _run_night_recap_steps)


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
        log.info("_check_bsjp_screener: skip, udah ke-dedup hari ini")
        return
    settings = _load_settings()
    if not settings["notif_bsjp"]:
        log.info("_check_bsjp_screener: skip, notif_bsjp off di Settings")
        return

    try:
        pool_res = (
            supabase.table("scanner_cache").select("ticker,price,volume_ratio")
            .eq("cocok_bsjp", True).order("volume_ratio", desc=True).limit(BSJP_POOL_LIMIT)
            .execute()
        )
    except Exception:
        log.exception("_check_bsjp_screener: gagal query scanner_cache Stage-1")
        return
    if not pool_res.data:
        log.info("_check_bsjp_screener: Stage-1 kosong, gak ada ticker cocok_bsjp=True hari ini")
        return
    log.info(f"_check_bsjp_screener: Stage-1 {len(pool_res.data)} kandidat, lanjut Stage-2 intraday")

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
        # harga LIVE dari intraday yang BARU di-fetch — row["price"] itu dari
        # scanner_cache, basi kalau belum di-refresh manual hari ini (kejadian
        # nyata: user dapet call BSJP jam 15:30 tapi harganya dari refresh
        # pagi). Data intraday-nya sendiri udah fresh, tinggal dipake.
        price_now = float(hist_15m["Close"].iloc[-1])
        value_traded_idr = price_now * days[-1]["s2_volume"]
        score = bsjp_intraday_score(takeoff, value_traded_idr)
        if score > 0:
            # takeoff["price_change_pct"] itu SESI 2 DOANG (open sesi 2 jam
            # 13:30 vs sekarang) — kejadian nyata: user komplain caption
            # "harga +1.68%" padahal saham beneran udah +17% hari itu (dari
            # closing kemarin), gara-gara measuring window-nya beda jauh
            # (mulai jam 13:30, bukan dari kemarin). full_day_pct di sini
            # itung dari closing KEMARIN (days[-2]) biar caption gak
            # menyesatkan soal seberapa kuat momentumnya beneran.
            prev_close = (days[-2].get("s2_close") or days[-2].get("s1_close")) if len(days) >= 2 else None
            full_day_pct = round((price_now - prev_close) / prev_close * 100, 2) if prev_close else None
            scored.append({"ticker": ticker, "price": price_now, "takeoff": takeoff, "score": score, "full_day_pct": full_day_pct})

    if not scored:
        log.info(f"_check_bsjp_screener: Stage-2 dari {len(pool_res.data)} kandidat, NOL yang 'terbang' sesi 2 (score>0)")
        return  # sesi 2 gak ada yang "terbang" beneran — diam, jangan maksain

    scored.sort(key=lambda c: c["score"], reverse=True)
    scored = scored[:MAX_BSJP_PER_DAY]

    # BSJP dulu GAK PUNYA target/SL sama sekali — gak ke-track di signal_alerts
    # (gak muncul di History NEXUS, gak bisa dievaluasi "kejemput atau enggak").
    # Beli LANGSUNG di harga alert (bukan nunggu entry zone kayak Swing/BPJS),
    # jadi entry_low=entry_high=harga sekarang, status langsung "open".
    for c in scored:
        try:
            hist_daily = _get_history(c["ticker"])
            lv = support_resistance(hist_daily)
        except Exception:
            c["levels"] = None
            continue
        if lv["rr_ratio"] < MIN_RR_RATIO or lv["risk_pct"] > MAX_RISK_PCT or lv["reward_pct"] > MAX_REWARD_PCT:
            c["levels"] = None  # RR/levels gak masuk akal — tetep tampil alert momentumnya, TAPI gak dikasih TP/SL ngaco
            continue
        c["levels"] = lv

    lines = ["🌆 <b>BSJP — Beli Sore Jual Pagi</b>\n", "Terkonfirmasi \"terbang\" di sesi 2 hari ini:"]
    for c in scored:
        t = c["takeoff"]
        support_note = " (+ sesi 1 juga spike, pendukung)" if t.get("s1_spike_supporting") else ""
        day_pct_txt = f", harga hari ini {c['full_day_pct']:+g}%" if c["full_day_pct"] is not None else ""
        level_txt = f"\n   🎯 Target Rp{c['levels']['resistance']:,.0f} · ⛔ SL Rp{c['levels']['stop_loss']:,.0f}" if c["levels"] else ""
        lines.append(
            f"✅ <b>{_esc(c['ticker'])}</b> — Rp{c['price']:,.0f} "
            f"(sesi 2: volume {t['volume_ratio']}x rata-rata, momentum sesi 2 {t['price_change_pct']:+g}%{day_pct_txt}){support_note}{level_txt}"
        )
    lines.append("\n📌 Sinyal relatif dari data intraday hari ini, bukan indikator resmi mentor.")
    lines.append("⏰ <b>Buruan, beli maksimal jam 15:57 buat kejar BSJP hari ini — jual PAGI besok, jangan dipegang kelamaan.</b>")

    log.info(f"_check_bsjp_screener: {len(scored)} kandidat 'terbang' sesi 2, kirim alert")
    if send_alert("\n".join(lines)):
        _dedup_mark("bsjp", "screener")
        for c in scored:
            if not c["levels"]:
                log.info(f"_check_bsjp_screener: {c['ticker']} kekirim TANPA TP/SL (RR/levels gak masuk akal)")
                continue
            try:
                supabase.table("signal_alerts").insert({
                    "ticker": c["ticker"],
                    "entry_price": c["price"],
                    "entry_low": c["price"],
                    "entry_high": c["price"],
                    "target": c["levels"]["resistance"],
                    "stop_loss": c["levels"]["stop_loss"],
                    "status": "open",
                    "source": "bsjp",
                    "faktor_pendukung": {
                        "volume_ratio_s2": c["takeoff"]["volume_ratio"],
                        "price_change_pct_s2": c["takeoff"]["price_change_pct"],
                        "s1_spike_supporting": c["takeoff"].get("s1_spike_supporting"),
                        "full_day_pct": c.get("full_day_pct"),
                    },
                }).execute()
                log.info(f"_check_bsjp_screener: {c['ticker']} ke-track ke signal_alerts")
            except Exception:
                log.exception(f"_check_bsjp_screener: gagal insert signal_alerts buat {c['ticker']}")


def _advise_hold_or_exit(row: dict) -> None:
    """Pertimbangan HOLD/EXIT buat 1 posisi 'open' (row signal_alerts) yang TP/SL-nya
    belum kena tapi deadline exit strategi-nya (BSJP: pagi, BPJS: sore) udah deket.
    Insight user: broker paling banyak akumulasi = paling banyak PEGANG barang —
    volume hari ini jauh di atas rata-rata TAPI harga gak ikutan naik kuat = indikasi
    DIA yang jual. Diem total kalau Invezgo gak configured/gagal fetch broker summary
    (jangan kasih rekomendasi asal tanpa data pendukung — insight dari user sendiri:
    lebih baik gak ngasih sinyal daripada ngasih sinyal ngasal)."""
    ticker = row["ticker"]
    try:
        hist = _get_history(ticker)
        price_now = float(hist["Close"].iloc[-1])
        volume_today = float(hist["Volume"].iloc[-1])
        volume_avg20 = float(hist["Volume"].iloc[-21:-1].mean()) if len(hist) >= 21 else None
    except Exception:
        return
    if not volume_avg20 or not invezgo_client.is_configured():
        return

    today = today_wib().isoformat()
    week_ago = (today_wib() - timedelta(days=7)).isoformat()
    try:
        bs = invezgo_client.get_broker_summary(ticker, week_ago, today)
        top_broker = max(bs, key=lambda b: float(b.get("net_value") or 0)) if bs else None
    except Exception:
        top_broker = None
    if not top_broker or float(top_broker.get("net_value") or 0) <= 0:
        return  # gak ada broker yang jelas paling akumulasi, jangan nebak siapa yang "jual"

    context = {
        "ticker": ticker,
        "entry_price": row["entry_price"],
        "target": row["target"],
        "stop_loss": row["stop_loss"],
        "price_now": price_now,
        "pnl_pct": round((price_now - row["entry_price"]) / row["entry_price"] * 100, 2),
        "top_broker_code": top_broker.get("code"),
        "top_broker_name": top_broker.get("name"),
        "top_broker_net_lot": round(float(top_broker.get("net_volume") or 0) / 100),
        "volume_today": volume_today,
        "volume_avg20": round(volume_avg20),
        "volume_ratio_today": round(volume_today / volume_avg20, 2),
    }
    try:
        advice = ask_hold_or_exit(context)
    except Exception:
        return
    if not advice or advice.get("rekomendasi") not in ("hold", "exit"):
        return

    emoji = "🟢" if advice["rekomendasi"] == "hold" else "🔴"
    label = "HOLD" if advice["rekomendasi"] == "hold" else "PERTIMBANGKAN EXIT"
    sign = "+" if context["pnl_pct"] >= 0 else ""
    caption = (
        f"{emoji} <b>{label} — {_esc(ticker)}</b> ({SOURCE_LABEL_ID.get(row.get('source'), row.get('source'))})\n\n"
        f"PnL sekarang: {sign}{context['pnl_pct']}% (Rp{row['entry_price']:,.0f} → Rp{price_now:,.0f})\n\n"
        f"{_esc(advice['alasan'])}"
    )
    send_alert(caption)


SOURCE_LABEL_ID = {"bsjp": "BSJP", "bpjs": "BPJS", "swing": "Swing"}


def _check_hold_advisory(source: str, only_before_today: bool = False) -> None:
    """Kirim pertimbangan HOLD/EXIT ke SEMUA posisi 'open' dari 1 source yang
    TP/SL-nya belum kena. `only_before_today`: buat BSJP doang (dientry KEMARIN
    sore, dicek besok siang — posisi yang di-entry HARI INI sendiri belum
    relevan buat dicek, masih baru beberapa jam).

    DIAGNOSTIC (2026-09-02): belum pernah divalidasi hidup di production —
    signal_alerts total cuma 2 baris (source bpjs doang), NOL 'bsjp'/'swing'
    pernah tercatat, jadi fungsi ini kemungkinan besar SELALU nemu rows==[]
    (gak ada posisi open buat dicek), bukan berarti advisory-nya sendiri
    error. log.info biar ketauan pasti dari log, bukan nebak."""
    if _dedup_seen("hold_advisory", source):
        log.info(f"_check_hold_advisory({source}): skip, udah ke-dedup hari ini")
        return
    try:
        res = supabase.table("signal_alerts").select("*").eq("status", "open").eq("source", source).execute()
    except Exception:
        log.exception(f"_check_hold_advisory({source}): gagal query signal_alerts")
        return
    rows = res.data or []
    if only_before_today:
        today_s = today_wib().isoformat()
        rows = [r for r in rows if str(r.get("alerted_at") or "") < today_s]
    if not rows:
        log.info(f"_check_hold_advisory({source}): NOL posisi 'open' buat dicek (only_before_today={only_before_today})")
        return
    log.info(f"_check_hold_advisory({source}): {len(rows)} posisi open, kirim advisory")
    for row in rows:
        try:
            _advise_hold_or_exit(row)
        except Exception:
            log.exception(f"_check_hold_advisory({source}): _advise_hold_or_exit gagal buat {row.get('ticker')}")
            continue
    _dedup_mark("hold_advisory", source)


def _run_bsjp_screener_steps() -> None:
    try:
        _check_bsjp_screener()
    except Exception:
        log.exception("_check_bsjp_screener gagal")
    # BPJS deadline-nya SAMA jam ini (15:30, sebelum market tutup, "harus
    # dijual sore ini") — panggilan TERPISAH dari _check_bsjp_screener()
    # (fungsi itu banyak early-return kalau BSJP sendiri gak nemu apa-apa
    # hari itu — "diam lebih baik daripada maksain" — BPJS hold-check
    # harus tetep jalan walau BSJP-nya gak nemu apa-apa).
    try:
        _check_hold_advisory("bpjs")
    except Exception:
        log.exception("_check_hold_advisory(bpjs) gagal")


async def run_bsjp_screener() -> None:
    await _run_scheduled(BSJP_SCREENER_HOUR, BSJP_SCREENER_MINUTE, "bsjp_screener", _run_bsjp_screener_steps)


BSJP_HOLD_CHECK_HOUR = 12  # midday break IDX (12:00-13:30) — BSJP HARUSNYA
BSJP_HOLD_CHECK_MINUTE = 5  # udah dijual PAGI, kalau siang gini masih 'open' berarti belum resolve


async def run_bsjp_hold_check() -> None:
    await _run_scheduled(
        BSJP_HOLD_CHECK_HOUR, BSJP_HOLD_CHECK_MINUTE, "bsjp_hold_check",
        lambda: _check_hold_advisory("bsjp", only_before_today=True),
    )


MARKET_OPEN = time(9, 0)
MARKET_CLOSE = time(15, 50)

SCANNER_REFRESH_HOUR = 16  # 10 menit abis market tutup — cukup buffer settlement,
SCANNER_REFRESH_MINUTE = 0  # tapi masih hari ini biar breakout hari ini kepake off-hours malam ini


async def run_scanner_refresh() -> None:
    """scanner_cache SEBELUMNYA cuma di-refresh manual (tombol Scanner) — ketauan
    pernah basi 7 HARI gara-gara gak ada yang klik, padahal ini SUMBER pool
    kandidat Swing/BPJS (_gather_candidates query technical_score/
    breakout_confirmed dari sini) — breakout minggu ini gak pernah kedeteksi
    selama itu. Sekarang auto-refresh 1x/hari abis market tutup (reuse
    refresh_scanner_data yang udah ada throttle 5-worker + retry rate-limit,
    sama logic kayak tombol manual, cuma dipanggil otomatis)."""
    await _run_scheduled(SCANNER_REFRESH_HOUR, SCANNER_REFRESH_MINUTE, "scanner_refresh", _run_scanner_refresh_step)


def _run_scanner_refresh_step() -> None:
    if not is_trading_day(today_wib()):
        return
    result = refresh_scanner_data()
    log.info(f"scanner_cache auto-refresh: {result['refreshed']} ok, {result['failed']} gagal")


FUNDAMENTALS_REFRESH_HOUR = 16
FUNDAMENTALS_REFRESH_MINUTE = 30  # 30 menit abis run_scanner_refresh — biar ticker BARU (baru
                                   # ke-refresh price-nya jam 16:00) ikut ke-cover fundamentals-nya juga


async def run_fundamentals_refresh() -> None:
    """Sama gap kayak scanner_cache (manual-trigger doang) — tapi cadence-nya
    MINGGUAN bukan harian, PER/PBV/dividend/market_cap emang jarang berubah
    harian (beda dari breakout/harga yang harus fresh tiap hari), harian
    cuma boros .info call (lebih berat dari .history()) buat data yang gak
    berubah. Senin 16:30 WIB — abis weekend, mulai minggu baru."""
    await _run_scheduled(
        FUNDAMENTALS_REFRESH_HOUR, FUNDAMENTALS_REFRESH_MINUTE, "fundamentals_refresh",
        _run_fundamentals_refresh_step, weekday=0,  # Senin
    )


def _run_fundamentals_refresh_step() -> None:
    result = refresh_fundamentals_data()
    log.info(f"fundamentals auto-refresh: {result['refreshed']} ok, {result['failed']} gagal")

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
    channel_calls_by_ticker = _recent_trade_calls_by_ticker()

    try:
        scan_res = (
            supabase.table("scanner_cache").select("ticker,price,volume_ratio")
            .gte("volume_ratio", 1.3).order("volume_ratio", desc=True).limit(pool_limit)
            .execute()
        )
        scan_by_ticker = {r["ticker"]: r for r in scan_res.data}
    except Exception:
        scan_by_ticker = {}

    # "buy on weakness" — SAMA jalur alternatif kayak Swing (support berkali-
    # kali disentuh & mantul), user eksplisit minta diperluas ke BPJS juga.
    # Query TERPISAH tanpa gate volume_ratio (kandidat ini JUSTRU lagi tenang/
    # gak rame volume, itu poinnya).
    try:
        support_res = (
            supabase.table("scanner_cache").select("ticker,price,volume_ratio")
            .eq("cocok_buy_on_weakness", True).limit(pool_limit).execute()
        )
        support_defended_tickers = {r["ticker"] for r in support_res.data}
    except Exception:
        support_defended_tickers = set()

    pool = set(scan_by_ticker) | set(mentor_by_ticker) | support_defended_tickers
    session = "s2" if _now_wib().time() >= time(13, 0) else "s1"

    candidates = []
    for ticker in pool:
        mentor = mentor_by_ticker.get(ticker)
        momentum_score = 0.0
        try:
            # harga LIVE dari intraday, BUKAN scanner_cache (basi kalau belum
            # di-refresh manual hari ini — sama bug yang ketemu di BSJP)
            hist_15m = _get_history_intraday(ticker)
            price = float(hist_15m["Close"].iloc[-1])
            days = daily_session_stats(hist_15m)
            takeoff = session_takeoff(days, session=session)
            if takeoff is not None:
                value_traded_idr = price * days[-1][f"{session}_volume"]
                momentum_score = bpjs_momentum_score(takeoff, value_traded_idr)
        except Exception:
            pass
        # trend/adx/bollinger/ma_alignment/buy_on_weakness — konteks TA sama
        # kayak Swing, dari hist HARIAN (bukan intraday), user eksplisit minta
        # diperluas ke BPJS juga. Fetch TERPISAH dari intraday di atas (beda
        # granularitas), gagal diem-diem kalau yfinance error.
        trend = adx_val = bollinger = ma_align = buy_on_weakness = chart_pattern = None
        try:
            hist_daily = _get_history(ticker)
            price_daily_now = float(hist_daily["Close"].iloc[-1])
            # determine_trend() itung golden/death cross dari MA50/MA200 —
            # BUG ketemu (code review): dulu dikasih hist_daily langsung
            # (period="2mo", ~40 bar), MA-nya jadi rata-rata SELURUH window
            # 2 bulan itu, BUKAN sinyal jangka panjang kayak yang di-dokumenin
            # ke Groq ("posisi harga vs MA50/MA200 MINGGUAN") — bisa kebalik
            # arah dari trend jangka panjang beneran. Fix: reuse pola sama
            # kayak Swing (_apply_smart_tp) — mingguan "max", fallback ke
            # hist_daily kalau weekly-nya gagal/kurang dari 50 bar.
            try:
                weekly = yf.Ticker(f"{ticker}.JK").history(period="max", interval="1wk", auto_adjust=False).dropna(subset=["Close"])
            except Exception:
                weekly = None
            trend = determine_trend(weekly if weekly is not None and len(weekly) >= 50 else hist_daily)
            adx_val = adx(hist_daily)
            bollinger = bollinger_signal(hist_daily)
            ma5 = float(hist_daily["Close"].tail(5).mean())
            ma10 = float(hist_daily["Close"].tail(10).mean())
            ma20 = float(hist_daily["Close"].tail(20).mean())
            ma_align = ma_alignment(ma5, ma10, ma20)
            buy_on_weakness = well_defended_support(hist_daily, price_daily_now)
            chart_pattern = detect_chart_pattern(hist_daily)
        except Exception:
            pass
        if momentum_score <= 0 and not mentor and not buy_on_weakness:
            continue
        candidates.append({
            "ticker": ticker,
            "momentum_score": momentum_score,
            "session": session,
            "mentor_call": ({"status": mentor["status"], "buy_price": mentor["buy_price"]} if mentor else None),
            "berita": news_by_ticker.get(ticker),
            "channel_calls": channel_calls_by_ticker.get(ticker),
            "trend": trend,
            "adx": adx_val,
            "bollinger": bollinger,
            "ma_alignment": ma_align,
            "buy_on_weakness": buy_on_weakness,
            "chart_pattern": chart_pattern,
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
    if not settings["notif_bpjs"]:
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
    if (pick.get("conviction") or 0) < MIN_CONVICTION_BPJS:
        return  # Groq sendiri gak cukup yakin — skip daripada kirim call ragu-ragu

    candidate = next((c for c in candidates if c["ticker"] == ticker), None)
    if candidate is None or (
        candidate["momentum_score"] <= 0 and not candidate.get("mentor_call") and not candidate.get("buy_on_weakness")
    ):
        return  # jaga-jaga Groq halusinasi ticker di luar pool / ngelanggar instruksi sendiri

    try:
        hist = _get_history(ticker)
        price_now = float(hist["Close"].iloc[-1])
        levels = support_resistance(hist)
        # sama bug/fix kayak Swing (_gather_candidates) — kalau candidate lolos
        # lewat jalur buy_on_weakness, SL/support HARUS berbasis level itu,
        # bukan trailing-20-hari support_resistance() biasa yang gak nyambung
        # sama narasi caption-nya. Override SEBELUM gate RR di bawah.
        apply_buy_on_weakness_support(levels, price_now, candidate.get("buy_on_weakness"))
    except Exception:
        return
    if levels["risk_pct"] > MAX_RISK_PCT or levels["reward_pct"] > MAX_REWARD_PCT:
        return  # SL/TP kejauhan dari harga sekarang, sama sanity check kayak Swing — jangan kirim angka ngaco
    if levels["rr_ratio"] < MIN_RR_RATIO:
        return  # kejadian nyata: PGAS TP +0.65% (RR "Buruk") lolos kirim — biaya beli+jual
        # broker retail Indonesia aja udah ~0.5-0.7% roundtrip, TP situ abis kegerus fee doang.
        # Groq milih ticker SEBELUM levels dihitung (gak pernah liat RR), jadi guard Python di
        # sini WAJIB, bukan optional — sama pola kayak MAX_RISK/MAX_REWARD di atas.

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
            "faktor_pendukung": {"faktor": pick.get("faktor_pendukung", []), "conviction": pick.get("conviction")},
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
            jenis = e.get("jenis")
            label = f" — {_esc(jenis)}" if jenis and jenis != "lainnya" else ""
            line = f"• {_esc(e.get('saham'))}{label} · {_esc(e.get('tanggal'))}"
            if e.get("detail"):
                line += f"\n  {_esc(e['detail'])}"
            lines.append(line)

    # BUG ketemu 2026-09-02 (user lapor Sarapan Pagi gak kekirim): field
    # rekomendasi diganti shape-nya waktu redesign ("saham"/"alasan" ->
    # {ticker, entry_price, target, stop_loss, call_oleh}, tempelan langsung
    # dari signal_alerts, lihat _generate_briefing), tapi formatter DI SINI
    # gak ikut kesentuh — .get("saham")/.get("alasan") selalu None, _esc(None)
    # (html.escape) crash tiap ada rekomendasi, ke-swallow diem-diem sama
    # try/except run_pre_market_briefing (gak ada log yang kebaca gampang).
    rekomendasi = briefing.get("rekomendasi") or []
    if rekomendasi:
        lines.append("\n⭐ <b>Watchlist Hari Ini</b>")
        for r in rekomendasi:
            lines.append(
                f"• <b>{_esc(r.get('ticker'))}</b> ({_esc(r.get('call_oleh'))}) — "
                f"Entry Rp{r['entry_price']:,.0f} · TP Rp{r['target']:,.0f} · SL Rp{r['stop_loss']:,.0f}"
            )

    send_alert("\n".join(lines))


async def run_pre_market_briefing() -> None:
    await _run_scheduled(PRE_MARKET_HOUR, PRE_MARKET_MINUTE, "pre_market_briefing", _send_morning_briefing)


async def run_telegram_channel_listener() -> None:
    """Long-poll Telegram getUpdates — 2 concern beda DIGABUNG di 1 loop (bukan
    2 poller terpisah) karena Telegram NOLAK >1 getUpdates paralel per bot
    token (409 Conflict):
    1. callback_query — tombol Terima/Tolak rotasi Swing (_propose_rotation).
       SELALU diproses, gak peduli TELEGRAM_CHANNEL_IDS di-setup atau enggak.
    2. channel_post dari TELEGRAM_CHANNEL_IDS, forward ke submit_intel()
       (manggil function-nya langsung, bukan HTTP ke diri sendiri — sama
       proses). Di-skip kalau TELEGRAM_CHANNEL_IDS kosong di .env."""
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

            callback = u.get("callback_query")
            if callback:
                try:
                    _handle_rotation_callback(callback)
                except Exception:
                    log.exception("_handle_rotation_callback gagal")
                continue

            if not TELEGRAM_CHANNEL_IDS:
                continue
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


def _run_morning_routine_steps() -> None:
    # tiap step DIBUNGKUS SENDIRI-SENDIRI (bukan 1 try/except ngelingkupin
    # semua) — biar 1 step gagal (misal refresh_mentor_calls network error)
    # gak nge-block step selanjutnya (misal _check_signal_outcomes yang
    # nutup posisi trading, itu lebih penting daripada mentor sheet)
    for step in (refresh_mentor_calls, _generate_briefing, _check_entry_zone_touches,
                 _check_signal_outcomes):
        try:
            step()
        except Exception:
            log.exception(f"{step.__name__} gagal (morning routine)")


async def run_morning_routine() -> None:
    """Sekali tiap hari jam MORNING_ROUTINE_HOUR: refresh mentor_calls dari
    Google Sheets, terus sintesis daily_briefing dari intel yang numpuk
    beberapa hari terakhir — biar pas dibuka paginya udah fresh, gak nunggu."""
    await _run_scheduled(MORNING_ROUTINE_HOUR, 0, "morning_routine", _run_morning_routine_steps)


WEEKLY_POSTMORTEM_HOUR = 21  # Minggu malam WIB — abis 1 minggu trading penuh (Sen-Jum) kelar


def _send_weekly_postmortem() -> None:
    """Rekap SEMUA posisi Swing+BPJS yang closed 7 hari terakhir (BSJP gak
    ikut — masih alert-only, gak ada tracking outcome). Groq nyari pola dari
    data asli, bukan nge-judge tiap trade — kalau data dikit/gak ada pola
    jelas, dia jujur bilang gitu (lihat prompt di groq_client.py)."""
    settings = _load_settings()
    if not settings["notif_weekly_postmortem"]:
        return

    since = (today_wib() - timedelta(days=7)).isoformat()
    try:
        res = (
            supabase.table("signal_alerts").select("ticker,source,status,outcome_pct,closed_at")
            .gte("closed_at", since).in_("status", ["tp_hit", "sl_hit", "timeout"]).execute()
        )
    except Exception:
        return
    rows = res.data

    if not rows:
        send_alert("📋 <b>Weekly Postmortem</b>\n\nGak ada posisi yang closed minggu ini.")
        return

    wins = [r for r in rows if r["status"] == "tp_hit"]
    losses = [r for r in rows if r["status"] == "sl_hit"]
    timeouts = [r for r in rows if r["status"] == "timeout"]
    win_rate = round(len(wins) / len(rows) * 100, 1)

    summary = {
        "total": len(rows), "tp_hit": len(wins), "sl_hit": len(losses), "timeout": len(timeouts),
        "win_rate_pct": win_rate,
        "detail": [{"ticker": r["ticker"], "source": r.get("source") or "swing", "status": r["status"],
                    "outcome_pct": r["outcome_pct"]} for r in rows],
    }
    try:
        insight = generate_postmortem(summary)
    except Exception:
        insight = {}

    text = (
        f"📋 <b>Weekly Postmortem</b>\n\n"
        f"{len(rows)} posisi closed minggu ini — {len(wins)} TP, {len(losses)} SL, "
        f"{len(timeouts)} timeout (win rate {win_rate}%).\n\n"
        f"🔍 <b>Pola:</b> {_esc(insight.get('pola', 'Gagal generate insight minggu ini.'))}\n\n"
        f"💡 <b>Saran:</b> {_esc(insight.get('saran', '-'))}"
    )
    send_alert(text)


async def run_weekly_postmortem() -> None:
    await _run_scheduled(WEEKLY_POSTMORTEM_HOUR, 0, "weekly_postmortem", _send_weekly_postmortem, weekday=6)
