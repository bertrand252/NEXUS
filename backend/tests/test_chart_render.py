"""Test unit buat chart_render.py — fokus ke logic tanggal/timezone
render_outcome_chart (bug nyata ketemu 2026-09-09: exit_date dari
datetime.now(timezone.utc) UDAH tz-aware, pd.Timestamp(dt, tz=tz) nolak itu
- 'Cannot pass a datetime or Timestamp with tzinfo with the tz parameter').
Gak assert isi pixel PNG (gak praktis/worth), cuma pastiin gak exception buat
kombinasi input tanggal yang beneran dipake caller (string ISO naive DAN
datetime tz-aware bercampur di 1 pemanggilan, persis skenario
_send_outcome_notification: alerted_at dari Supabase = string, exit_date =
datetime.now(timezone.utc))."""
import pandas as pd
from datetime import datetime, timezone
from chart_render import render_outcome_chart


def _make_hist_tz_aware(n: int = 40) -> pd.DataFrame:
    idx = pd.date_range("2026-07-01", periods=n, freq="D", tz="Asia/Jakarta")
    closes = [2300.0 + i * 10 for i in range(n)]
    return pd.DataFrame({
        "Open": closes, "Close": closes,
        "High": [c * 1.01 for c in closes], "Low": [c * 0.99 for c in closes],
        "Volume": [1_000_000] * n,
    }, index=idx)


def test_render_outcome_chart_handles_tz_aware_datetime_exit_date():
    # persis skenario nyata: exit_date dari datetime.now(timezone.utc)
    hist = _make_hist_tz_aware()
    png = render_outcome_chart(
        "TEST", hist, entry_price=2350.0, entry_date="2026-07-05T00:00:00+00:00",
        exit_price=2600.0, exit_date=datetime.now(timezone.utc),
        target=2600.0, stop_loss=2280.0, outcome="tp_hit",
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # signature PNG valid


def test_render_outcome_chart_handles_naive_datetime_dates():
    hist = _make_hist_tz_aware()
    png = render_outcome_chart(
        "TEST", hist, entry_price=2350.0, entry_date=datetime(2026, 7, 5),
        exit_price=2280.0, exit_date=datetime(2026, 7, 15),
        target=2600.0, stop_loss=2280.0, outcome="sl_hit",
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_outcome_chart_handles_hist_without_tz():
    idx = pd.date_range("2026-07-01", periods=30, freq="D")  # tz-naive index
    closes = [2300.0 + i * 5 for i in range(30)]
    hist = pd.DataFrame({
        "Open": closes, "Close": closes,
        "High": [c * 1.01 for c in closes], "Low": [c * 0.99 for c in closes],
        "Volume": [1_000_000] * 30,
    }, index=idx)
    png = render_outcome_chart(
        "TEST", hist, entry_price=2350.0, entry_date="2026-07-05",
        exit_price=2450.0, exit_date=datetime.now(timezone.utc),
        target=2500.0, stop_loss=2280.0, outcome="tp_hit",
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
