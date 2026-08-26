from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import yfinance as yf
from scoring import compute_score
from scanner_universe import TICKERS, SECTOR_BY_TICKER, NAME_BY_TICKER
from config import supabase
from levels import support_resistance
from groq_client import translate_to_indonesian
import invezgo_client

router = APIRouter()


def _build_accum_lookup() -> dict | None:
    """{ticker: "accum"|"dist"} dari top BDM flow + foreign flow Invezgo, 1x per
    refresh (bukan per-ticker call — endpoint-nya udah ngasih semua top mover
    market sekaligus). None kalau Invezgo belum di-subscribe atau lagi error
    (biar refresh_scanner tetap jalan pake mock, gak ikut gagal total)."""
    if not invezgo_client.is_configured():
        return None
    today = date.today().isoformat()
    lookup: dict = {}
    try:
        acc = invezgo_client.get_top_accumulation(today)
        for row in acc.get("accum", []):
            lookup[row["code"]] = "accum"
        for row in acc.get("dist", []):
            lookup[row["code"]] = "dist"
        frn = invezgo_client.get_top_foreign(today)
        for row in frn.get("accum", []):
            lookup.setdefault(row["code"], "accum")
        for row in frn.get("dist", []):
            lookup.setdefault(row["code"], "dist")
    except Exception:
        return None
    return lookup


def _get_history(ticker: str, period: str = "2mo"):
    hist = yf.Ticker(f"{ticker}.JK").history(period=period)  # .JK suffix = IDX di Yahoo Finance
    # baris terakhir kadang NaN (suspend/gak ada transaksi hari itu) — buang biar
    # gak nyebar NaN ke scoring & JSON response (NaN gak valid JSON, bikin 500)
    hist = hist.dropna(subset=["Close"])
    if hist.empty:
        raise ValueError(f"no data for {ticker}")
    return hist


def _rsi14(closes) -> float:
    """RSI(14) standar. Butuh minimal ~15 baris data — _get_history pakai period 2mo biar aman."""
    delta = closes.diff()
    avg_gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
    avg_loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    if not avg_loss or avg_loss != avg_loss:  # 0 atau NaN (data kurang)
        return 50.0  # netral kalau gak bisa dihitung
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _score_from_history(ticker: str, hist, accum_lookup: dict | None = None) -> dict:
    price_now = float(hist["Close"].iloc[-1])
    price_5d_ago = float(hist["Close"].iloc[-6]) if len(hist) >= 6 else float(hist["Close"].iloc[0])
    volume_today = float(hist["Volume"].iloc[-1])
    volume_avg20 = float(hist["Volume"].tail(20).mean())
    low_20d = float(hist["Low"].tail(20).min())
    high_20d = float(hist["High"].tail(20).max())
    chg_pct = (price_now - float(hist["Close"].iloc[-2])) / float(hist["Close"].iloc[-2]) * 100 if len(hist) >= 2 else 0.0

    ma20 = float(hist["Close"].tail(20).mean())
    price_vs_ma20_pct = (price_now - ma20) / ma20 * 100 if ma20 else 0.0
    rsi14 = _rsi14(hist["Close"])

    score = compute_score(ticker, volume_today, volume_avg20, price_now, price_5d_ago, low_20d, high_20d, rsi14, price_vs_ma20_pct, accum_lookup)

    return {
        "ticker": ticker,
        "price": round(price_now, 2),
        "change_pct": round(chg_pct, 2),
        "volume_ratio": round(volume_today / volume_avg20, 2) if volume_avg20 else 0,
        **score,
    }


@router.get("")
def get_scanner():
    """Baca dari scanner_cache (Supabase), bukan live-fetch — 951 ticker gak sanggup
    di-live-fetch tiap page-load. Isi cache-nya lewat POST /scanner/refresh."""
    try:
        res = supabase.table("scanner_cache").select("*").order("total_score", desc=True).execute()
    except Exception:
        return {"data": [], "errors": [], "warning": "Cache masih kosong — jalanin POST /scanner/refresh dulu."}
    if not res.data:
        return {"data": [], "errors": [], "warning": "Cache masih kosong — jalanin POST /scanner/refresh dulu."}
    return {"data": res.data, "errors": []}


@router.post("/refresh")
def refresh_scanner():
    """Live-fetch semua 951 ticker di data/idx_universe.json (paralel via thread pool,
    yfinance itu I/O-bound jadi ini bukan over-engineering — sequential bakal makan
    berpuluh menit), hitung score, terus upsert ke scanner_cache. Manual trigger,
    belum ada scheduler otomatis."""
    accum_lookup = _build_accum_lookup()

    def _fetch_one(ticker: str) -> dict:
        row = _score_from_history(ticker, _get_history(ticker), accum_lookup)
        row["sector"] = SECTOR_BY_TICKER.get(ticker, "—")
        row["name"] = NAME_BY_TICKER.get(ticker, ticker)
        return row

    results, errors = [], []
    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = {pool.submit(_fetch_one, t): t for t in TICKERS}
        for future in as_completed(futures):
            t = futures[future]
            try:
                results.append(future.result())
            except Exception as e:
                errors.append({"ticker": t, "error": str(e)})

    for i in range(0, len(results), 500):
        chunk = results[i:i + 500]
        supabase.table("scanner_cache").upsert(chunk, on_conflict="ticker").execute()

    return {"refreshed": len(results), "failed": len(errors), "errors": errors[:30]}


class TranslateInput(BaseModel):
    text: str


@router.post("/translate")
def translate_summary(payload: TranslateInput):
    """Terjemahin teks (deskripsi bisnis perusahaan dari yfinance) ke Bahasa
    Indonesia, dipanggil on-demand pas user klik toggle bahasa di frontend."""
    try:
        return {"translated": translate_to_indonesian(payload.text)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gagal translate: {e}")


@router.get("/index/ihsg")
def get_ihsg():
    """Harga IHSG (^JKSE) + sparkline 20 hari terakhir, buat banner Market Mood di Dashboard."""
    try:
        hist = yf.Ticker("^JKSE").history(period="2mo").dropna(subset=["Close"])
        if hist.empty:
            raise ValueError("no data")
    except Exception:
        raise HTTPException(status_code=502, detail="Data IHSG gak ketemu di yfinance")

    price_now = float(hist["Close"].iloc[-1])
    price_prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else price_now
    chg_pct = (price_now - price_prev) / price_prev * 100 if price_prev else 0.0

    return {
        "price": round(price_now, 2),
        "change_pct": round(chg_pct, 2),
        "spark": [round(float(c), 2) for c in hist["Close"].tail(20)],
    }


CHART_TIMEFRAMES = {
    "1D": {"period": "5d", "interval": "15m"},
    "1W": {"period": "1mo", "interval": "1d"},
    "1M": {"period": "2mo", "interval": "1d"},
    "1Y": {"period": "1y", "interval": "1d"},
}


def _candles_from_hist(hist) -> list[dict]:
    return [
        {
            "time": int(idx.timestamp()),  # UTCTimestamp — lightweight-charts terima ini buat daily & intraday sekaligus
            "open": round(float(row["Open"]), 2),
            "high": round(float(row["High"]), 2),
            "low": round(float(row["Low"]), 2),
            "close": round(float(row["Close"]), 2),
            "volume": round(float(row["Volume"]), 0),
        }
        for idx, row in hist.iterrows()
    ]


@router.get("/{ticker}")
def get_stock_detail(ticker: str, period: str = "1M"):
    """Detail 1 saham: skor + level support/resistance (selalu dari window 2 bulan,
    biar scoring konsisten) + candlestick (periode bisa diganti-ganti via ?period=
    1D/1W/1M/1Y buat tombol timeframe di frontend, gak ikut ngubah skor)."""
    ticker = ticker.upper()
    try:
        hist = _get_history(ticker)
        result = _score_from_history(ticker, hist, _build_accum_lookup())
        result["levels"] = support_resistance(hist)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Data untuk {ticker} gak ketemu di yfinance")
    result["sector"] = SECTOR_BY_TICKER.get(ticker, "—")

    try:
        info = yf.Ticker(f"{ticker}.JK").info
        result["company"] = {
            "name": info.get("longName") or NAME_BY_TICKER.get(ticker),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "employees": info.get("fullTimeEmployees"),
            "summary": info.get("longBusinessSummary"),
            "website": info.get("website"),
        }
    except Exception:
        result["company"] = None  # yfinance kadang gak punya profile lengkap buat ticker tertentu, gak fatal

    timeframe = CHART_TIMEFRAMES.get(period, CHART_TIMEFRAMES["1M"])
    if timeframe == CHART_TIMEFRAMES["1M"]:
        chart_hist = hist  # udah di-fetch di atas, gak perlu call yfinance lagi
    else:
        try:
            chart_hist = (
                yf.Ticker(f"{ticker}.JK")
                .history(period=timeframe["period"], interval=timeframe["interval"])
                .dropna(subset=["Close"])
            )
        except Exception:
            chart_hist = hist  # gagal fetch periode lain, fallback ke yang udah ada daripada chart kosong

    result["candles"] = _candles_from_hist(chart_hist)

    return result