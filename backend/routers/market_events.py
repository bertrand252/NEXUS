from fastapi import APIRouter
from forex_factory import get_forex_events

router = APIRouter()


@router.get("")
def list_market_events():
    events = get_forex_events()
    return {
        "data": events,
        "warning": None if events else "Gak bisa ambil data dari Forex Factory saat ini — coba lagi nanti.",
    }