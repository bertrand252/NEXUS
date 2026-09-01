"""
Serving prediksi AI Prediction (model XGBoost, lihat train_prediction_model.py
buat cara latihnya). Kalau model belum pernah di-training (file gak ada),
is_configured() False — Stock Detail tetep nampilin placeholder jujur
"Belum tersedia", gak crash (pola sama kayak invezgo_client.py).
"""
import os
import json
import joblib
import pandas as pd
from prediction_features import compute_features, FEATURE_NAMES

_DIR = os.path.dirname(__file__)
MODEL_PATH = os.path.join(_DIR, "models", "xgb_predictor.joblib")
META_PATH = os.path.join(_DIR, "models", "xgb_predictor_meta.json")

_model = None
_meta: dict = {}

if os.path.exists(MODEL_PATH) and os.path.exists(META_PATH):
    try:
        _model = joblib.load(MODEL_PATH)
        with open(META_PATH, encoding="utf-8") as f:
            _meta = json.load(f)
    except Exception:
        _model = None


def is_configured() -> bool:
    return _model is not None


def predict_direction(hist, broker_features: dict | None = None) -> dict | None:
    """None kalau model belum di-training ATAU data historis ticker ini
    kurang buat ngitung fitur — biar caller gampang: null = tampilin
    placeholder, gak perlu bedain alasannya di frontend.
    broker_features: snapshot HARI INI dari fitur broker (lihat
    prediction_features.py::compute_broker_features_series) — kalau None,
    compute_features() fallback ke nilai netral (dulu SELALU None, artinya
    prediksi live gak pernah liat kondisi broker asli walau modelnya
    dilatih pake itu — caller (routers/scanner.py) sekarang fetch ini beneran)."""
    if _model is None:
        return None
    features = compute_features(hist, broker_features)
    if features is None:
        return None

    try:
        X = pd.DataFrame([features])[FEATURE_NAMES]
        prob_up = float(_model.predict_proba(X)[0][1])
    except Exception:
        return None  # jangan biarin bug prediksi keanggep "data ticker gak ketemu" sama caller

    direction = "up" if prob_up >= 0.5 else "down"
    return {
        "direction": direction,
        "probability": round(prob_up if direction == "up" else 1 - prob_up, 3),
        "model_accuracy": _meta.get("accuracy"),
        "baseline_accuracy": _meta.get("baseline_accuracy"),
    }
