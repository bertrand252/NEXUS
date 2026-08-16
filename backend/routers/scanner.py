from fastapi import APIRouter, HTTPException
import yfinance as yf
from scoring import compute_score

router = APIRouter()

# MVP watchlist, sesuai dummy data yang udah ada di frontend.
# Nanti kalau mau scan "semua saham IDX" beneran, ganti ini jadi query dari
# daftar emiten BEI (bisa dari file CSV statis atau tabel Supabase) — belum
# perlu sekarang, 10 ticker ini cukup buat demo sidang.
WATCHLIST = ["ANTM", "BBRI", "ADRO", "ASII", "BMRI", "ICBP", "TLKM", "GOTO", "UNVR", "MDKA"]

SECTOR = {
    "ANTM": "Basic Materials", "BBRI": "Banking", "ADRO": "Energy", "ASII": "Consumer",
    "BMRI": "Banking", "ICBP": "Consumer", "TLKM": "Technology", "GOTO": "Technology",
    "UNVR": "Consumer", "MDKA": "Basic Materials",
}


def _get_history(ticker: str, period: str = "1mo"):
    hist = yf.Ticker(f"{ticker}.JK").history(period=period)  # .JK suffix = IDX di Yahoo Finance
    if hist.empty:
        raise ValueError(f"no data for {ticker}")
    return hist


def _score_from_history(ticker: str, hist) -> dict:
    price_now = float(hist["Close"].iloc[-1])
    price_5d_ago = float(hist["Close"].iloc[-6]) if len(hist) >= 6 else float(hist["Close"].iloc[0])
    volume_today = float(hist["Volume"].iloc[-1])
    volume_avg20 = float(hist["Volume"].tail(20).mean())
    low_20d = float(hist["Low"].tail(20).min())
    high_20d = float(hist["High"].tail(20).max())
    chg_pct = (price_now - float(hist["Close"].iloc[-2])) / float(hist["Close"].iloc[-2]) * 100 if len(hist) >= 2 else 0.0

    score = compute_score(ticker, volume_today, volume_avg20, price_now, price_5d_ago, low_20d, high_20d)

    return {
        "ticker": ticker,
        "price": round(price_now, 2),
        "change_pct": round(chg_pct, 2),
        "volume_ratio": round(volume_today / volume_avg20, 2) if volume_avg20 else 0,
        **score,
    }


@router.get("")
def get_scanner():
    results, errors = [], []
    for t in WATCHLIST:
        try:
            results.append(_score_from_history(t, _get_history(t)))
        except Exception as e:
            errors.append({"ticker": t, "error": str(e)})

    if not results:
        raise HTTPException(status_code=502, detail={"message": "yfinance returned no data at all", "errors": errors})

    results.sort(key=lambda r: r["total_score"], reverse=True)
    return {"data": results, "errors": errors}


@router.get("/{ticker}")
def get_stock_detail(ticker: str):
    """Detail 1 saham buat halaman stock-detail.html: skor + candlestick 1 bulan terakhir."""
    ticker = ticker.upper()
    try:
        hist = _get_history(ticker)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Data untuk {ticker} gak ketemu di yfinance")

    result = _score_from_history(ticker, hist)
    result["sector"] = SECTOR.get(ticker, "—")

    candles = [
        {
            "time": idx.strftime("%Y-%m-%d"),
            "open": round(float(row["Open"]), 2),
            "high": round(float(row["High"]), 2),
            "low": round(float(row["Low"]), 2),
            "close": round(float(row["Close"]), 2),
        }
        for idx, row in hist.iterrows()
    ]
    result["candles"] = candles

    return result