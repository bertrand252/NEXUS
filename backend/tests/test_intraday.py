"""Migrasi self-check yang tadinya di intraday.py::__main__ ke pytest beneran
— biar kecek otomatis tiap `pytest`, bukan cuma pas dijalanin manual."""
import pandas as pd
from intraday import daily_session_stats, session_takeoff


def _bar(ts, o, h, l, c, v):
    return {"Open": o, "High": h, "Low": l, "Close": c, "Volume": v, "_ts": ts}


def _synthetic_hist() -> pd.DataFrame:
    rows = []
    # Kamis (weekday=3): Sesi 1 09:00-12:00, Sesi 2 13:30-15:50 — volume normal
    for hh, mm in [(9, 0), (9, 15), (10, 0), (11, 0), (11, 45)]:
        rows.append(_bar(f"2026-08-27 {hh:02d}:{mm:02d}", 100, 101, 99, 100, 100_000))
    for hh, mm in [(13, 30), (14, 0), (14, 30), (15, 0), (15, 30), (15, 45)]:
        rows.append(_bar(f"2026-08-27 {hh:02d}:{mm:02d}", 100, 101, 99, 100, 100_000))
    # Jumat (weekday=4): Sesi 1 09:00-11:30, Sesi 2 14:00-15:50 — Sesi 2 TERBANG (volume 5x)
    for hh, mm in [(9, 0), (9, 15), (10, 0), (11, 0), (11, 15)]:
        rows.append(_bar(f"2026-08-28 {hh:02d}:{mm:02d}", 100, 101, 99, 100, 90_000))
    for hh, mm in [(14, 0), (14, 30), (15, 0), (15, 15), (15, 30), (15, 45)]:
        rows.append(_bar(f"2026-08-28 {hh:02d}:{mm:02d}", 100, 110, 100, 108, 500_000))

    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.pop("_ts")).dt.tz_localize("Asia/Jakarta")
    return df


def test_daily_session_stats_splits_by_official_schedule():
    days = daily_session_stats(_synthetic_hist())
    assert len(days) == 2
    assert days[0]["date"].weekday() == 3  # Kamis
    assert days[1]["date"].weekday() == 4  # Jumat
    assert days[0]["s1_bars"] == 5 and days[0]["s2_bars"] == 6
    # Jumat Sesi 2 mulai 14:00 (bukan 13:30 kayak weekday) -> tetep 6 bar (14:00-15:45)
    assert days[1]["s1_bars"] == 5 and days[1]["s2_bars"] == 6


def test_session_takeoff_detects_session2_spike():
    days = daily_session_stats(_synthetic_hist())
    takeoff = session_takeoff(days, session="s2")
    assert takeoff is not None
    assert takeoff["volume_ratio"] > 4  # sesi 2 Jumat ~5x volume sesi 2 Kamis
    assert takeoff["price_change_pct"] > 5  # 100 -> 108


def test_session_takeoff_none_when_history_too_short():
    days = daily_session_stats(_synthetic_hist())
    assert session_takeoff(days[:1], session="s2") is None  # cuma 1 hari, gak ada pembanding
