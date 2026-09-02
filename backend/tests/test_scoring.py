"""Test unit buat scoring.py — fungsi murni (gak nyentuh Supabase/yfinance/
Groq), paling gampang & paling worth dites duluan (banyak angka ambang batas
yang gampang salah ketik pas di-refactor)."""
import math
import pandas as pd
from scoring import (
    volume_score, price_score, technical_score, bsjp_criteria,
    compression_setup, bsjp_intraday_score, bpjs_momentum_score, signal_label,
    ma_alignment, adx, bollinger_signal,
)


def _make_hist(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({
        "Open": closes, "Close": closes,
        "High": [c * 1.01 for c in closes],
        "Low": [c * 0.99 for c in closes],
        "Volume": [1_000_000] * len(closes),
    }, index=idx)


def test_volume_score_thresholds():
    assert volume_score(0, 0) == 0  # volume_avg20 nol, jangan ZeroDivisionError
    assert volume_score(100, 100) == 6     # ratio 1.0
    assert volume_score(150, 100) == 13    # ratio 1.5
    assert volume_score(200, 100) == 19    # ratio 2.0
    assert volume_score(300, 100) == 25    # ratio 3.0


def test_price_score_momentum_and_position():
    assert price_score(0, 0, 0, 0) == 0  # semua nol, jangan error
    # harga di high 20 hari + momentum kuat -> skor maksimal
    assert price_score(price_now=110, price_5d_ago=100, low_20d=90, high_20d=110) == 25


def test_technical_score_breakout_confirmed_high_volume():
    # breakout jelas + volume gede + close di atas + likuid -> skor tinggi
    score = technical_score(price_now=110, resistance_prior=100, volume_ratio=3.5,
                             close_position_pct=0.9, value_traded_idr=2_000_000_000)
    assert score == 20  # 10+6+2+2, skor maksimal


def test_technical_score_no_breakout_low_volume():
    score = technical_score(price_now=95, resistance_prior=100, volume_ratio=0.8,
                             close_position_pct=0.2, value_traded_idr=100_000_000)
    assert score == 0


def test_bsjp_criteria_pass():
    assert bsjp_criteria(
        price_now=105, price_prev=100, volume_today=300, volume_avg20=100,
        ma5=102, value_traded_idr=6_000_000_000,
    ) is True


def test_bsjp_criteria_fail_low_liquidity():
    # breakout+volume oke, tapi value_traded di bawah ambang institusi -> gagal
    assert bsjp_criteria(
        price_now=105, price_prev=100, volume_today=300, volume_avg20=100,
        ma5=102, value_traded_idr=1_000_000_000,
    ) is False


def test_bsjp_criteria_gocap_excluded():
    assert bsjp_criteria(
        price_now=53, price_prev=50, volume_today=300, volume_avg20=100,
        ma5=51, value_traded_idr=6_000_000_000,
    ) is False  # price_prev <=50, dianggap saham gocap


def test_compression_setup_pass():
    assert compression_setup(ma5=100, ma10=101, ma20=102, price_now=105,
                              sideways_days=15, rr_ratio=2.0) is True


def test_compression_setup_fail_ma_too_spread():
    assert compression_setup(ma5=90, ma10=100, ma20=110, price_now=105,
                              sideways_days=15, rr_ratio=2.0) is False


def test_bsjp_intraday_score_no_takeoff():
    assert bsjp_intraday_score(None, 6_000_000_000) == 0.0


def test_bsjp_intraday_score_price_flat_or_down():
    takeoff = {"volume_ratio": 5.0, "price_change_pct": 0.0, "s1_spike_supporting": False}
    assert bsjp_intraday_score(takeoff, 6_000_000_000) == 0.0


def test_bsjp_intraday_score_positive_with_supporting_bonus():
    takeoff = {"volume_ratio": 5.0, "price_change_pct": 3.0, "s1_spike_supporting": True}
    score = bsjp_intraday_score(takeoff, 6_000_000_000)
    assert score == 5.5  # 5.0 * 1.1


def test_bpjs_momentum_score_below_liquidity_floor():
    takeoff = {"volume_ratio": 4.0, "price_change_pct": 2.0}
    assert bpjs_momentum_score(takeoff, 1_000_000_000) == 0.0  # di bawah Rp3M


def test_signal_label_bands():
    assert signal_label(80) == "Strong"
    assert signal_label(75) == "Strong"
    assert signal_label(60) == "Moderate"
    assert signal_label(30) == "Weak"
    assert signal_label(10) == "None"


def test_ma_alignment_golden_death_mixed():
    assert ma_alignment(105, 102, 100) == "golden"  # MA5>MA10>MA20, bullish stack
    assert ma_alignment(95, 98, 100) == "death"  # kebalikannya, bearish stack
    assert ma_alignment(100, 105, 102) == "mixed"  # belum align jelas


def test_adx_strong_trend_beats_choppy_market():
    uptrend = _make_hist([100 + i * 1.5 for i in range(60)])  # naik konsisten, minim noise
    choppy = _make_hist([100 + 3 * math.sin(i) for i in range(60)])  # osilasi di range sempit
    adx_trend, adx_choppy = adx(uptrend), adx(choppy)
    assert adx_trend > 25  # trend kuat
    assert adx_choppy < 20  # choppy/lemah
    assert adx_trend > adx_choppy


def test_adx_none_when_history_too_short():
    assert adx(_make_hist([100.0] * 10)) is None  # < period*2 (14*2=28)


def test_bollinger_squeeze_after_volatile_period():
    # 40 hari volatil (amplitude gede) diikuti 30 hari SANGAT sempit — band
    # width sekarang harus masuk 20% tersempit relatif ke histori barusan
    volatile = [100 + 8 * math.sin(i * 0.5) for i in range(40)]
    tight = [100.0 + 0.05 * math.sin(i) for i in range(30)]
    result = bollinger_signal(_make_hist(volatile + tight))
    assert result["squeeze"] is True


def test_bollinger_position_above_upper_on_breakout():
    closes = [100.0] * 79 + [130.0]  # lompatan gede hari terakhir, nembus band atas
    result = bollinger_signal(_make_hist(closes))
    assert result["position"] == "above_upper"


def test_bollinger_none_when_history_too_short():
    assert bollinger_signal(_make_hist([100.0] * 10)) is None  # < period+20 (20+20=40)
