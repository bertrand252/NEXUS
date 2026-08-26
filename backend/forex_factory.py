"""
Ambil kalender ekonomi mingguan dari Forex Factory (gratis, gak butuh API key).
Dipakai oleh routers/market_events.py (buat ditampilin) dan routers/portfolio.py
(sebagai konteks Step B — gantiin STUB_FOREX_EVENTS lama).
"""
import time
from datetime import datetime, timedelta, timezone
import requests

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
JAKARTA_TZ = timezone(timedelta(hours=7))
CACHE_TTL_SECONDS = 300  # 5 menit — FF cuma update kalender ini beberapa kali sehari, jadi aman di-cache

_cache: dict = {"data": None, "fetched_at": 0}

FLAG = {
    "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵", "CNY": "🇨🇳",
    "AUD": "🇦🇺", "CHF": "🇨🇭", "NZD": "🇳🇿", "CAD": "🇨🇦", "IDR": "🇮🇩",
}

# Sektor IDX yang biasanya paling kena imbas per mata uang/negara asal event.
# Ini pemetaan umum (rule of thumb), bukan hasil model kuantitatif — cukup buat
# kasih konteks kualitatif ke AI & user, bukan angka presisi.
SECTOR_MAP = {
    "USD": "All Sectors", "EUR": "Banking", "GBP": "Banking",
    "JPY": "Export, Technology", "CNY": "Basic Materials, Export",
    "AUD": "Basic Materials", "CHF": "Banking", "NZD": "Basic Materials",
    "CAD": "Energy", "IDR": "All Sectors",
}


def get_forex_events() -> list[dict]:
    """Fetch + parse kalender minggu ini. Di-cache 5 menit biar gak nembak API mereka
    berkali-kali dalam waktu singkat (bisa kena 429 rate limit kalau gak di-cache) —
    dipanggil dari market-events.html DAN portfolio simulate, jadi gampang numpuk request.
    Kalau FF down/limited, balikin list kosong (bukan error) biar fitur lain tetap jalan."""
    now = time.time()
    if _cache["data"] is not None and (now - _cache["fetched_at"]) < CACHE_TTL_SECONDS:
        return _cache["data"]

    try:
        res = requests.get(FF_URL, timeout=8)
        res.raise_for_status()
        raw = res.json()
    except Exception as e:
        print(f"[forex_factory] gagal fetch: {type(e).__name__}: {e}")  # keliatan di terminal uvicorn
        # kalau ada cache lama (walau expired), lebih baik pakai itu daripada kosong total
        return _cache["data"] if _cache["data"] is not None else []

    events = []
    for e in raw:
        try:
            dt_utc_offset = datetime.fromisoformat(e["date"])
            dt_wib = dt_utc_offset.astimezone(JAKARTA_TZ)
        except (KeyError, ValueError):
            continue

        country = e.get("country", "")
        events.append({
            "date": dt_wib.strftime("%Y-%m-%d"),
            "time_wib": dt_wib.strftime("%H:%M"),
            "flag": FLAG.get(country, "🌐"),
            "currency": country,
            "event": e.get("title", ""),
            "impact": e.get("impact", "Low"),
            "forecast": e.get("forecast", ""),
            "previous": e.get("previous", ""),
            "idx_sector_impact": SECTOR_MAP.get(country, "—"),
        })

    _cache["data"] = events
    _cache["fetched_at"] = now
    return events