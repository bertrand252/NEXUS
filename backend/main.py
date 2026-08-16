from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from config import supabase, FRONTEND_ORIGINS
from routers import scanner, intel, portfolio, market_events

app = FastAPI(title="NEXUS API")

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


app.include_router(scanner.router, prefix="/scanner", tags=["scanner"])
app.include_router(intel.router, prefix="/intel", tags=["intel"])
app.include_router(portfolio.router, prefix="/portfolio", tags=["portfolio"])
app.include_router(market_events.router, prefix="/market-events", tags=["market-events"])

# ---------------------------------------------------------------------------
# Next router: journal (Trading Journal — belum digarap)
# ---------------------------------------------------------------------------