"""
Wrapper tipis buat Telegram Bot API. Single-user (skripsi ini gak butuh multi-user),
jadi chat_id disimpen 1 baris aja di Supabase, bukan per-akun.
"""
import requests
from config import supabase, TELEGRAM_BOT_TOKEN

API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def get_bot_username() -> str | None:
    res = requests.get(f"{API_BASE}/getMe", timeout=8)
    if not res.ok:
        return None
    return res.json().get("result", {}).get("username")


def get_latest_chat_id() -> str | None:
    """Ambil chat_id dari update terakhir yang diterima bot. User harus kirim
    pesan apa aja ke bot dulu (biasanya /start) sebelum ini dipanggil."""
    res = requests.get(f"{API_BASE}/getUpdates", timeout=8)
    res.raise_for_status()
    updates = res.json().get("result", [])
    if not updates:
        return None
    return str(updates[-1]["message"]["chat"]["id"])


def get_channel_updates(offset: int | None = None, timeout: int = 25) -> list[dict]:
    """Long-poll getUpdates buat channel_post doang. offset biar Telegram gak
    ngirim ulang update yang udah di-ack (dipake scheduler.py)."""
    params = {"timeout": timeout, "allowed_updates": '["channel_post"]'}
    if offset is not None:
        params["offset"] = offset
    res = requests.get(f"{API_BASE}/getUpdates", params=params, timeout=timeout + 10)
    res.raise_for_status()
    return res.json().get("result", [])


def get_chat_id_by_username(username: str) -> str | None:
    """Resolve @username channel ke chat_id numerik — dipake sekali pas setup,
    biar gak perlu nebak-nebak ID channel manual."""
    username = username if username.startswith("@") else f"@{username}"
    res = requests.get(f"{API_BASE}/getChat", params={"chat_id": username}, timeout=8)
    if not res.ok:
        return None
    return str(res.json().get("result", {}).get("id"))


def get_saved_chat_id() -> str | None:
    res = supabase.table("telegram_settings").select("chat_id").limit(1).execute()
    return res.data[0]["chat_id"] if res.data else None


def save_chat_id(chat_id: str) -> None:
    existing = supabase.table("telegram_settings").select("id").limit(1).execute()
    if existing.data:
        supabase.table("telegram_settings").update({"chat_id": chat_id}).eq("id", existing.data[0]["id"]).execute()
    else:
        supabase.table("telegram_settings").insert({"chat_id": chat_id}).execute()


def send_message(chat_id: str, text: str) -> bool:
    res = requests.post(f"{API_BASE}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=8)
    return res.ok


def send_alert(text: str) -> bool:
    """Dipakai fitur lain (misal Scanner) buat ngirim alert ke chat yang udah tersimpan.
    Balikin False diem-diem kalau belum ada chat_id tersimpan — biar caller gak perlu
    peduli soal setup Telegram tiap kali mau kirim alert."""
    chat_id = get_saved_chat_id()
    if not chat_id:
        return False
    return send_message(chat_id, text)


def send_photo(chat_id: str, photo_bytes: bytes, caption: str) -> bool:
    res = requests.post(
        f"{API_BASE}/sendPhoto",
        data={"chat_id": chat_id, "caption": caption[:1024]},  # limit caption Telegram
        files={"photo": ("chart.png", photo_bytes, "image/png")},
        timeout=15,
    )
    return res.ok


def send_alert_photo(photo_bytes: bytes, caption: str) -> bool:
    """Sama kayak send_alert() tapi lampiran foto (dipake scheduler.py buat kirim
    chart + garis support/resistance)."""
    chat_id = get_saved_chat_id()
    if not chat_id:
        return False
    return send_photo(chat_id, photo_bytes, caption)