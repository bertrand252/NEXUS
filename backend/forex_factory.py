"""
Ambil kalender ekonomi mingguan dari Forex Factory (gratis, gak butuh API key).
Dipakai oleh routers/market_events.py (buat ditampilin) dan routers/portfolio.py
(sebagai konteks Step B — gantiin STUB_FOREX_EVENTS lama).
"""
from datetime import datetime, timedelta, timezone
import requests

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
JAKARTA_TZ = timezone(timedelta(hours=7))

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
    """Fetch + parse kalender minggu ini. Kalau FF down, balikin list kosong (bukan error)
    biar fitur lain yang bergantung ke ini (portfolio simulation) tetap jalan."""
    try:
        res = requests.get(FF_URL, timeout=8)
        res.raise_for_status()
        raw = res.json()
    except Exception as e:
        print(f"[forex_factory] gagal fetch: {type(e).__name__}: {e}")  # keliatan di terminal uvicorn
        return []

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
    return events