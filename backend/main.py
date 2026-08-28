import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from config import supabase, FRONTEND_ORIGINS
from auth_guard import require_auth
from routers import scanner, intel, portfolio, market_events, journal, telegram, watchlist, mentor_calls, daily_briefing, signal_track
from scheduler import (
    run_scheduler,
    run_morning_routine,
    run_telegram_channel_listener,
    run_telegram_scrape_listener,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """4 task background (lihat scheduler.py buat detail tiap fungsi):
    run_scheduler, run_morning_routine, run_telegram_channel_listener (channel
    yang bot-nya admin), run_telegram_scrape_listener (channel yang cuma
    di-subscribe biasa, di-scrape dari preview publik). Semua skip diem-diem
    kalau config terkait kosong."""
    tasks = [
        asyncio.create_task(run_scheduler()),
        asyncio.create_task(run_morning_routine()),
        asyncio.create_task(run_telegram_channel_listener()),
        asyncio.create_task(run_telegram_scrape_listener()),
    ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="NEXUS API", lifespan=lifespan)

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