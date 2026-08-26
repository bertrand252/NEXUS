"""
Load daftar 951 ticker IDX + sektor dari data/idx_universe.json (sumber: GitHub
wildangunawan/Dataset-Saham-IDX, snapshot Feb 2025 — CC BY-NC 4.0, non-commercial).
Data agak lawas (IPO baru setelah snapshot belum kecatet) tapi ini data asli, bukan karangan.
"""
import json
import os

_UNIVERSE_PATH = os.path.join(os.path.dirname(__file__), "data", "idx_universe.json")

with open(_UNIVERSE_PATH, encoding="utf-8") as f:
    UNIVERSE = json.load(f)

TICKERS = [u["ticker"] for u in UNIVERSE]
SECTOR_BY_TICKER = {u["ticker"]: u["sector"] for u in UNIVERSE}
NAME_BY_TICKER = {u["ticker"]: u["name"] for u in UNIVERSE}
