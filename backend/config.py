import os
import socket
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Railway kadang resolve host luar (misal api.groq.com) ke IPv6 yang rute-nya
# putus dari egress mereka, connect gagal ("Connection error.") padahal key &
# host valid (jalan normal dari lokal). Paksa semua resolusi DNS proses ini ke
# IPv4 aja — semua service yang dipanggil (Groq, Supabase, yfinance, Telegram,
# Forex Factory) support IPv4 kok.
_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _getaddrinfo_ipv4_only

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