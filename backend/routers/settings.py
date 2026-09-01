from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import supabase

router = APIRouter()

DEFAULTS = {
    "alert_threshold": 50,
    "notif_strong_signal": True,   # Swing (breakout+volume, 1-2/minggu)
    "notif_daily_recap": True,     # night recap + running positions update
    "notif_economic_events": True,
    "notif_portfolio_risk": True,
    "notif_watchlist": True,       # dulu nyambung ke notif_strong_signal, sekarang independen
    "notif_bsjp": True,
    "notif_bpjs": True,
    "notif_weekly_postmortem": True,
    "notif_whale_alert": True,     # transaksi abnormal gede (running-trade), polling watchlist tiap jam pas market buka
}


class SettingsInput(BaseModel):
    alert_threshold: int
    notif_strong_signal: bool
    notif_daily_recap: bool
    notif_economic_events: bool
    notif_portfolio_risk: bool
    notif_watchlist: bool = True
    notif_bsjp: bool = True
    notif_bpjs: bool = True
    notif_weekly_postmortem: bool = True
    notif_whale_alert: bool = True


@router.get("")
def get_settings():
    try:
        res = supabase.table("app_settings").select("*").eq("id", 1).limit(1).execute()
    except Exception:
        return {**DEFAULTS, "warning": "Tabel app_settings belum ada — pake default sementara, jalanin SQL setup dulu di Supabase."}
    if not res.data:
        return {**DEFAULTS, "warning": None}
    row = res.data[0]
    return {**DEFAULTS, **{k: row[k] for k in DEFAULTS if k in row}, "warning": None}


@router.put("")
def update_settings(payload: SettingsInput):
    data = payload.model_dump()
    data["id"] = 1
    try:
        supabase.table("app_settings").upsert(data).execute()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gagal simpan setting — tabel app_settings belum di-setup? ({e})")
    return {**data, "warning": None}
