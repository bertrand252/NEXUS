"""
Fitur numerik buat AI Prediction (XGBoost) — dipake BARENG training
(train_prediction_model.py) dan serving (prediction.py). WAJIB fungsi
yang SAMA persis buat dua-duanya, kalau beda training/serving jadi gak
nyambung (model dilatih liat definisi fitur A, disuruh prediksi liat
definisi fitur B).

Vectorized (pandas rolling), bukan loop per-hari — biar training ribuan
baris x ratusan ticker gak lambat.
"""
import numpy as np
import pandas as pd

FEATURE_NAMES = [
    "volume_ratio", "momentum_pct", "position_pct", "breakout_pct",
    "close_position_pct", "change_pct", "price_vs_ma20_pct", "rsi14",
]


def compute_features_series(hist: pd.DataFrame) -> pd.DataFrame:
    """1 baris per hari trading, kolom = FEATURE_NAMES. Baris awal (butuh
    20 hari histori ke belakang) bakal NaN — di-drop sama caller."""
    close, high, low, volume = hist["Close"], hist["High"], hist["Low"], hist["Volume"]

    volume_avg20 = volume.rolling(20).mean()
    volume_ratio = volume / volume_avg20

    momentum_pct = (close - close.shift(5)) / close.shift(5) * 100

    low_20d = low.rolling(20).min()
    high_20d = high.rolling(20).max()
    position_pct = (close - low_20d) / (high_20d - low_20d)

    # resistance dari 20 hari SEBELUM hari ini — sama pola kayak scoring.py,
    # biar breakout_pct gak tautologi
    resistance_prior = high.shift(1).rolling(20).max()
    breakout_pct = (close - resistance_prior) / resistance_prior * 100

    day_range = high - low
    close_position_pct = ((close - low) / day_range).where(day_range > 0, 0.5)

    change_pct = close.pct_change() * 100

    ma20 = close.rolling(20).mean()
    price_vs_ma20_pct = (close - ma20) / ma20 * 100

    delta = close.diff()
    avg_gain = delta.clip(lower=0).rolling(14).mean()
    avg_loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi14 = (100 - (100 / (1 + rs))).fillna(50.0)

    return pd.DataFrame({
        "volume_ratio": volume_ratio,
        "momentum_pct": momentum_pct,
        "position_pct": position_pct,
        "breakout_pct": breakout_pct,
        "close_position_pct": close_position_pct,
        "change_pct": change_pct,
        "price_vs_ma20_pct": price_vs_ma20_pct,
        "rsi14": rsi14,
    })


def compute_features(hist: pd.DataFrame) -> dict | None:
    """Baris TERAKHIR doang — dipake serving (1 ticker, "fitur hari ini").
    None kalau data kurang / fitur gak lengkap (butuh histori 20 hari)."""
    df = compute_features_series(hist)
    if df.empty:
        return None
    last = df.iloc[-1]
    if last.isna().any():
        return None
    return last.to_dict()
