import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from config import supabase, FRONTEND_ORIGINS
from auth_guard import require_auth
from rate_limit import limiter
from routers import scanner, intel, portfolio, market_events, journal, telegram, watchlist, mentor_calls, daily_briefing, signal_track, settings
from scheduler import (
    run_scheduler,
    run_morning_routine,
    run_night_recap,
    run_pre_market_briefing,
    run_bsjp_screener,
    run_bsjp_hold_check,
    run_weekly_postmortem,
    run_telegram_channel_listener,
    run_telegram_scrape_listener,
    run_entry_zone_watcher,
    run_scanner_refresh,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """11 task background (lihat scheduler.py buat detail tiap fungsi):
    run_scheduler (Swing, jam market tutup), run_morning_routine, run_pre_market_briefing
    (08:45, "sarapan pagi" ke Telegram), run_bsjp_screener (15:30, screener BSJP +
    pertimbangan hold/exit BPJS yang masih open), run_bsjp_hold_check (12:00,
    pertimbangan hold/exit BSJP yang di-entry kemarin & masih open), run_night_recap,
    run_weekly_postmortem (Minggu 21:00, rekap Swing+BPJS seminggu),
    run_telegram_channel_listener (channel yang bot-nya admin),
    run_telegram_scrape_listener (channel yang cuma di-subscribe biasa, di-scrape
    dari preview publik), run_entry_zone_watcher (tiap 15 menit pas market buka,
    notif ENTRY ZONE real-time — bukan nunggu run_morning_routine besok pagi),
    run_scanner_refresh (16:00 WIB abis market tutup, auto-refresh scanner_cache
    — sebelumnya cuma manual, sempet basi 7 hari). Semua skip diem-diem kalau
    config/setting terkait kosong/off."""
    tasks = [
        asyncio.create_task(run_scheduler()),
        asyncio.create_task(run_morning_routine()),
        asyncio.create_task(run_pre_market_briefing()),
        asyncio.create_task(run_bsjp_screener()),
        asyncio.create_task(run_bsjp_hold_check()),
        asyncio.create_task(run_night_recap()),
        asyncio.create_task(run_weekly_postmortem()),
        asyncio.create_task(run_telegram_channel_listener()),
        asyncio.create_task(run_telegram_scrape_listener()),
        asyncio.create_task(run_entry_zone_watcher()),
        asyncio.create_task(run_scanner_refresh()),
    ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="NEXUS API", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)  # WAJIB ditambahin SEBELUM CORS (middleware LIFO — kebalik urutannya)

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"status": "ok", "service": "NEXUS API"}


@app.get("/health/supabase")
def health_supabase():
    """Confirms the backend can actually reach Supabase — hit this after setup."""
    try:
        # cheap query: list tables via a lightweight select, adjust table name once you have one
        supabase.table("daily_market_intel").select("id").limit(1).execute()
        return {"supabase": "connected"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Supabase not reachable: {e}")


_auth = [Depends(require_auth)]

app.include_router(scanner.router, prefix="/scanner", tags=["scanner"], dependencies=_auth)
app.include_router(intel.router, prefix="/intel", tags=["intel"], dependencies=_auth)
app.include_router(portfolio.router, prefix="/portfolio", tags=["portfolio"], dependencies=_auth)
app.include_router(market_events.router, prefix="/market-events", tags=["market-events"], dependencies=_auth)
app.include_router(journal.router, prefix="/journal", tags=["journal"], dependencies=_auth)
app.include_router(telegram.router, prefix="/telegram", tags=["telegram"], dependencies=_auth)
app.include_router(watchlist.router, prefix="/watchlist", tags=["watchlist"], dependencies=_auth)
app.include_router(mentor_calls.router, prefix="/mentor-calls", tags=["mentor-calls"], dependencies=_auth)
app.include_router(daily_briefing.router, prefix="/daily-briefing", tags=["daily-briefing"], dependencies=_auth)
app.include_router(signal_track.router, prefix="/signal-track", tags=["signal-track"], dependencies=_auth)
app.include_router(settings.router, prefix="/settings", tags=["settings"], dependencies=_auth)