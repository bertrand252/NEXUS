"""
Backtest PORTFOLIO — beda dari backtest.py (yang ngukur "apa gate breakout+
volume-nya profitable secara RATA-RATA per trade, tiap ticker independen").
Ini simulasiin trading BENERAN dari 1 modal awal bersama: position sizing
(% risk per trade, sama logic Position Sizing Calculator di StockDetail),
posisi konkuren dibatasin (gak bisa all-in ke semua sinyal sekaligus, modal
kebagi), biar keliatan equity curve / return / drawdown riil kalau strategi
ini beneran ditradingin — bukan cuma "rata-rata tiap trade untung/rugi berapa".

Reuse trade-generation dari backtest.py (_process_one) — sinyal yang SAMA
persis (gate + sanity cap), cuma cara ngitung HASIL AKHIRNYA beda. Manual run
(python portfolio_backtest.py).

CATATAN JUJUR — approksimasi, bukan simulasi persis:
1. Equity curve di-update di TIAP event entry/exit doang (bukan mark-to-market
   harian) — posisi yang lagi OPEN dihitung di harga ENTRY-nya (bukan harga
   pasar hari itu), jadi drawdown INTRADAY-TRADE gak ketangkep, cuma drawdown
   dari urutan hasil realized. Approksimasi wajar buat gambaran kasar, BUKAN
   angka presisi buat klaim risk management.
2. Position sizing pake risk_amount dari MODAL AWAL (bukan modal berjalan) —
   biar risk per trade konsisten, gak makin gede pas lagi untung beruntun
   (itu praktik position sizing yang lebih konservatif/umum dipake).
3. MAX_CONCURRENT_POSITIONS itu ASUMSI diversifikasi wajar (10), bukan angka
   dari riset — bisa diubah sesuai preferensi risiko.
"""
import sys
import os
import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import yfinance as yf

from config import supabase
from backtest import _process_one

INITIAL_CAPITAL = 10_000_000  # Rp10 juta, angka bulat buat ilustrasi
RISK_PCT_PER_TRADE = 2.0      # sama default Position Sizing Calculator (StockDetail)
MAX_CONCURRENT_POSITIONS = 10  # diversifikasi wajar — ASUMSI, bukan dari riset
MAX_POSITION_PCT = 20          # BUG NYATA ketemu pas riset: saham murah/tipis (GRPH: entry Rp50,
                                # SL Rp49, risk/share Rp1) bikin position sizing risk-based DOANG
                                # ngasih size gila (60 juta lembar @ modal 3M = 100% modal 1 saham).
                                # Cap independen ini (sama kayak fix Position Sizing Calculator di
                                # StockDetail.jsx) - JANGAN lebih dari 20% modal per saham, gak
                                # peduli seberapa kecil risk/share-nya.
LOT_SIZE = 100                 # 1 lot IDX = 100 lembar

_DIR = os.path.dirname(__file__)
RESULTS_PATH = os.path.join(_DIR, "portfolio_backtest_results.json")


def _gather_all_trades(tickers: list[str]) -> list[dict]:
    all_trades = []
    errors = 0
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_process_one, t): t for t in tickers}
        for future in as_completed(futures):
            try:
                all_trades.extend(future.result())
            except Exception:
                errors += 1
    print(f"selesai ({errors} ticker gagal fetch) -> {len(all_trades)} trade sinyal kekumpul dari {len(tickers)} ticker")
    return all_trades


def simulate_portfolio(trades: list[dict], initial_capital: float = INITIAL_CAPITAL,
                        max_concurrent_positions: int = MAX_CONCURRENT_POSITIONS) -> dict:
    """Jalanin trade-trade (dari SEMUA ticker, dicampur kronologis) lewat 1
    modal bersama — position sizing + batas posisi konkuren beneran diterapin,
    bukan tiap ticker independen kayak backtest.py.

    initial_capital/max_concurrent_positions diparameterin (bukan langsung
    pake konstanta) — buat eksperimen "modal segini cukup gak buat ngambil
    mayoritas sinyal" TANPA ngubah default produksi."""
    for i, t in enumerate(trades):
        t["_id"] = i

    events = []
    for t in trades:
        events.append({"date": t["entry_date"], "order": 0, "type": "entry", "trade": t})
        events.append({"date": t["exit_date"], "order": 1, "type": "exit", "trade": t})
    # exit DULUAN di hari yang sama (order=1 duluan?) -- enggak, exit harus
    # dieksekusi SEBELUM entry baru biar modalnya freed duluan di hari yg
    # sama -> exit order=0, entry order=1
    for e in events:
        e["order"] = 0 if e["type"] == "exit" else 1
    events.sort(key=lambda e: (e["date"], e["order"]))

    cash = float(initial_capital)
    open_positions: dict[int, dict] = {}
    equity_curve = []  # [{"date", "equity"}]
    taken = skipped_capacity = skipped_capital = skipped_bad_risk = 0

    def _current_equity() -> float:
        mark = sum(p["shares"] * p["entry_price"] for p in open_positions.values())
        return cash + mark

    for e in events:
        t = e["trade"]
        if e["type"] == "exit":
            pos = open_positions.pop(t["_id"], None)
            if pos:
                cash += pos["shares"] * t["exit_price"]
                equity_curve.append({"date": e["date"], "equity": round(_current_equity(), 2)})
            continue

        # entry
        if len(open_positions) >= max_concurrent_positions:
            skipped_capacity += 1
            continue
        risk_per_share = t["entry_price"] - t["stop_loss"]
        if risk_per_share <= 0:
            skipped_bad_risk += 1
            continue
        risk_amount = initial_capital * (RISK_PCT_PER_TRADE / 100)
        shares_from_risk = int(risk_amount / risk_per_share)
        max_position_cost = initial_capital * (MAX_POSITION_PCT / 100)
        shares_from_max_position = int(max_position_cost / t["entry_price"]) if t["entry_price"] > 0 else 0
        shares = min(shares_from_risk, shares_from_max_position)
        shares = (shares // LOT_SIZE) * LOT_SIZE
        if shares < LOT_SIZE:
            skipped_bad_risk += 1
            continue
        cost = shares * t["entry_price"]
        if cost > cash:
            skipped_capital += 1
            continue
        cash -= cost
        open_positions[t["_id"]] = {"shares": shares, "entry_price": t["entry_price"]}
        taken += 1
        equity_curve.append({"date": e["date"], "equity": round(_current_equity(), 2)})

    final_equity = _current_equity()
    total_return_pct = round((final_equity - initial_capital) / initial_capital * 100, 2)

    peak = initial_capital
    max_dd_pct = 0.0
    for pt in equity_curve:
        peak = max(peak, pt["equity"])
        dd = (peak - pt["equity"]) / peak * 100
        max_dd_pct = max(max_dd_pct, dd)

    return {
        "initial_capital": initial_capital,
        "final_equity": round(final_equity, 2),
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": round(max_dd_pct, 2),
        "trades_taken": taken,
        "trades_skipped_max_positions": skipped_capacity,
        "trades_skipped_no_capital": skipped_capital,
        "trades_skipped_bad_risk": skipped_bad_risk,
        "trades_total_signals": len(trades),
        "equity_curve": equity_curve,
    }


def _ihsg_benchmark(start_date: str, end_date: str) -> float | None:
    """Return IHSG buy-and-hold di rentang tanggal yang sama, buat pembanding
    — "kalau modalnya ditaro IHSG doang dari awal, hasilnya berapa"."""
    try:
        hist = yf.Ticker("^JKSE").history(start=start_date, end=end_date).dropna(subset=["Close"])
        if len(hist) < 2:
            return None
        return round((float(hist["Close"].iloc[-1]) - float(hist["Close"].iloc[0])) / float(hist["Close"].iloc[0]) * 100, 2)
    except Exception:
        return None


def run(tickers: list[str] | None = None, initial_capital: float = INITIAL_CAPITAL,
        max_concurrent_positions: int = MAX_CONCURRENT_POSITIONS, trades: list[dict] | None = None) -> list[dict]:
    """trades bisa di-passing langsung (hasil _gather_all_trades sebelumnya)
    biar eksperimen ganti-ganti modal gak perlu fetch ulang 937 ticker tiap
    kali — return trades-nya biar caller bisa reuse buat run() berikutnya."""
    if trades is None:
        if tickers is None:
            res = supabase.table("scanner_cache").select("ticker").execute()
            tickers = [r["ticker"] for r in res.data]
        print(f"portfolio backtest pake {len(tickers)} ticker...")
        trades = _gather_all_trades(tickers)
    if not trades:
        print("GAGAL — gak ada trade sinyal yang kesimulasi sama sekali.")
        return trades

    print(f"\n=== Modal Rp{initial_capital:,.0f}, risk {RISK_PCT_PER_TRADE}%/trade, max {max_concurrent_positions} posisi konkuren ===")
    result = simulate_portfolio(trades, initial_capital, max_concurrent_positions)

    dates = sorted(set(t["entry_date"] for t in trades) | set(t["exit_date"] for t in trades))
    ihsg_return = _ihsg_benchmark(dates[0], dates[-1]) if dates else None
    result["ihsg_buy_and_hold_pct"] = ihsg_return
    result["period"] = {"from": dates[0], "to": dates[-1]} if dates else None

    print(f"\nModal awal      : Rp{result['initial_capital']:,.0f}")
    print(f"Equity akhir    : Rp{result['final_equity']:,.0f}")
    print(f"Total return    : {result['total_return_pct']:+.2f}%")
    if ihsg_return is not None:
        print(f"IHSG buy&hold   : {ihsg_return:+.2f}% (periode sama, {dates[0]} -> {dates[-1]})")
    print(f"Max drawdown    : -{result['max_drawdown_pct']:.2f}% (approksimasi, lihat catatan di docstring)")
    print(f"Trade diambil   : {result['trades_taken']} / {result['trades_total_signals']} sinyal")
    print(f"  - skip (posisi penuh) : {result['trades_skipped_max_positions']}")
    print(f"  - skip (modal abis)   : {result['trades_skipped_no_capital']}")
    print(f"  - skip (risk gak valid): {result['trades_skipped_bad_risk']}")

    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump({**result, "run_at": datetime.now(timezone.utc).isoformat()}, f, indent=2, ensure_ascii=False)
    print(f"Hasil tersimpen: {RESULTS_PATH}")
    return trades


if __name__ == "__main__":
    run()
