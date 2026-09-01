"""
Kalender hari libur — dipake buat 2 hal: (1) sinyal "wait and see" ke AI
seleksi alert Swing (dekat tanggal merah/weekend panjang = rawan profit
taking, banyak saham turun sebelum bursa tutup lama), (2) suppress
notifikasi rutin (Sarapan Pagi/Recap Malam) di hari bursa tutup.

CATATAN JUJUR: sumbernya data libur nasional Indonesia dari komunitas
(GitHub, gratis, gak butuh API key) — BUKAN kalender resmi BEI (BEI punya
beberapa hari libur "cuti bersama"/khusus bursa yang gak selalu match 1:1
sama libur nasional biasa, dan gak ada API resmi publik buat kalender BEI).
Approximation yang cukup akurat buat kebanyakan kasus, bukan 100% presis.
"""
from datetime import date, timedelta
import time
import requests
from config import today_wib

HOLIDAY_URL = "https://raw.githubusercontent.com/guangrei/APIHariLibur_V2/main/calendar.json"
CACHE_TTL_SECONDS = 24 * 60 * 60  # data taunan, jarang berubah — cache 1 hari cukup

_cache: dict = {"data": None, "fetched_at": 0}


def _get_holidays() -> dict:
    now = time.time()
    if _cache["data"] is not None and (now - _cache["fetched_at"]) < CACHE_TTL_SECONDS:
        return _cache["data"]
    try:
        res = requests.get(HOLIDAY_URL, timeout=10)
        res.raise_for_status()
        data = res.json()
    except Exception:
        return _cache["data"] or {}  # gagal fetch — pake cache lama kalau ada, biar gak crash caller
    _cache["data"], _cache["fetched_at"] = data, now
    return data


def is_trading_day(d: date) -> bool:
    """Weekend ATAU hari libur (flag holiday=true di data) dianggap bursa tutup."""
    if d.weekday() >= 5:  # Sabtu/Minggu
        return False
    entry = _get_holidays().get(d.isoformat())
    if entry and entry.get("holiday"):
        return False
    return True


def upcoming_holidays(within_days: int = 3, from_date: date | None = None) -> list[dict]:
    """Hari libur/weekend dalam N hari ke depan (exclude hari ini sendiri) —
    dipake buat sinyal "wait and see" (rawan profit taking sebelum bursa
    tutup lama)."""
    start = from_date or today_wib()
    holidays = _get_holidays()
    result = []
    for i in range(1, within_days + 1):
        d = start + timedelta(days=i)
        if d.weekday() >= 5:
            result.append({"date": d.isoformat(), "reason": "Weekend"})
            continue
        entry = holidays.get(d.isoformat())
        if entry and entry.get("holiday"):
            result.append({"date": d.isoformat(), "reason": ", ".join(entry.get("summary", ["Libur"]))})
    return result
