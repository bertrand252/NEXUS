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
    """Long-poll getUpdates — channel_post (forward berita) + callback_query
    (tombol Terima/Tolak rotasi Swing). Digabung 1 fungsi/1 loop (bukan 2
    poller terpisah) karena Telegram NOLAK >1 getUpdates paralel per bot
    token (409 Conflict) — offset biar Telegram gak ngirim ulang update yang
    udah di-ack (dipake scheduler.py::run_telegram_channel_listener)."""
    params = {"timeout": timeout, "allowed_updates": '["channel_post","callback_query"]'}
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


def send_message(chat_id: str, text: str) -> int | None:
    """Balikin message_id kalau sukses (dipake buat unsend/delete_message
    belakangan), None kalau gagal. Masih aman dipake di context boolean
    (`if send_alert(...)`) — message_id Telegram gak pernah 0."""
    res = requests.post(
        f"{API_BASE}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=8,
    )
    if not res.ok:
        return None
    return res.json().get("result", {}).get("message_id")


def send_alert(text: str) -> int | None:
    """Dipakai fitur lain (misal Scanner) buat ngirim alert ke chat yang udah tersimpan.
    Balikin None diem-diem kalau belum ada chat_id tersimpan — biar caller gak perlu
    peduli soal setup Telegram tiap kali mau kirim alert."""
    chat_id = get_saved_chat_id()
    if not chat_id:
        return None
    return send_message(chat_id, text)


def send_photo(chat_id: str, photo_bytes: bytes, caption: str) -> int | None:
    res = requests.post(
        f"{API_BASE}/sendPhoto",
        data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},  # limit caption Telegram
        files={"photo": ("chart.png", photo_bytes, "image/png")},
        timeout=15,
    )
    if not res.ok:
        return None
    return res.json().get("result", {}).get("message_id")


def send_alert_photo(photo_bytes: bytes, caption: str) -> int | None:
    """Sama kayak send_alert() tapi lampiran foto (dipake scheduler.py buat kirim
    chart + garis support/resistance)."""
    chat_id = get_saved_chat_id()
    if not chat_id:
        return None
    return send_photo(chat_id, photo_bytes, caption)


def send_message_with_buttons(chat_id: str, text: str, buttons: list[list[dict]]) -> int | None:
    """buttons: [[{"text": "...", "callback_data": "..."}], ...] — inline
    keyboard (dipake buat usul rotasi Swing: tombol Terima/Tolak)."""
    res = requests.post(
        f"{API_BASE}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
              "reply_markup": {"inline_keyboard": buttons}},
        timeout=8,
    )
    if not res.ok:
        return None
    return res.json().get("result", {}).get("message_id")


def send_alert_with_buttons(text: str, buttons: list[list[dict]]) -> int | None:
    chat_id = get_saved_chat_id()
    if not chat_id:
        return None
    return send_message_with_buttons(chat_id, text, buttons)


def answer_callback_query(callback_query_id: str, text: str | None = None) -> None:
    """Wajib dipanggil abis tombol diklik — kalau enggak, Telegram nunjukin
    loading spinner nempel di tombol user selamanya (client-side UX doang,
    bukan API requirement fatal, tapi diem-diem gagal disini gak masalah)."""
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        requests.post(f"{API_BASE}/answerCallbackQuery", json=payload, timeout=8)
    except Exception:
        pass


def edit_message_text(message_id: int, text: str, remove_buttons: bool = True) -> bool:
    """Update pesan usul rotasi abis user mutusin (ilangin tombol, tunjukin
    hasil keputusan) — biar gak nyangkut nunjukin tombol yang udah gak
    relevan lagi kalau di-scroll ulang."""
    chat_id = get_saved_chat_id()
    if not chat_id:
        return False
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if remove_buttons:
        payload["reply_markup"] = {"inline_keyboard": []}
    res = requests.post(f"{API_BASE}/editMessageText", json=payload, timeout=8)
    return res.ok


def delete_message(message_id: int) -> bool:
    """Unsend — hapus pesan yang UDAH kekirim (dipake pas posisi ditutup:
    TP/SL/timeout/invalidated, biar chat Telegram gak numpuk alert basi).
    Bot Telegram bisa hapus pesannya sendiri di chat privat kapan aja (gak
    ada batas 48 jam kayak akun user biasa). Diem-diem gagal kalau
    message_id gak valid/pesan udah kehapus manual — bukan error fatal."""
    chat_id = get_saved_chat_id()
    if not chat_id or not message_id:
        return False
    res = requests.post(
        f"{API_BASE}/deleteMessage",
        json={"chat_id": chat_id, "message_id": message_id},
        timeout=8,
    )
    return res.ok