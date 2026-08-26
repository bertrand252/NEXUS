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
from levels import support_resistance
from chart_render import render_chart
from groq_client import analyze_alert
from telegram_bot import send_alert_photo, send_alert

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


async def run_morning_routine() -> None:
    """Sekali tiap hari jam MORNING_ROUTINE_HOUR, refresh mentor_calls dari
    Google Sheets — biar pas dibuka paginya udah fresh, gak nunggu fetch."""
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
