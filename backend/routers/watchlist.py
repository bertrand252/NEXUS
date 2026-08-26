from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import supabase

router = APIRouter()


class AddTicker(BaseModel):
    ticker: str


@router.get("")
def list_watchlist():
    res = supabase.table("watchlist").select("*").order("created_at").execute()
    return {"data": res.data}


@router.post("")
def add_ticker(payload: AddTicker):
    ticker = payload.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker gak boleh kosong")

    res = supabase.table("watchlist").upsert({"ticker": ticker}, on_conflict="ticker").execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Gagal simpan ticker ke Supabase")
    return {"data": res.data}


@router.delete("/{ticker}")
def remove_ticker(ticker: str):
    supabase.table("watchlist").delete().eq("ticker", ticker.upper()).execute()
    return {"ok": True}
