from fastapi import APIRouter
from config import supabase
from mentor_sheet import fetch_mentor_calls

router = APIRouter()


@router.get("")
def get_mentor_calls():
    """Baca dari cache (mentor_calls table), plus cross-reference signal NEXUS
    sendiri (dari scanner_cache) buat tiap ticker — biar kelihatan apa NEXUS
    juga ngeflag saham yang sama."""
    try:
        res = supabase.table("mentor_calls").select("*").order("id").execute()
    except Exception:
        return {"data": [], "warning": "Cache masih kosong — jalanin POST /mentor-calls/refresh dulu."}
    if not res.data:
        return {"data": [], "warning": "Cache masih kosong — jalanin POST /mentor-calls/refresh dulu."}

    signal_map = {}
    tickers = list({r["ticker"] for r in res.data})
    if tickers:
        try:
            scan = supabase.table("scanner_cache").select("ticker,total_score,signal").in_("ticker", tickers).execute()
            signal_map = {r["ticker"]: {"total_score": r["total_score"], "signal": r["signal"]} for r in scan.data}
        except Exception:
            pass

    data = [{**r, "nexus_signal": signal_map.get(r["ticker"])} for r in res.data]
    return {"data": data, "warning": None}


@router.post("/refresh")
def refresh_mentor_calls():
    """Tarik ulang dari Google Sheets, timpa total (sheet mentor itu source of
    truth buat call yang lagi aktif, bukan log historis yang perlu di-append)."""
    calls = fetch_mentor_calls()
    supabase.table("mentor_calls").delete().neq("id", 0).execute()
    if calls:
        supabase.table("mentor_calls").insert(calls).execute()
    return {"refreshed": len(calls)}
