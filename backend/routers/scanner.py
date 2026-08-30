import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import yfinance as yf
from scoring import compute_score, bsjp_criteria, invest_criteria, compression_setup
from scanner_universe import TICKERS, SECTOR_BY_TICKER, NAME_BY_TICKER
from config import supabase, today_wib
from levels import support_resistance, detect_pivot_zones
from prediction import predict_direction
from groq_client import translate_to_indonesian, explain_levels
from rate_limit import limiter
import invezgo_client

router = APIRouter()


def _build_accum_lookup() -> dict | None:
    """{ticker: "accum"|"dist"} dari top BDM flow + foreign flow Invezgo, 1x per
    refresh (bukan per-ticker call — endpoint-nya udah ngasih semua top mover
    market sekaligus). None kalau Invezgo belum di-subscribe atau lagi error
    (biar refresh_scanner tetap jalan pake mock, gak ikut gagal total)."""
    if not invezgo_client.is_configured():
        return None
    today = today_wib().isoformat()
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


def _get_history_intraday(ticker: str, period: str = "5d", interval: str = "15m"):
    """Sama pola _get_history, tapi bar 15-menit — dipake BSJP stage-2 (session
    takeoff) & BPJS. Pola fetch-nya sama persis CHART_TIMEFRAMES["1D"] di bawah,
    udah kebukti index-nya tz-aware Asia/Jakarta."""
    hist = yf.Ticker(f"{ticker}.JK").history(period=period, interval=interval)
    hist = hist.dropna(subset=["Close"])
    if hist.empty:
        raise ValueError(f"no intraday data for {ticker}")
    return hist


def _score_from_history(ticker: str, hist, accum_lookup: dict | None = None) -> dict:
    price_now = float(hist["Close"].iloc[-1])
    price_5d_ago = float(hist["Close"].iloc[-6]) if len(hist) >= 6 else float(hist["Close"].iloc[0])
    volume_today = float(hist["Volume"].iloc[-1])
    volume_avg20 = float(hist["Volume"].tail(20).mean())
    low_20d = float(hist["Low"].tail(20).min())
    high_20d = float(hist["High"].tail(20).max())
    chg_pct = (price_now - float(hist["Close"].iloc[-2])) / float(hist["Close"].iloc[-2]) * 100 if len(hist) >= 2 else 0.0

    # resistance dari 20 hari SEBELUM hari ini (bukan termasuk hari ini) — kalau
    # kepake hist["High"].tail(20) yang masih ngikut hari ini, "breakout" jadi
    # tautologi (resistance-nya ke-update duluan sama harga hari ini sendiri)
    prior = hist.iloc[:-1].tail(20)
    resistance_prior = float(prior["High"].max()) if not prior.empty else price_now

    today = hist.iloc[-1]
    day_range = float(today["High"] - today["Low"])
    close_position_pct = (float(today["Close"]) - float(today["Low"])) / day_range if day_range > 0 else 0.5
    value_traded_idr = price_now * volume_today

    score = compute_score(
        ticker, volume_today, volume_avg20, price_now, price_5d_ago, low_20d, high_20d,
        resistance_prior, close_position_pct, value_traded_idr, accum_lookup,
    )

    price_prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else price_now
    ma5 = float(hist["Close"].tail(5).mean())
    cocok_bsjp = bsjp_criteria(price_now, price_prev, volume_today, volume_avg20, ma5, value_traded_idr)

    ma10 = float(hist["Close"].tail(10).mean())
    ma20 = float(hist["Close"].tail(20).mean())
    sideways_days = _sideways_days(hist)
    rr_ratio = support_resistance(hist)["rr_ratio"]
    cocok_compression = compression_setup(ma5, ma10, ma20, price_now, sideways_days, rr_ratio)

    return {
        "ticker": ticker,
        "price": round(price_now, 2),
        "change_pct": round(chg_pct, 2),
        "volume_ratio": round(volume_today / volume_avg20, 2) if volume_avg20 else 0,
        "cocok_bsjp": cocok_bsjp,
        "cocok_compression": cocok_compression,
        "sideways_days": sideways_days,
        **score,
    }


def _sideways_days(hist, band_pct: float = 3.0) -> int:
    """Berapa hari BERTURUT-TURUT SEBELUM hari ini harga stay dalem band_pct
    dari MA20 — proxy "udah berapa lama sepi/sideways". Mulai dari KEMARIN
    (bukan hari ini), soalnya hari ini bisa aja lagi breakout (itu justru
    yang mau dideteksi, bukan bagian dari fase sideways-nya)."""
    ma20 = hist["Close"].rolling(20).mean()
    closes = hist["Close"]
    count = 0
    start = len(hist) - 2
    floor = max(len(hist) - 41, 19)
    for i in range(start, floor, -1):
        m = ma20.iloc[i]
        if m != m or m <= 0:  # NaN check
            break
        deviation = abs(closes.iloc[i] - m) / m * 100
        if deviation > band_pct:
            break
        count += 1
    return count


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


@router.get("/sectors")
def get_sector_heatmap():
    """Agregasi scanner_cache per sektor — buat heatmap rotasi sektor di
    Dashboard. Sektor "—" (ticker yang gak ke-mapping) sengaja dipisah, gak
    ikut ditampilin di heatmap (bukan sektor asli)."""
    try:
        res = supabase.table("scanner_cache").select("sector,total_score,signal").execute()
    except Exception:
        return {"data": [], "warning": "Cache masih kosong — jalanin POST /scanner/refresh dulu."}
    if not res.data:
        return {"data": [], "warning": "Cache masih kosong — jalanin POST /scanner/refresh dulu."}

    agg: dict[str, dict] = {}
    for row in res.data:
        sector = row.get("sector") or "—"
        if sector == "—":
            continue
        a = agg.setdefault(sector, {"sector": sector, "count": 0, "score_sum": 0, "strong_count": 0})
        a["count"] += 1
        a["score_sum"] += row["total_score"]
        if row["signal"] == "Strong":
            a["strong_count"] += 1

    data = [
        {"sector": a["sector"], "count": a["count"], "strong_count": a["strong_count"],
         "avg_score": round(a["score_sum"] / a["count"], 1)}
        for a in agg.values()
    ]
    data.sort(key=lambda d: d["strong_count"], reverse=True)
    return {"data": data, "warning": None}


@router.post("/refresh")
@limiter.limit("2/minute")
def refresh_scanner(request: Request):
    """Live-fetch semua 951 ticker di data/idx_universe.json (paralel via thread pool,
    yfinance itu I/O-bound jadi ini bukan over-engineering — sequential bakal makan
    berpuluh menit), hitung score, terus upsert ke scanner_cache. Manual trigger,
    belum ada scheduler otomatis.

    Rate limit KETAT (2/menit) — bukan cuma jaga backend NEXUS, tapi jaga-jaga
    Yahoo Finance (951 request ke yfinance per panggilan, udah pernah kena
    "Too Many Requests" gara-gara terlalu sering refresh, lihat insiden #8)."""
    accum_lookup = _build_accum_lookup()

    def _fetch_one(ticker: str) -> dict:
        row = _score_from_history(ticker, _get_history(ticker), accum_lookup)
        row["sector"] = SECTOR_BY_TICKER.get(ticker, "—")
        row["name"] = NAME_BY_TICKER.get(ticker, ticker)
        return row

    def _fetch_batch(tickers: list[str], max_workers: int) -> tuple[list[dict], list[dict]]:
        batch_results, batch_errors = [], []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch_one, t): t for t in tickers}
            for future in as_completed(futures):
                t = futures[future]
                try:
                    batch_results.append(future.result())
                except Exception as e:
                    batch_errors.append({"ticker": t, "error": str(e)})
        return batch_results, batch_errors

    # max_workers diturunin dari 15 — di Railway semua request numpang 1 IP,
    # burst gede lebih gampang kena rate-limit Yahoo dibanding IP rumah.
    results, errors = _fetch_batch(TICKERS, max_workers=5)

    # retry sekali khusus yang kena rate-limit (bukan yang emang no-data/delisted)
    rate_limited = [e["ticker"] for e in errors if "Too Many Requests" in e["error"]]
    if rate_limited:
        time.sleep(5)  # kasih jeda biar rate-limit window Yahoo reset dulu
        errors = [e for e in errors if "Too Many Requests" not in e["error"]]
        retry_results, retry_errors = _fetch_batch(rate_limited, max_workers=3)
        results.extend(retry_results)
        errors.extend(retry_errors)

    for i in range(0, len(results), 500):
        chunk = results[i:i + 500]
        supabase.table("scanner_cache").upsert(chunk, on_conflict="ticker").execute()

    return {"refreshed": len(results), "failed": len(errors), "errors": errors[:30]}


@router.post("/refresh-fundamentals")
@limiter.limit("2/minute")
def refresh_fundamentals(request: Request):
    """Data fundamental (PER/PBV/dividend yield/market cap) buat Invest
    criteria — TERPISAH dari POST /refresh (harga/volume) karena beda cadence
    (fundamental jarang berubah harian, gak perlu di-refresh tiap kali harga
    di-refresh) dan `.info` lebih berat ke yfinance dibanding `.history()`.
    Manual trigger doang, belum ada scheduler otomatis (gak ada urgensi).
    Cuma nyentuh ticker yang UDAH ada row-nya di scanner_cache — kalau upsert
    dibiarin bikin row baru buat ticker yang belum pernah lolos refresh price
    (`POST /refresh`), row itu bakal punya price/total_score/signal NULL, dan
    frontend crash pas manggil `.toLocaleString()` di harga yang null."""
    try:
        existing = supabase.table("scanner_cache").select("ticker").execute()
        existing_tickers = {r["ticker"] for r in existing.data}
    except Exception:
        return {"refreshed": 0, "failed": 0, "errors": [], "warning": "scanner_cache kosong — jalanin POST /refresh dulu"}

    def _fetch_one(ticker: str) -> dict:
        info = yf.Ticker(f"{ticker}.JK").info
        per = info.get("trailingPE")
        pbv = info.get("priceToBook")
        dividend_yield = info.get("dividendYield")
        market_cap = info.get("marketCap")
        return {
            "ticker": ticker,
            "per": per,
            "pbv": pbv,
            "dividend_yield": dividend_yield,
            "market_cap": market_cap,
            "cocok_invest": invest_criteria(per, pbv, dividend_yield, market_cap),
            "fundamentals_updated_at": datetime.now(timezone.utc).isoformat(),
        }

    tickers_to_fetch = [t for t in TICKERS if t in existing_tickers]

    results, errors = [], []
    with ThreadPoolExecutor(max_workers=5) as pool:  # sama kayak POST /refresh — .info lebih berat, jangan naikin
        futures = {pool.submit(_fetch_one, t): t for t in tickers_to_fetch}
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


@router.post("/{ticker}/annotate")
def annotate_chart(ticker: str):
    """Penjelasan Groq buat level support/resistance di chart Stock Detail —
    on-demand (tombol), BUKAN otomatis tiap buka halaman, biar gak boros call
    Groq buat user yang cuma window-shopping."""
    ticker = ticker.upper()
    try:
        hist = _get_history(ticker)
        result = _score_from_history(ticker, hist, _build_accum_lookup())
        levels = support_resistance(hist)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Data untuk {ticker} gak ketemu di yfinance")

    score_breakdown = {
        "volume_score": result["volume_score"], "price_score": result["price_score"],
        "accumulation_score": result["accumulation_score"], "technical_score": result["technical_score"],
    }
    try:
        return {"penjelasan": explain_levels(ticker, score_breakdown, levels)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gagal generate penjelasan: {e}")


@router.get("/{ticker}/broker-flow")
def get_broker_flow(ticker: str):
    """Kerangka tab "Broker Flow" (StockDetail) — broker summary, broker stalker
    (top broker net buy/sell hari ini), insider activity, notation, order queue,
    tape reading, volume profile. SEMUA field None + configured=False kalau
    INVEZGO_API_KEY belum diisi — frontend nampilin "Belum tersedia" jujur,
    BUKAN data ngarang. Dibuat sebelum key aktif, jadi tiap field independen
    (1 gagal gak nge-block yang lain) karena struktur response REAL belum
    pernah diverifikasi lawan API asli."""
    ticker = ticker.upper()
    if not invezgo_client.is_configured():
        return {
            "configured": False,
            "broker_summary": None, "top_broker_stalker": None, "insider_activity": None,
            "notation": None, "price_table": None, "financial_statement": None, "price_seasonality": None,
        }

    today = today_wib().isoformat()
    result = {"configured": True}
    try:
        result["broker_summary"] = invezgo_client.get_broker_summary(ticker)
    except Exception:
        result["broker_summary"] = None

    # broker paling gede net-buy hari ini -> liat histori akumulasi dia
    # khusus di saham ini (broker stalker butuh 2 parameter: broker+stock,
    # jadi harus tau broker MANA dulu dari broker_summary di atas)
    try:
        top_broker = max(result["broker_summary"], key=lambda b: b.get("net_value", 0)) if result["broker_summary"] else None
        result["top_broker_stalker"] = invezgo_client.get_broker_stalker(top_broker["code"], ticker) if top_broker else None
    except Exception:
        result["top_broker_stalker"] = None

    try:
        result["insider_activity"] = invezgo_client.get_insider_activity(
            ticker, (today_wib() - timedelta(days=90)).isoformat(), today,
        )
    except Exception:
        result["insider_activity"] = None
    try:
        notation_all = invezgo_client.get_notation_list()
        result["notation"] = next((n for n in notation_all if n.get("code") == ticker), None)
    except Exception:
        result["notation"] = None
    try:
        result["price_table"] = invezgo_client.get_price_table(ticker, today)
    except Exception:
        result["price_table"] = None
    try:
        result["financial_statement"] = invezgo_client.get_financial_statement(ticker)
    except Exception:
        result["financial_statement"] = None
    try:
        result["price_seasonality"] = invezgo_client.get_price_seasonality(ticker)
    except Exception:
        result["price_seasonality"] = None
    return result


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
        # tanggal closing valid terakhir — yfinance kadang NaN/bolong buat ^JKSE
        # beberapa hari, jadi harga di atas bisa aja bukan closing hari ini
        "as_of": hist.index[-1].strftime("%Y-%m-%d"),
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
        result["ai_zones"] = detect_pivot_zones(hist)
        result["ai_prediction"] = predict_direction(hist)
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

    try:
        mentor_res = supabase.table("mentor_calls").select("*").eq("ticker", ticker).limit(1).execute()
        result["mentor_call"] = mentor_res.data[0] if mentor_res.data else None
    except Exception:
        result["mentor_call"] = None

    try:
        bpjs_res = (
            supabase.table("signal_alerts").select("alerted_at,status,entry_price,target,stop_loss")
            .eq("ticker", ticker).eq("source", "bpjs")
            .order("alerted_at", desc=True).limit(1).execute()
        )
        result["bpjs_last_alert"] = bpjs_res.data[0] if bpjs_res.data else None
    except Exception:
        result["bpjs_last_alert"] = None  # kolom "source" belum ada / query gagal — jangan gagalin detail saham

    try:
        # cocok_invest itung PER/PBV/dividend dari yfinance .info (lambat), cuma
        # dihitung di refresh_fundamentals bulk job — di sini cukup baca cache-nya
        invest_res = supabase.table("scanner_cache").select("cocok_invest").eq("ticker", ticker).limit(1).execute()
        result["cocok_invest"] = bool(invest_res.data and invest_res.data[0].get("cocok_invest"))
    except Exception:
        result["cocok_invest"] = False

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