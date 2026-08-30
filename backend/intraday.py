"""Helper buat baca bar 15-menit yfinance (interval='15m') dan pisahin per sesi
perdagangan IDX (Sesi 1 pagi / Sesi 2 siang-sore). Dipake bareng oleh BSJP
(scoring.py + scheduler.py) dan BPJS.

Jam sesi resmi (diverifikasi idx.co.id via bareksa, Agustus 2026):
    Senin-Kamis: Sesi I 09:00-12:00, istirahat 12:00-13:30, Sesi II 13:30-15:50
    Jumat:       Sesi I 09:00-11:30, istirahat 11:30-14:00, Sesi II 14:00-15:50
"""

from datetime import date, time

SESSION_WEEKDAY = (time(9, 0), time(12, 0), time(13, 30), time(15, 50))
SESSION_FRIDAY = (time(9, 0), time(11, 30), time(14, 0), time(15, 50))

MIN_SESSION_BARS = 6  # ~1.5 jam data (bar 15 menit) — ponytail heuristic, bukan angka baku


def _session_bounds(d: date) -> tuple[time, time, time, time]:
    """(s1_start, s1_end, s2_start, s2_end) WIB. ponytail: gak nangani jadwal
    setengah-hari pra-libur (kalau IDX pernah pasang itu) — upgrade kalau
    kejadian, sekarang asumsi jadwal normal Senin-Kamis/Jumat doang."""
    return SESSION_FRIDAY if d.weekday() == 4 else SESSION_WEEKDAY


def daily_session_stats(hist_15m) -> list[dict]:
    """hist_15m: DataFrame dari yf.Ticker(...).history(interval="15m") — index
    tz-aware Asia/Jakarta (udah dikonfirmasi yfinance balikin gini), kolom
    Open/High/Low/Close/Volume. Group per hari kalender, split tiap hari jadi
    Sesi 1 / Sesi 2 pakai _session_bounds() (Jumat beda jadwal).

    Balikin list kronologis (hari paling lama duluan):
    [{"date": date, "s1_volume","s1_open","s1_close","s1_high","s1_low","s1_bars",
      "s2_volume","s2_open","s2_close","s2_high","s2_low","s2_bars"}]

    Hari yang Sesi 2-nya belum ada bar sama sekali (misal lagi jalan pas Sesi 1,
    atau hari ini) TETEP masuk list dengan s2_bars=0 — caller yang mutusin mau
    diapain, jangan di-skip diam-diam di sini.
    """
    if hist_15m.empty:
        return []

    idx = hist_15m.index
    days = sorted({ts.date() for ts in idx})

    out = []
    for d in days:
        s1_start, s1_end, s2_start, s2_end = _session_bounds(d)
        day_bars = hist_15m[idx.date == d]
        times = day_bars.index.time

        s1 = day_bars[(times >= s1_start) & (times < s1_end)]
        s2 = day_bars[(times >= s2_start) & (times < s2_end)]

        row = {"date": d}
        for label, bars in (("s1", s1), ("s2", s2)):
            if bars.empty:
                row.update({f"{label}_volume": 0.0, f"{label}_open": None, f"{label}_close": None,
                             f"{label}_high": None, f"{label}_low": None, f"{label}_bars": 0})
            else:
                row.update({
                    f"{label}_volume": float(bars["Volume"].sum()),
                    f"{label}_open": float(bars["Open"].iloc[0]),
                    f"{label}_close": float(bars["Close"].iloc[-1]),
                    f"{label}_high": float(bars["High"].max()),
                    f"{label}_low": float(bars["Low"].min()),
                    f"{label}_bars": len(bars),
                })
        out.append(row)

    return out


def session_takeoff(days: list[dict], session: str = "s2") -> dict | None:
    """Bandingin sesi TERAKHIR di window (hari ini) vs rata-rata sesi yang SAMA
    di hari-hari sebelumnya (mirip scoring.py::volume_score tapi di-scope ke 1
    sesi doang). Dipake generik buat BSJP (session="s2") dan BPJS (session
    sesuai sesi yang lagi jalan).

    None kalau histori <2 hari, atau bar hari ini di sesi tsb < MIN_SESSION_BARS
    (data belum cukup buat disimpulkan).

    Return: {"volume_ratio", "price_change_pct", "s1_spike_supporting", "close", "high"}
    "s1_spike_supporting" cuma keisi (bool) kalau session="s2" — nunjukkin
    Sesi 1 hari ini JUGA spike vs rata-rata Sesi 1 sebelumnya (pendukung/bonus,
    BUKAN syarat wajib — user eksplisit gak ada indikator resmi soal ini).
    """
    if len(days) < 2:
        return None

    today = days[-1]
    prior = days[:-1]

    bars_key = f"{session}_bars"
    if today.get(bars_key, 0) < MIN_SESSION_BARS:
        return None

    vol_key = f"{session}_volume"
    open_key = f"{session}_open"
    close_key = f"{session}_close"
    high_key = f"{session}_high"

    prior_volumes = [p[vol_key] for p in prior if p.get(f"{session}_bars", 0) >= MIN_SESSION_BARS]
    if not prior_volumes:
        return None
    avg_prior_volume = sum(prior_volumes) / len(prior_volumes)

    today_volume = today[vol_key]
    today_open = today[open_key]
    today_close = today[close_key]
    if not avg_prior_volume or today_open is None or today_close is None:
        return None

    result = {
        "volume_ratio": round(today_volume / avg_prior_volume, 3),
        "price_change_pct": round((today_close - today_open) / today_open * 100, 2),
        "close": today_close,
        "high": today[high_key],
    }

    if session == "s2":
        s1_prior_volumes = [p["s1_volume"] for p in prior if p.get("s1_bars", 0) >= MIN_SESSION_BARS]
        s1_avg = sum(s1_prior_volumes) / len(s1_prior_volumes) if s1_prior_volumes else 0
        result["s1_spike_supporting"] = bool(
            s1_avg and today.get("s1_bars", 0) >= MIN_SESSION_BARS and today["s1_volume"] >= s1_avg * 1.5
        )

    return result


if __name__ == "__main__":
    import pandas as pd

    def _bar(ts, o, h, l, c, v):
        return {"Open": o, "High": h, "Low": l, "Close": c, "Volume": v, "_ts": ts}

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

    days = daily_session_stats(df)
    assert len(days) == 2, f"expected 2 hari, dapet {len(days)}"
    assert days[0]["date"].weekday() == 3
    assert days[1]["date"].weekday() == 4
    assert days[0]["s1_bars"] == 5 and days[0]["s2_bars"] == 6
    assert days[1]["s1_bars"] == 5 and days[1]["s2_bars"] == 6, "Jumat Sesi 2 harusnya mulai jam 14:00, ke-split 6 bar"

    takeoff = session_takeoff(days, session="s2")
    assert takeoff is not None
    assert takeoff["volume_ratio"] > 4, f"volume_ratio harusnya ~5x, dapet {takeoff['volume_ratio']}"
    assert takeoff["price_change_pct"] > 5, f"price_change_pct harusnya naik gede, dapet {takeoff['price_change_pct']}"

    print("intraday.py self-check OK:", takeoff)
