from fastapi import APIRouter
from forex_factory import get_forex_events
import invezgo_client

router = APIRouter()


@router.get("")
def list_market_events():
    events = get_forex_events()
    return {
        "data": events,
        "warning": None if events else "Gak bisa ambil data dari Forex Factory saat ini — coba lagi nanti.",
    }


@router.get("/corporate")
def list_corporate_actions():
    """"Special Events asli" — corporate action (RUPS/dividen) dari Invezgo,
    gantiin ekstraksi dari teks berita (Groq) yang sekarang masih ⚠️ Parsial
    (gak ada crosscheck ke sumber resmi). configured=False + data kosong kalau
    INVEZGO_API_KEY belum diisi. Shape payload DIVERIFIKASI lawan API asli
    (2026-09-01): RUPS_SCHEDULE {Date,Venue,Remark,Result,DateStr,RecDate,TimeStr},
    DIVIDEND {ExDate,Status,CumDate,RecDate,DistDate,PaymentType,TotalDividen,
    DividenPerShare} — beda struktur per-type, jangan disamain. Limit 20/page,
    API gak dukung filter tanggal, jadi frontend yang sort+filter ke mendatang."""
    if not invezgo_client.is_configured():
        return {"configured": False, "rups": None, "dividend": None}

    result = {"configured": True}
    try:
        result["rups"] = invezgo_client.get_calendar(action_type="RUPS_SCHEDULE", limit=20)
    except Exception:
        result["rups"] = None
    try:
        result["dividend"] = invezgo_client.get_calendar(action_type="DIVIDEND", limit=20)
    except Exception:
        result["dividend"] = None
    return result