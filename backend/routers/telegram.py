from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from telegram_bot import get_latest_chat_id, get_saved_chat_id, save_chat_id, send_message, get_bot_username
from config import TELEGRAM_BOT_TOKEN

router = APIRouter()


@router.get("/status")
def status():
    if not TELEGRAM_BOT_TOKEN:
        return {"connected": False, "error": "TELEGRAM_BOT_TOKEN belum diisi di .env"}
    chat_id = get_saved_chat_id()
    return {"connected": chat_id is not None, "chat_id": chat_id, "bot_username": get_bot_username()}


@router.get("/detect")
def detect_chat_id():
    """Dipanggil setelah user kirim /start ke bot — cari chat_id dari pesan terakhir yang diterima."""
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN belum diisi di .env")
    chat_id = get_latest_chat_id()
    if not chat_id:
        raise HTTPException(status_code=404, detail="Belum ada pesan masuk. Kirim /start ke bot dulu, baru coba lagi.")
    return {"chat_id": chat_id}


class ConnectInput(BaseModel):
    chat_id: str


@router.post("/connect")
def connect(payload: ConnectInput):
    save_chat_id(payload.chat_id)
    send_message(payload.chat_id, "✅ NEXUS berhasil terhubung ke Telegram kamu. Alert sinyal saham akan dikirim ke sini.")
    return {"connected": True, "chat_id": payload.chat_id}


@router.post("/test")
def test_alert():
    chat_id = get_saved_chat_id()
    if not chat_id:
        raise HTTPException(status_code=400, detail="Belum ada Telegram yang terhubung")
    ok = send_message(chat_id, "🔔 Test alert dari NEXUS — kalau kamu baca ini, koneksi Telegram kamu berfungsi normal.")
    if not ok:
        raise HTTPException(status_code=502, detail="Gagal kirim pesan — cek token bot atau chat_id")
    return {"sent": True}