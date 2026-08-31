"""
Backtesting strategi Swing (breakout+volume, scoring.py::technical_score) lawan
histori harga yfinance — jawab pertanyaan "kalau gate teknikal ini dipake dari
dulu, beneran untung gak?". Manual run (python backtest.py), bukan auto-scheduled
— sama pola kayak train_prediction_model.py.

CATATAN JUJUR — approksimasi, bukan replay 100% persis alert live:
1. TP/SL pake find_smart_tp() yang SAMA kayak alert live (gap+fibonacci+swing,
   daily/weekly/monthly, role-reversal, sanity cap SL 15%) — PENTING ini bukan
   opsional: support_resistance() 20-hari SIMPEL gak bisa dipake buat TP hari
   breakout, soalnya breakout ARTINYA harga UDAH ngelewatin resistance 20-hari
   itu sendiri (target jadi di belakang harga, RR negatif) — kebukti pas dites
   (BBCA: RR -0.55 s/d 0.0 di 10 breakout pertama pake 20-hari doang, semua
   ke-skip). Weekly/monthly di-fetch 1x per ticker (bukan per entry), di-slice
   sampe tanggal entry biar gak bocor liat masa depan.
2. Entry diasumsikan LANGSUNG kefill di Close hari breakout (skip simulasi nunggu
   harga masuk zona entry_low-entry_high kayak status "waiting_entry" produksi) —
   zona entry dihitung dari Close hari itu sendiri (±1%/+2%), jadi hampir selalu
   overlap immediate, penyederhanaan ini kecil dampaknya.
3. Backtest ini ngitung SEMUA sinyal yang lolos gate (technical_score>=12 + RR>=1.5),
   BUKAN cuma 1-2 pick/minggu yang beneran dikirim ke Telegram (itu keputusan Groq +
   cap MAX_ALERTS_PER_WEEK, soal UX notifikasi biar gak spam, bukan soal kualitas
   sinyal). Jadi angka win rate di sini ngukur KUALITAS GATE-nya, bukan simulasi
   persis volume alert yang beneran dikirim.
4. BSJP/BPJS gak ikut dibacktest di sini — sinyalnya dari data intraday (15m),
   yfinance cuma nyimpen ~60 hari ke belakang buat interval itu, gak cukup buat
   backtest multi-tahun. Cuma Swing (pake data harian, histori panjang) yang bisa.

Constants BREAKOUT_TECHNICAL_THRESHOLD/MIN_RR_RATIO di-hardcode di sini (bukan
import dari scheduler.py — scheduler.py narik Telegram/Groq client cuma buat
kirim alert live, gak relevan buat script analisis offline). Kalau constant itu
diubah di scheduler.py, update juga di sini biar tetep sinkron.
"""
import sys
import os
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")  # sama alasan kayak train_prediction_model.py

import numpy as np
import pandas as pd
import yfinance as yf

from config import supabase
from prediction_features import compute_features_series
from levels import support_resistance, find_smart_tp, rr_label
from scoring import compression_setup
from routers.scanner import _sideways_days

BREAKOUT_TECHNICAL_THRESHOLD = 12  # sinkron manual scheduler.py::BREAKOUT_TECHNICAL_THRESHOLD
MIN_RR_RATIO = 1.5                 # sinkron manual scheduler.py::MIN_RR_RATIO
SIGNAL_TIMEOUT_DAYS = 14           # sinkron manual scheduler.py::SIGNAL_TIMEOUT_DAYS
MAX_RISK_PCT = 20                  # sinkron manual scheduler.py::MAX_RISK_PCT — kejadian nyata PACK
MAX_REWARD_PCT = 50                # (TP +275%, SL -58%) kekirim ke Telegram sebelum cap ini ada di produksi.
                                     # Backtest yang sebelumnya jalan TANPA cap ini — hasil "avg win" lama
                                     # kemungkinan kena inflate outlier kayak gini, makanya di-re-run.


def _fetch_ihsg_uptrend_map() -> dict:
    """Riset VCP (Minervini): breakout compression cuma diklaim ampuh pas market
    BROAD lagi uptrend — `scoring.py::compression_setup` yang ada SEKARANG gak
    cek ini sama sekali. Buat eksperimen, index IHSG (^JKSE) di-fetch 1x doang
    (bukan per ticker), map {date: bool} apa close hari itu di atas MA50-nya
    sendiri (proxy uptrend paling standar/laziest, sama semangat kayak trend
    filter yang udah ada di levels.py::determine_trend tapi versi index)."""
    hist = yf.Ticker("^JKSE").history(period="5y").dropna(subset=["Close"])
    ma50 = hist["Close"].rolling(50).mean()
    uptrend = hist["Close"] > ma50
    return {ts.date(): bool(v) for ts, v in uptrend.items()}


def _volume_dry_up(hist_upto: pd.DataFrame, sideways_days: int) -> bool:
    """Elemen VCP kedua yang ilang di compression_setup lama: volume harusnya
    MENGECIL selama fase sideways (bukan cuma harga yang diem). Bandingin
    rata-rata volume SELAMA base vs rata-rata volume 20 hari SEBELUM base
    mulai — kalau base beneran "volume dry-up", volume selama base harusnya
    lebih rendah dari kebiasaan sebelumnya."""
    n = len(hist_upto)
    if sideways_days < 5 or n < sideways_days + 20:
        return False
    vol_during_base = hist_upto["Volume"].iloc[-sideways_days:].mean()
    vol_before_base = hist_upto["Volume"].iloc[-(sideways_days + 20):-sideways_days].mean()
    if not vol_before_base:
        return False
    return vol_during_base < vol_before_base

_DIR = os.path.dirname(__file__)
RESULTS_PATH = os.path.join(_DIR, "backtest_results.json")


def _technical_score_series(feats: pd.DataFrame, value_traded_idr: pd.Series) -> pd.Series:
    """Versi vectorized scoring.py::technical_score — WAJIB formula sama persis
    (dicek via self-check di bawah), biar hasil backtest nyerminin gate produksi
    beneran, bukan gate yang mirip-mirip doang."""
    breakout_pct = feats["breakout_pct"]
    volume_ratio = feats["volume_ratio"]
    close_position_pct = feats["close_position_pct"]

    score = pd.Series(0, index=feats.index, dtype=int)
    score += np.select([breakout_pct >= 0, breakout_pct >= -3], [10, 5], default=0)
    score += np.select([volume_ratio >= 3.0, volume_ratio >= 2.0, volume_ratio >= 1.5], [6, 4, 2], default=0)
    score += np.select([close_position_pct >= 0.7, close_position_pct >= 0.4], [2, 1], default=0)
    score += np.select([value_traded_idr >= 1_000_000_000, value_traded_idr >= 300_000_000], [2, 1], default=0)
    return score


def _smart_levels(hist_slice: pd.DataFrame, weekly_slice: pd.DataFrame, monthly_slice: pd.DataFrame) -> dict:
    """Mirror scheduler.py::support_resistance()+_apply_smart_tp() digabung jadi
    1 — TP dari find_smart_tp (gap+fibonacci+swing+role-reversal), SL dari situ
    juga TAPI cuma dipake kalau jaraknya masuk akal (<=15%, sanity cap sama
    kayak produksi — lihat scheduler.py komentar soal MPIX). Fallback diem-diem
    ke support_resistance() 20-hari kalau smart TP gagal/data kurang."""
    levels = support_resistance(hist_slice)
    price_now = float(hist_slice["Close"].iloc[-1])
    if len(weekly_slice) < 5 or len(monthly_slice) < 2:
        return levels
    try:
        smart = find_smart_tp({"daily": hist_slice, "weekly": weekly_slice, "monthly": monthly_slice}, price_now)
    except Exception:
        return levels

    tp1, sl_anchor = smart["tp1"], smart["sl_anchor"]
    if tp1:
        levels["resistance"] = tp1["price"]
    if sl_anchor and (price_now - sl_anchor["price"]) / price_now <= 0.15:
        levels["stop_loss"] = round(sl_anchor["price"] * 0.98, 2)

    risk_pct = round((price_now - levels["stop_loss"]) / price_now * 100, 2)
    reward_pct = round((levels["resistance"] - price_now) / price_now * 100, 2)
    rr_ratio = round(reward_pct / risk_pct, 2) if risk_pct > 0 else 0.0
    levels["risk_pct"], levels["reward_pct"], levels["rr_ratio"] = risk_pct, reward_pct, rr_ratio
    levels["rr_label"] = rr_label(rr_ratio)
    return levels


def _simulate_ticker(ticker: str, hist: pd.DataFrame, weekly: pd.DataFrame, monthly: pd.DataFrame,
                      timeout_days: int = SIGNAL_TIMEOUT_DAYS, ihsg_uptrend: dict | None = None) -> list[dict]:
    """Jalan sepanjang histori 1 ticker, cari hari breakout_confirmed (technical_score
    >=12) + RR gate (>=1.5) lolos, simulasiin trade sampe TP/SL/timeout/masih
    kebuka pas data abis. 1 posisi aktif per ticker di satu waktu (skip sinyal baru
    selama posisi lama masih jalan — sama logic "udah dialert hari ini" produksi,
    disederhanain jadi "udah ada posisi aktif").

    timeout_days parameterized (bukan langsung pake SIGNAL_TIMEOUT_DAYS) — buat
    eksperimen "compression butuh timeout lebih panjang gak" TANPA ngubah
    konstanta yang mirror produksi asli."""
    if len(hist) < 60:
        return []

    feats = compute_features_series(hist)
    value_traded_idr = hist["Close"] * hist["Volume"]
    tech_score = _technical_score_series(feats, value_traded_idr)
    close = hist["Close"].values
    dates = hist.index

    trades = []
    i = 20  # butuh minimal 20 hari histori buat fitur pertama valid
    n = len(hist)
    while i < n:
        if pd.isna(tech_score.iloc[i]) or tech_score.iloc[i] < BREAKOUT_TECHNICAL_THRESHOLD:
            i += 1
            continue

        entry_date = dates[i]
        # slice weekly/monthly SAMPE tanggal entry doang — jangan bocor liat masa depan
        weekly_slice = weekly[weekly.index <= entry_date]
        monthly_slice = monthly[monthly.index <= entry_date]
        levels = _smart_levels(hist.iloc[:i + 1], weekly_slice, monthly_slice)
        if levels["rr_ratio"] < MIN_RR_RATIO:
            i += 1
            continue
        if levels["risk_pct"] > MAX_RISK_PCT or levels["reward_pct"] > MAX_REWARD_PCT:
            i += 1
            continue  # SL/TP kejauhan buat swing beneran, sama sanity check kayak produksi

        entry_price = float(close[i])
        target, stop = levels["resistance"], levels["stop_loss"]

        # compression setup (teknik mentor: sideways lama + MA5/10/20 ngumpul
        # SEBELUM breakout) — dicek TERPISAH dari gate breakout+volume utama,
        # cuma buat label/perbandingan, BUKAN syarat tambahan buat masuk trade
        # (sama kayak produksi: ini konteks pemilihan Groq, bukan hard gate)
        hist_upto = hist.iloc[:i + 1]
        sideways_days = _sideways_days(hist_upto)
        ma5 = float(hist_upto["Close"].tail(5).mean())
        ma10 = float(hist_upto["Close"].tail(10).mean())
        ma20 = float(hist_upto["Close"].tail(20).mean())
        is_compression = compression_setup(ma5, ma10, ma20, entry_price, sideways_days, levels["rr_ratio"])

        # versi VCP (Minervini) - 2 elemen yang ilang di compression_setup lama:
        # volume harus MENGECIL selama base, DAN market broad (IHSG) harus lagi
        # uptrend. is_compression_vcp lebih ketat, cuma True kalau ketiganya
        # (compression lama + volume dry-up + IHSG uptrend) sama-sama kepenuhi.
        volume_dry_up = _volume_dry_up(hist_upto, sideways_days) if is_compression else False
        market_uptrend = bool(ihsg_uptrend and ihsg_uptrend.get(entry_date.date(), False)) if ihsg_uptrend else False
        is_compression_vcp = is_compression and volume_dry_up and market_uptrend

        status, exit_price, exit_idx = None, None, None
        j = i + 1
        while j < n:
            days_open = (dates[j] - entry_date).days
            c = float(close[j])
            if c >= target:
                status, exit_price, exit_idx = "tp_hit", c, j
                break
            if c <= stop:
                status, exit_price, exit_idx = "sl_hit", c, j
                break
            if days_open > timeout_days:
                status, exit_price, exit_idx = "timeout", c, j
                break
            j += 1

        if status is None:
            break  # data abis sebelum posisi ditutup — jangan diitung (belum tau hasilnya)

        outcome_pct = round((exit_price - entry_price) / entry_price * 100, 2)
        trades.append({
            "ticker": ticker,
            "entry_date": entry_date.strftime("%Y-%m-%d"),
            "exit_date": dates[exit_idx].strftime("%Y-%m-%d"),
            "status": status,
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "outcome_pct": outcome_pct,
            "days_held": (dates[exit_idx] - entry_date).days,
            "compression_setup": is_compression,
            "compression_vcp": is_compression_vcp,
            "rr_ratio": levels["rr_ratio"],
            "stop_loss": round(stop, 2),  # dipake portfolio_backtest.py buat position sizing (risk per share)
        })
        i = exit_idx + 1  # lanjut nyari sinyal baru abis posisi lama ditutup

    return trades


def _process_one(ticker: str, timeout_days: int = SIGNAL_TIMEOUT_DAYS, ihsg_uptrend: dict | None = None) -> list[dict]:
    try:
        t = yf.Ticker(f"{ticker}.JK")
        hist = t.history(period="5y").dropna(subset=["Close"])
        weekly = t.history(period="max", interval="1wk").dropna(subset=["Close"])
        monthly = t.history(period="max", interval="1mo").dropna(subset=["Close"])
    except Exception:
        return []
    return _simulate_ticker(ticker, hist, weekly, monthly, timeout_days, ihsg_uptrend)


def run(tickers: list[str] | None = None, timeout_days: int = SIGNAL_TIMEOUT_DAYS) -> None:
    if tickers is None:
        res = supabase.table("scanner_cache").select("ticker").execute()
        tickers = [r["ticker"] for r in res.data]

    print("fetch IHSG buat filter uptrend market (VCP eksperimen)...")
    try:
        ihsg_uptrend = _fetch_ihsg_uptrend_map()
    except Exception:
        print("gagal fetch IHSG - eksperimen compression_vcp bakal selalu False")
        ihsg_uptrend = {}

    print(f"backtest Swing (breakout+volume) pake {len(tickers)} ticker, histori 5 tahun tiap ticker, timeout={timeout_days} hari...")
    all_trades: list[dict] = []
    errors = 0
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_process_one, t, timeout_days, ihsg_uptrend): t for t in tickers}
        for future in as_completed(futures):
            try:
                all_trades.extend(future.result())
            except Exception:
                errors += 1
    print(f"selesai ({errors} ticker gagal fetch) -> {len(all_trades)} trade simulasi kekumpul\n")

    if not all_trades:
        print("GAGAL — gak ada trade yang kesimulasi sama sekali.")
        return

    df = pd.DataFrame(all_trades)

    print("=== SEMUA breakout (gate umum technical_score>=12) ===")
    overall = _print_stats(df)

    # perbandingan: breakout yang DIDAHULUI compression (sideways lama, teknik
    # mentor) vs breakout biasa — compression_setup di produksi cuma jadi
    # KONTEKS TAMBAHAN buat Groq milih, bukan hard gate, jadi kedua kelompok
    # ini SAMA-SAMA lolos gate utama, cuma beda punya "riwayat sideways" atau enggak
    comp_df = df[df["compression_setup"]]
    non_comp_df = df[~df["compression_setup"]]
    print(f"\n=== Breakout DIDAHULUI compression ({len(comp_df)} trade) — teknik mentor ===")
    comp_stats = _print_stats(comp_df) if not comp_df.empty else None
    print(f"\n=== Breakout BIASA, gak ada compression ({len(non_comp_df)} trade) ===")
    non_comp_stats = _print_stats(non_comp_df) if not non_comp_df.empty else None

    # versi VCP (Minervini) - compression_setup LAMA + volume dry-up + IHSG
    # uptrend. Ini tes YANG FAIR lawan teknik asli (bukan versi kasar di atas).
    vcp_df = df[df["compression_vcp"]]
    print(f"\n=== Breakout compression VERSI VCP ({len(vcp_df)} trade) — + volume dry-up + IHSG uptrend ===")
    vcp_stats = _print_stats(vcp_df) if not vcp_df.empty else None

    if overall["expectancy_pct"] <= 0:
        print("\n⚠️  PERINGATAN: expectancy <=0 — rata-rata gate breakout+volume ini RUGI")
        print("    kalau semua sinyal yang lolos diambil semua, bukan cuma 1-2 pick/minggu terbaik.")

    result = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "n_tickers": len(tickers),
        "overall": overall,
        "compression_setup": comp_stats,
        "no_compression": non_comp_stats,
        "compression_vcp": vcp_stats,
    }
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nHasil tersimpen: {RESULTS_PATH}")


def _print_stats(df: pd.DataFrame) -> dict:
    wins = df[df["outcome_pct"] > 0]
    losses = df[df["outcome_pct"] <= 0]
    win_rate = round(len(wins) / len(df) * 100, 1) if len(df) else 0.0
    avg_win = round(wins["outcome_pct"].mean(), 2) if not wins.empty else 0.0
    avg_loss = round(losses["outcome_pct"].mean(), 2) if not losses.empty else 0.0
    avg_days = round(df["days_held"].mean(), 1) if len(df) else 0.0
    expectancy = round(df["outcome_pct"].mean(), 2) if len(df) else 0.0
    by_status = df["status"].value_counts().to_dict()

    print(f"Total trade   : {len(df)}")
    print(f"Win rate      : {win_rate}% ({len(wins)} untung / {len(losses)} rugi-atau-flat)")
    print(f"Avg win       : +{avg_win}%")
    print(f"Avg loss      : {avg_loss}%")
    print(f"Expectancy    : {expectancy}% per trade")
    print(f"Avg hari held : {avg_days} hari")
    print(f"Breakdown     : {by_status}")

    return {
        "n_trades": len(df), "win_rate_pct": win_rate, "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss, "expectancy_pct": expectancy, "avg_days_held": avg_days,
        "breakdown": by_status,
    }


def _self_check() -> None:
    """Parity check: technical_score vectorized (di atas) HARUS balikin angka
    sama persis kayak scoring.py::technical_score (scalar, dipake live) buat
    input yang sama — kalau beda, backtest ngukur gate yang SALAH."""
    from scoring import technical_score as scalar_technical_score

    cases = [
        (7000, 6800, 3.5, 0.85, 2_000_000_000),
        (6800, 6900, 1.2, 0.3, 200_000_000),
        (5000, 5100, 0.5, 0.5, 400_000_000),
    ]
    for price_now, resistance_prior, volume_ratio, close_position_pct, value_traded_idr in cases:
        expected = scalar_technical_score(price_now, resistance_prior, volume_ratio, close_position_pct, value_traded_idr)
        breakout_pct = (price_now - resistance_prior) / resistance_prior * 100
        feats = pd.DataFrame({"breakout_pct": [breakout_pct], "volume_ratio": [volume_ratio], "close_position_pct": [close_position_pct]})
        got = int(_technical_score_series(feats, pd.Series([value_traded_idr])).iloc[0])
        assert got == expected, f"mismatch: vectorized={got} scalar={expected} buat case {price_now, resistance_prior, volume_ratio, close_position_pct, value_traded_idr}"
    print("backtest.py self-check OK: technical_score vectorized == scalar\n")


if __name__ == "__main__":
    _self_check()
    run()
