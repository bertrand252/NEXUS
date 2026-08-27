import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Channel Telegram yang mau di-forward otomatis ke /intel — chat_id numerik,
# pisah koma buat lebih dari 1. Kosong = fitur ini gak jalan (skip diem-diem).
# Cuma jalan buat channel yang bot-nya udah di-invite jadi admin.
TELEGRAM_CHANNEL_IDS = [c.strip() for c in os.getenv("TELEGRAM_CHANNEL_IDS", "").split(",") if c.strip()]

# Channel Telegram yang mau di-scrape dari preview publik (t.me/s/<username>) —
# buat channel yang kita CUMA subscriber biasa, gak bisa jadiin bot admin.
# Isi username-nya doang (tanpa @), pisah koma.
TELEGRAM_SCRAPE_CHANNELS = [c.strip().lstrip("@") for c in os.getenv("TELEGRAM_SCRAPE_CHANNELS", "").split(",") if c.strip()]

# Invezgo (opsional) — kosong = fallback ke yfinance/mock, keisi = NEXUS otomatis pakai data Invezgo
INVEZGO_API_KEY = os.getenv("INVEZGO_API_KEY")
FRONTEND_ORIGINS = os.getenv("FRONTEND_ORIGINS", "*").split(",")

# Buat caller mesin-ke-mesin (WhatsApp listener, dst) yang gak bisa login penuh — lihat auth_guard.py
SERVICE_API_KEY = os.getenv("SERVICE_API_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL / SUPABASE_KEY missing — copy .env.example to .env and fill it in")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)