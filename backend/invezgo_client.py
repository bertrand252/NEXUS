"""
Thin wrapper around Invezgo API (https://api.invezgo.com) — mirror gaya groq_client.py.
Semua fungsi butuh INVEZGO_API_KEY di .env. Kalau key belum diisi, is_configured()
balikin False dan caller (scoring.py/scanner.py) fallback ke yfinance/mock — NEXUS
gak ikut down cuma karena belum subscribe.

CATATAN JUJUR: bentuk response di bawah ini based on contoh yang nempel di OpenAPI
spec resmi mereka (api.invezgo.com/openapi.json), BUKAN hasil tes lawan API asli
(belum ada API key aktif pas kode ini ditulis). Kemungkinan ada penyesuaian kecil
field/struktur begitu dites pertama kali pake key beneran.
"""
import httpx
from config import INVEZGO_API_KEY

BASE_URL = "https://api.invezgo.com"


def is_configured() -> bool:
    return bool(INVEZGO_API_KEY)


def _headers() -> dict:
    return {"Authorization": f"Bearer {INVEZGO_API_KEY}"}


def get_stock_list() -> list[dict]:
    """[{code, name, sector, logo}, ...] — semua ticker IDX, real-time."""
    res = httpx.get(f"{BASE_URL}/analysis/list/stock", headers=_headers(), timeout=30)
    res.raise_for_status()
    return res.json()


def get_top_accumulation(date: str) -> dict:
    """{"accum": [{code, name, price, change, value, volume, graph}], "dist": [...]}
    Top saham BDM flow (bandarmologi) di tanggal itu. date format: YYYY-MM-DD."""
    res = httpx.get(f"{BASE_URL}/analysis/top/accumulation", headers=_headers(),
                     params={"date": date}, timeout=30)
    res.raise_for_status()
    return res.json()


def get_top_foreign(date: str) -> dict:
    """Struktur sama kayak get_top_accumulation, buat foreign flow."""
    res = httpx.get(f"{BASE_URL}/analysis/top/foreign", headers=_headers(),
                     params={"date": date}, timeout=30)
    res.raise_for_status()
    return res.json()


def get_broker_summary(code: str) -> dict:
    res = httpx.get(f"{BASE_URL}/analysis/summary/stock/{code}", headers=_headers(), timeout=30)
    res.raise_for_status()
    return res.json()


def get_stock_chart(code: str) -> dict:
    res = httpx.get(f"{BASE_URL}/analysis/chart/stock/{code}", headers=_headers(), timeout=30)
    res.raise_for_status()
    return res.json()


def get_index_chart(code: str) -> dict:
    res = httpx.get(f"{BASE_URL}/analysis/chart/index/{code}", headers=_headers(), timeout=30)
    res.raise_for_status()
    return res.json()
