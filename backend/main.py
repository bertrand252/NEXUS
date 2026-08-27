import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from config import supabase, FRONTEND_ORIGINS
from auth_guard import require_auth
from routers import scanner, intel, portfolio, market_events, journal, telegram, watchlist, mentor_calls
from scheduler import run_scheduler, run_morning_routine


@asynccontextmanager
async def lifespan(app: FastAPI):
    """2 task background: run_scheduler (cek scanner_cache tiap 15 menit, kirim
    alert Telegram otomatis buat signal Strong baru) dan run_morning_routine
    (jam 06:00 tiap hari, refresh mentor_calls sebelum market open). Lihat
    scheduler.py."""
    tasks = [asyncio.create_task(run_scheduler()), asyncio.create_task(run_morning_routine())]
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