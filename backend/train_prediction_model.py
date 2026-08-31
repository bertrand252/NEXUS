"""
Training AI Prediction (XGBoost, prediksi arah harga 5 hari ke depan).
Manual run (python train_prediction_model.py), BUKAN auto-scheduled —
data histori gak berubah drastis harian, retraining gak perlu tiap hari.

Split TEMPORAL (bukan random shuffle) — train di data lama, test di data
baru — biar akurasi yang dilaporin jujur nyerminin kondisi prediksi
beneran (bukan bocor liat "masa depan" pas training).
"""
import sys
import os
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

# stdout gampang crash (UnicodeEncodeError) pas di-redirect ke file di Windows
# (defaultnya cp1252, bukan UTF-8) — kejadian beneran, script sempet crash
# abis ngumpulin 900rb+ baris data gara-gara nyoba print 1 emoji doang.
sys.stdout.reconfigure(encoding="utf-8")

import joblib
import pandas as pd
import xgboost as xgb
import yfinance as yf
from sklearn.metrics import accuracy_score

from config import supabase
from prediction_features import compute_features_series, FEATURE_NAMES

FUTURE_DAYS = 5
UP_THRESHOLD_PCT = 2.0  # label "naik" kalau harga 5 hari ke depan naik >2%

_DIR = os.path.dirname(__file__)
MODEL_DIR = os.path.join(_DIR, "models")
MODEL_PATH = os.path.join(MODEL_DIR, "xgb_predictor.joblib")
META_PATH = os.path.join(MODEL_DIR, "xgb_predictor_meta.json")


def _get_tickers() -> list[str]:
    """Ambil dari scanner_cache yang UDAH ada — data-nya udah kebukti valid
    di yfinance, hemat waktu vs nyoba 951 ticker dari nol (banyak yang bakal
    gagal/gocap/delisted)."""
    res = supabase.table("scanner_cache").select("ticker").execute()
    return [r["ticker"] for r in res.data]


def _process_one(ticker: str) -> list[dict]:
    try:
        hist = yf.Ticker(f"{ticker}.JK").history(period="5y", auto_adjust=False).dropna(subset=["Close"])
    except Exception:
        return []
    if len(hist) < 60:
        return []

    feats = compute_features_series(hist)
    close = hist["Close"]
    future_return = (close.shift(-FUTURE_DAYS) - close) / close * 100
    label = (future_return > UP_THRESHOLD_PCT).astype(int)

    df = feats.copy()
    df["label"] = label
    df["date"] = hist.index
    df = df.dropna(subset=FEATURE_NAMES)
    df = df.iloc[:-FUTURE_DAYS] if len(df) > FUTURE_DAYS else df.iloc[0:0]  # buang ekor tanpa label (masa depan blm ada)
    return df.to_dict("records")


def _build_dataset(tickers: list[str]) -> pd.DataFrame:
    results, errors = [], 0
    # concurrency rendah + gak ada retry — training itu sekali jalan, kalau
    # 1 ticker gagal fetch skip aja, gak worth kompleksitas retry kayak
    # POST /scanner/refresh (itu buat user-facing, ini offline script)
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_process_one, t): t for t in tickers}
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception:
                errors += 1
    print(f"selesai fetch {len(tickers)} ticker ({errors} gagal) -> {len(results)} baris data")
    return pd.DataFrame(results)


def train() -> None:
    tickers = _get_tickers()
    print(f"training pake {len(tickers)} ticker dari scanner_cache (fetch 5 tahun histori tiap ticker, bisa lama)...")
    df = _build_dataset(tickers)
    if df.empty or len(df) < 500:
        print(f"GAGAL — cuma {len(df)} baris data kekumpul, kurang buat training yang masuk akal.")
        return

    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = df.sort_values("date")
    cutoff = df["date"].quantile(0.85)
    train_df = df[df["date"] < cutoff]
    test_df = df[df["date"] >= cutoff]
    print(f"train: {len(train_df)} baris, test: {len(test_df)} baris, cutoff tanggal: {cutoff.date()}")

    X_train, y_train = train_df[FEATURE_NAMES], train_df["label"]
    X_test, y_test = test_df[FEATURE_NAMES], test_df["label"]

    model = xgb.XGBClassifier(n_estimators=200, max_depth=4, learning_rate=0.05, eval_metric="logloss")
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    accuracy = float(accuracy_score(y_test, y_pred))
    baseline_accuracy = float(max(y_test.mean(), 1 - y_test.mean()))

    print(f"\nAkurasi model di test set (data yang model BELUM PERNAH liat): {accuracy:.3f}")
    print(f"Baseline (tebak kelas mayoritas doang, gak pake model): {baseline_accuracy:.3f}")
    if accuracy <= baseline_accuracy + 0.02:
        print("⚠️  PERINGATAN: model gak jauh lebih baik dari nebak asal-asalan.")
        print("    Tetep disimpen (jujur, angka akurasinya ke-tampil di UI apa adanya),")
        print("    tapi jangan dianggep model ini beneran prediktif.")

    os.makedirs(MODEL_DIR, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    meta = {
        "accuracy": round(accuracy, 4),
        "baseline_accuracy": round(baseline_accuracy, 4),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_samples": len(df),
        "n_tickers": len(tickers),
        "future_days": FUTURE_DAYS,
        "up_threshold_pct": UP_THRESHOLD_PCT,
    }
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"\nModel tersimpen: {MODEL_PATH}")
    print(f"Meta tersimpen: {META_PATH}")


if __name__ == "__main__":
    train()
