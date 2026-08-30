"""
Backtest sederhana: apa sentiment berita (dari daily_market_intel, hasil Groq
summarize intel WhatsApp/Telegram) beneran PREDIKTIF buat pergerakan harga
beberapa hari ke depan? Validasi pipeline berita-nya sendiri, sama semangat
kayak backtest.py buat strategi Swing. Manual run (python news_backtest.py).

CATATAN JUJUR — WAJIB dibaca sebelum percaya hasilnya:
Data intel baru ada dari sekitar 17 hari (~189 entry per akhir Agustus 2026).
Hasil di sini BARU AWAL, BUKAN kesimpulan statistik yang reliable — sample
terlalu kecil & span waktu terlalu pendek buat generalisasi. Script ini
REUSABLE — makin lama data intel numpuk, makin valid hasilnya kalau di-run
ulang nanti (bulan depan, dst).
"""
import sys
import os
import json
from collections import defaultdict
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import yfinance as yf

from config import supabase

FUTURE_DAYS = 3  # bandingin harga N hari trading SETELAH tanggal berita

_DIR = os.path.dirname(__file__)
RESULTS_PATH = os.path.join(_DIR, "news_backtest_results.json")


def _fetch_mentions() -> list[dict]:
    """[{ticker, date, sentiment}, ...] — dari SEMUA daily_market_intel yang
    ada sentiment + saham_disebut valid."""
    res = supabase.table("daily_market_intel").select("tanggal,summary_ai").execute()
    mentions = []
    for row in res.data:
        s = row.get("summary_ai") or {}
        sentiment = s.get("sentiment")
        if sentiment not in ("bullish", "bearish", "neutral", "mixed"):
            continue
        for ticker in s.get("saham_disebut") or []:
            mentions.append({"ticker": ticker, "date": row["tanggal"], "sentiment": sentiment})
    return mentions


def _price_change_after(hist: pd.DataFrame, date_str: str, future_days: int) -> float | None:
    """Perubahan harga dari Close PERTAMA pada/setelah date_str, ke Close
    `future_days` bar trading kemudian. None kalau data kurang (berita
    terlalu baru, belum ada cukup hari trading setelahnya)."""
    idx = hist.index
    target = pd.Timestamp(date_str)
    if idx.tz is not None:
        target = target.tz_localize(idx.tz)
    after = hist[idx >= target]
    if len(after) < future_days + 1:
        return None
    base = float(after["Close"].iloc[0])
    future = float(after["Close"].iloc[future_days])
    if not base:
        return None
    return (future - base) / base * 100


def run() -> None:
    mentions = _fetch_mentions()
    print(f"{len(mentions)} mention saham dari daily_market_intel...")
    if not mentions:
        print("GAGAL — belum ada intel yang ke-tag sentiment+saham.")
        return

    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for m in mentions:
        by_ticker[m["ticker"]].append(m)

    results = []
    errors = 0
    for ticker, items in by_ticker.items():
        try:
            hist = yf.Ticker(f"{ticker}.JK").history(period="3mo").dropna(subset=["Close"])
        except Exception:
            errors += 1
            continue
        if hist.empty:
            errors += 1
            continue
        for m in items:
            chg = _price_change_after(hist, m["date"], FUTURE_DAYS)
            if chg is not None:
                results.append({**m, "price_change_pct": round(chg, 2)})

    print(f"selesai ({errors} ticker gagal fetch) -> {len(results)}/{len(mentions)} mention berhasil dicocokin ke data harga\n")
    if not results:
        print("GAGAL — gak ada mention yang punya data harga cukup (berita kejadian pas ini terlalu baru, belum ada 3 hari trading berikutnya).")
        return

    df = pd.DataFrame(results)
    summary = {}
    for sentiment in ("bullish", "bearish", "neutral", "mixed"):
        sub = df[df["sentiment"] == sentiment]
        if sub.empty:
            continue
        avg = round(sub["price_change_pct"].mean(), 2)
        summary[sentiment] = {"n": len(sub), "avg_price_change_pct": avg}
        print(f"{sentiment:10s}: {len(sub):3d} mention, rata-rata perubahan harga {FUTURE_DAYS} hari kemudian: {avg:+.2f}%")

    overall_avg = round(df["price_change_pct"].mean(), 2)
    print(f"\n{'overall':10s}: {len(df):3d} mention, rata-rata semua sentiment: {overall_avg:+.2f}%")

    bullish_avg = summary.get("bullish", {}).get("avg_price_change_pct")
    bearish_avg = summary.get("bearish", {}).get("avg_price_change_pct")
    if bullish_avg is not None and bearish_avg is not None:
        if bullish_avg > bearish_avg:
            print(f"\nArah masuk akal: bullish ({bullish_avg:+.2f}%) > bearish ({bearish_avg:+.2f}%).")
        else:
            print(f"\n⚠️  Arah KEBALIK dari ekspektasi: bullish ({bullish_avg:+.2f}%) <= bearish ({bearish_avg:+.2f}%) — bisa noise data kecil, jangan buru-buru simpulin ada yang salah.")

    print(f"\n⚠️  PENTING: data intel span cuma ~17 hari ({len(mentions)} mention total) — INI BUKAN")
    print("    kesimpulan statistik yang reliable, sample kekecilan buat generalisasi apapun.")
    print("    Run ulang script ini beberapa bulan lagi begitu data numpuk lebih banyak.")

    result = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "n_mentions_total": len(mentions),
        "n_mentions_matched": len(results),
        "future_days": FUTURE_DAYS,
        "by_sentiment": summary,
        "overall_avg_price_change_pct": overall_avg,
        "caveat": "Data intel baru ~17 hari span - BUKAN kesimpulan reliable, sample kekecilan.",
    }
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nHasil tersimpen: {RESULTS_PATH}")


if __name__ == "__main__":
    run()
