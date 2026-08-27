import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Invezgo (opsional) — kosong = fallback ke yfinance/mock, keisi = NEXUS otomatis pakai data Invezgo
INVEZGO_API_KEY = os.getenv("INVEZGO_API_KEY")
FRONTEND_ORIGINS = os.getenv("FRONTEND_ORIGINS", "*").split(",")

# Buat caller mesin-ke-mesin (WhatsApp listener, dst) yang gak bisa login penuh — lihat auth_guard.py
SERVICE_API_KEY = os.getenv("SERVICE_API_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL / SUPABASE_KEY missing — copy .env.example to .env and fill it in")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)