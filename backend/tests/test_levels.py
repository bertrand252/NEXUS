"""Test unit buat levels.py — support/resistance & rr_label, fungsi murni
(gak nyentuh yfinance/Supabase)."""
import pandas as pd
from levels import rr_label, support_resistance, well_defended_support


def test_rr_label_bands():
    assert rr_label(0.5) == "Buruk"
    assert rr_label(1.5) == "Cukup"
    assert rr_label(2.5) == "Bagus"
    assert rr_label(4.0) == "Sangat Bagus"


def _make_hist(closes: list[float]) -> pd.DataFrame:
    """DataFrame OHLC sintetis — High/Low dikasih spread kecil dari Close
    biar realistis, index tanggal harian berurutan."""
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({
        "Open": closes, "Close": closes,
        "High": [c * 1.01 for c in closes],
        "Low": [c * 0.99 for c in closes],
        "Volume": [1_000_000] * len(closes),
    }, index=idx)


def test_support_resistance_excludes_today_no_tautology():
    """Bug yang pernah kejadian beneran (KETR/LIFE, lihat CLAUDE.md insiden):
    resistance HARUS dari 20 hari SEBELUM hari ini, bukan ikutan hari ini —
    kalau hari ini bikin high baru, resistance gak boleh sama persis price_now."""
    closes = [100.0] * 20 + [150.0]  # hari terakhir breakout gede ke 150
    hist = _make_hist(closes)
    levels = support_resistance(hist)
    price_now = 150.0
    assert levels["resistance"] != price_now
    assert levels["resistance"] < price_now  # resistance dari histori (~101), bukan hari ini


def test_support_resistance_rr_ratio_computed():
    closes = [100.0 + i * 0.5 for i in range(21)]  # naik pelan-pelan, gak breakout ekstrem
    hist = _make_hist(closes)
    levels = support_resistance(hist)
    assert levels["rr_label"] == rr_label(levels["rr_ratio"])
    assert levels["stop_loss"] < levels["entry_low"]  # SL harus di bawah zona entry


_SUPPORT_BOUNCE_CLOSES = [
    110, 107, 103, 100, 103, 107, 111, 108, 104, 100,
    103, 108, 112, 109, 105, 100, 104, 109, 113, 110, 106, 102,
]  # support ~100 disentuh 3x jelas (tiap bounce+turun cukup bar buat swing_window=3 kedeteksi)


def test_well_defended_support_detects_repeated_bounce():
    hist = _make_hist(_SUPPORT_BOUNCE_CLOSES)
    result = well_defended_support(hist, price_now=103.0)
    assert result is not None
    assert result["touches"] >= 3
    assert result["support_price"] < 103.0


def test_well_defended_support_none_when_price_too_far():
    hist = _make_hist(_SUPPORT_BOUNCE_CLOSES)
    assert well_defended_support(hist, price_now=140.0) is None  # udah lari jauh, bukan lagi buy-on-weakness


def test_well_defended_support_none_without_enough_touches():
    closes = [100.0 + i for i in range(20)]  # naik lurus, gak ada support berulang
    hist = _make_hist(closes)
    assert well_defended_support(hist, price_now=119.0) is None
