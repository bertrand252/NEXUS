import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()


def _env(name: str) -> str | None:
    """os.getenv + strip — Railway (atau copy-paste mana pun) gampang nyelipin
    trailing newline/spasi di value env var, yang bikin header kayak
    'Authorization: Bearer xxx\\n' dianggap invalid sama httpx (LocalProtocolError)
    padahal key-nya sendiri valid."""
    val = os.getenv(name)
    return val.strip() if val is not None else None


SUPABASE_URL = _env("SUPABASE_URL")
SUPABASE_KEY = _env("SUPABASE_KEY")
GROQ_API_KEY = _env("GROQ_API_KEY")
GROQ_MODEL = _env("GROQ_MODEL") or "openai/gpt-oss-120b"
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")

# Channel Telegram yang mau di-forward otomatis ke /intel — chat_id numerik,
# pisah koma buat lebih dari 1. Kosong = fitur ini gak jalan (skip diem-diem).
# Cuma jalan buat channel yang bot-nya udah di-invite jadi admin.
TELEGRAM_CHANNEL_IDS = [c.strip() for c in os.getenv("TELEGRAM_CHANNEL_IDS", "").split(",") if c.strip()]

# Channel Telegram yang mau di-scrape dari preview publik (t.me/s/<username>) —
# buat channel yang kita CUMA subscriber biasa, gak bisa jadiin bot admin.
# Isi username-nya doang (tanpa @), pisah koma.
TELEGRAM_SCRAPE_CHANNELS = [c.strip().lstrip("@") for c in os.getenv("TELEGRAM_SCRAPE_CHANNELS", "").split(",") if c.strip()]

# Invezgo (opsional) — kosong = fallback ke yfinance/mock, keisi = NEXUS otomatis pakai data Invezgo
INVEZGO_API_KEY = _env("INVEZGO_API_KEY")
FRONTEND_ORIGINS = [o.strip() for o in os.getenv("FRONTEND_ORIGINS", "*").split(",") if o.strip()]

# Buat caller mesin-ke-mesin (WhatsApp listener, dst) yang gak bisa login penuh — lihat auth_guard.py
SERVICE_API_KEY = _env("SERVICE_API_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL / SUPABASE_KEY missing — copy .env.example to .env and fill it in")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)