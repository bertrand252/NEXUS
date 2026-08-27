"""
Scheduler background sederhana: tiap CHECK_INTERVAL_SECONDS, cek scanner_cache
buat ticker Strong dengan score tertinggi (cuma #1, bukan semua), rangkum
alasan lewat Groq, render chart + garis support/resistance, kirim ke Telegram
sebagai foto. Jalan di process yang sama kayak FastAPI lewat asyncio.create_task
— gak butuh cron/Celery/proses terpisah, paling sederhana buat single-user tool.
"""
import asyncio
from datetime import date, datetime, timedelta
from config import supabase
from routers.scanner import _get_history
from routers.mentor_calls import refresh_mentor_calls
from routers.daily_briefing import _generate_briefing
from levels import support_resistance
from chart_render import render_chart
from groq_client import analyze_alert
from telegram_bot import send_alert_photo, send_alert, get_channel_updates
from telegram_scrape import fetch_channel_posts
from routers.intel import submit_intel, IntelInput
from config import TELEGRAM_CHANNEL_IDS, TELEGRAM_SCRAPE_CHANNELS

TELEGRAM_SCRAPE_INTERVAL_SECONDS = 10 * 60  # preview publik gak realtime kayak bot API, polling 10 menit cukup

_last_seen_post_id: dict[str, int] = {}

MORNING_ROUTINE_HOUR = 6  # 06:00 waktu lokal server, sebelum market IDX buka jam 09:00

CHECK_INTERVAL_SECONDS = 60 * 60  # 1 jam — dedup per-ticker jadi gak ngaruh ke spam, cuma ke seberapa cepet nyampe

_alerted_today: set[str] = set()
_invalidated_today: set[str] = set()
_alerted_date: date | None = None


def _reset_if_new_day() -> None:
    global _alerted_date, _alerted_today, _invalidated_today
    today = date.today()
    if _alerted_date != today:
        _alerted_date = today
        _alerted_today = set()
        _invalidated_today = set()
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


def _build_caption(ticker: str, total_score: int, levels: dict, reasoning: dict) -> str:
    return (
        f"🔴 STRONG SIGNAL — {ticker} (score {total_score}/100)\n\n"
        f"Kenapa kuat: {reasoning.get('alasan_strong', '-')}\n\n"
        f"Entry: Rp{levels['entry_low']:,.0f} - Rp{levels['entry_high']:,.0f}\n"
        f"Stop loss: Rp{levels['stop_loss']:,.0f}\n"
        f"Risk: {levels['risk_pct']}%\n"
        f"Alasan risk: {reasoning.get('alasan_risk', '-')}"
    )


def check_and_alert() -> None:
    _reset_if_new_day()
    _check_invalidated()

    try:
        res = (
            supabase.table("scanner_cache")
            .select("ticker,total_score,signal,volume_score,price_score,accumulation_score,technical_score")
            .eq("signal", "Strong")
            .order("total_score", desc=True)
            .execute()
        )
    except Exception:
        return  # scanner_cache belum ke-refresh / Supabase lagi bermasalah, coba lagi interval berikutnya

    candidates = [r for r in res.data if r["ticker"] not in _alerted_today]
    if not candidates:
        return

    top = candidates[0]  # cuma kirim #1, bukan semua ticker Strong
    ticker = top["ticker"]

    try:
        hist = _get_history(ticker)
        levels = support_resistance(hist)
        chart_png = render_chart(ticker, hist, levels["support"], levels["resistance"])
        score_breakdown = {
            "volume_score": top["volume_score"],
            "price_score": top["price_score"],
            "accumulation_score": top["accumulation_score"],
            "technical_score": top["technical_score"],
        }
        reasoning = analyze_alert(ticker, score_breakdown, levels)
    except Exception:
        return  # gagal di yfinance/Groq/render — coba lagi interval berikutnya, jangan tandain alerted

    caption = _build_caption(ticker, top["total_score"], levels, reasoning)
    if not send_alert_photo(chart_png, caption):
        return  # Telegram belum di-connect di Settings, atau gagal kirim

    _alerted_today.add(ticker)


async def run_scheduler() -> None:
    while True:
        check_and_alert()
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


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
