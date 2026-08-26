"""
Fetch + parse spreadsheet call saham dari mentor user (Google Sheets, export
CSV publik — gak butuh OAuth). Dipake router mentor_calls.py & scheduler.py
buat refresh tiap pagi sebelum market open.

Struktur sheet (2 baris header):
  NO, TICKER, RECOM DATE, BUY PRICE, , TP1, TP2, CL, gain%TP1, gain%TP2,
  loss%CL, STATUS, CURRENT PRICE, FLOATING PNL
"""
import csv
import io
from datetime import datetime
import requests

SHEET_ID = "1T2h0Hg_-M64XBEqU2wccOS0bVYkxlK3V4-BRUw0GcAA"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"


def _parse_number(raw: str) -> float | None:
    raw = raw.strip().replace("%", "").replace(",", ".")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_date(raw: str) -> str | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def fetch_mentor_calls() -> list[dict]:
    res = requests.get(CSV_URL, timeout=20)
    res.raise_for_status()
    rows = list(csv.reader(io.StringIO(res.text)))

    calls = []
    for row in rows[2:]:  # baris 1-2 itu header 2 tingkat
        if len(row) < 14 or not row[1].strip():
            continue
        calls.append({
            "ticker": row[1].strip().upper(),
            "recom_date": _parse_date(row[2]),
            "buy_price": _parse_number(row[3]),
            "tp1": _parse_number(row[5]),
            "tp2": _parse_number(row[6]),
            "cl": _parse_number(row[7]),
            "gain_tp1_pct": _parse_number(row[8]),
            "gain_tp2_pct": _parse_number(row[9]),
            "loss_cl_pct": _parse_number(row[10]),
            "status": row[11].strip(),
            "current_price": _parse_number(row[12]),
            "floating_pnl_pct": _parse_number(row[13]),
        })
    return calls
