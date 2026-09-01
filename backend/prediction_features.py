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
    "broker_net_5d_norm", "broker_consistency_20d",
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


def compute_broker_features_series(inventory_chart: dict, value_traded: pd.Series) -> pd.DataFrame:
    """Dari invezgo_client.get_inventory_chart_stock() raw response — agregat
    net value SEMUA broker per hari (proxy demand institusi), 2 fitur time-series:
    - broker_net_5d_norm: rolling 5-hari sum net value, dinormalisasi value
      traded 20-hari (biar sebanding lintas saham gede/kecil, bukan angka
      Rupiah mentah yang skalanya beda2 jauh per emiten)
    - broker_consistency_20d: rolling 20-hari, fraksi hari net-buy positif —
      sinyal akumulasi STEADY, konsep sama kayak scheduler.py::_detect_bandar
      (consistency_pct) tapi versi time-series penuh buat training, bukan
      snapshot sesaat.
    Index balik NAIVE (tanpa timezone) — tanggal kalender Invezgo diperlakukan
    sebagai LABEL hari, bukan instant UTC (kalau di-tz_convert, jam-nya ikut
    geser, jadi beda dari index harian yfinance yang juga naive-per-tanggal —
    kejadian bug nyata: join gagal total 0 baris nyambung gara-gara ini).
    Caller WAJIB strip tz index `hist`/`value_traded` (`.tz_localize(None)`)
    sebelum join biar ketemu. DataFrame kosong kalau raw gak punya broker data.

    PENTING: `data[].value` per broker itu KUMULATIF sejak `from_date`
    (dikonfirmasi lawan API asli 2026-09-01 — hari pertama window kecil, hari
    terakhir udah triliunan, growth curve BUKAN oscillating harian). WAJIB
    di-diff() per broker dulu (delta hari-ke-hari) SEBELUM digabung/di-roll —
    nge-sum raw cumulative value langsung (kayak versi awal fungsi ini)
    double-counting parah, sama bug yang ketemu & di-fix di
    scheduler.py::_detect_bandar."""
    brokers = inventory_chart.get("broker") or []
    if not brokers:
        return pd.DataFrame()
    daily_delta_totals: dict[str, float] = {}
    for b in brokers:
        prev = 0.0
        for d in sorted(b.get("data") or [], key=lambda x: x["date"]):
            v = d.get("value") or 0
            daily_delta_totals[d["date"]] = daily_delta_totals.get(d["date"], 0) + (v - prev)
            prev = v
    if not daily_delta_totals:
        return pd.DataFrame()
    s = pd.Series(daily_delta_totals).sort_index()
    s.index = pd.to_datetime(s.index)  # naive

    net_5d_raw = s.rolling(5, min_periods=1).sum()
    value_traded_naive = value_traded.copy()
    value_traded_naive.index = value_traded_naive.index.tz_localize(None)
    value_traded_avg20 = value_traded_naive.rolling(20, min_periods=5).mean()
    net_5d_norm = net_5d_raw / value_traded_avg20.reindex(net_5d_raw.index)

    consistency_20d = (s > 0).rolling(20, min_periods=5).mean()

    return pd.DataFrame({"broker_net_5d_norm": net_5d_norm, "broker_consistency_20d": consistency_20d})


def compute_features(hist: pd.DataFrame, broker_features: dict | None = None) -> dict | None:
    """Baris TERAKHIR doang — dipake serving (1 ticker, "fitur hari ini").
    None kalau data kurang / fitur gak lengkap (butuh histori 20 hari).
    broker_features: {"broker_net_5d_norm", "broker_consistency_20d"} buat HARI
    INI doang (bukan time-series) — opsional, isi 0.0/netral kalau gak dikasih
    (Invezgo gagal fetch/belum configured), BUKAN bikin whole prediction gagal."""
    df = compute_features_series(hist)
    if df.empty:
        return None
    last = df.iloc[-1]
    tech_cols = [c for c in FEATURE_NAMES if c not in ("broker_net_5d_norm", "broker_consistency_20d")]
    if last[tech_cols].isna().any():
        return None
    result = last.to_dict()
    result["broker_net_5d_norm"] = (broker_features or {}).get("broker_net_5d_norm", 0.0) or 0.0
    result["broker_consistency_20d"] = (broker_features or {}).get("broker_consistency_20d", 0.5) or 0.5
    return result
