"""
Accumulation scoring, max 100 total:
  Volume Score        /25  -> REAL, dari yfinance (rasio volume hari ini vs rata-rata 20 hari)
  Price Score         /25  -> REAL, dari yfinance (momentum harga 5 hari + posisi vs high/low)
  Accumulation Score  /30  -> REAL kalau INVEZGO_API_KEY keisi (top BDM/foreign flow),
                              MOCK (hash ticker) kalau belum subscribe Invezgo
  Technical Score     /20  -> REAL, dari yfinance (breakout resistance 20 hari + volume,
                              lihat technical_score() buat alasan kenapa bukan RSI+MA20)
"""
import hashlib


def _deterministic_mock(ticker: str, seed_salt: str, max_val: int) -> int:
    h = hashlib.md5(f"{ticker}-{seed_salt}".encode()).hexdigest()
    return int(h, 16) % (max_val + 1)


def volume_score(volume_today: float, volume_avg20: float) -> int:
    """0-25. Rasio volume hari ini vs rata-rata 20 hari — makin tinggi rasio, makin kuat sinyal."""
    if not volume_avg20:
        return 0
    ratio = volume_today / volume_avg20
    if ratio >= 3.0:
        return 25
    if ratio >= 2.0:
        return 19
    if ratio >= 1.5:
        return 13
    if ratio >= 1.0:
        return 6
    return 0


def price_score(price_now: float, price_5d_ago: float, low_20d: float, high_20d: float) -> int:
    """0-25. Momentum 5 hari + posisi harga relatif terhadap range 20 hari."""
    if not price_5d_ago or not (high_20d - low_20d):
        return 0
    momentum_pct = (price_now - price_5d_ago) / price_5d_ago * 100
    position_pct = (price_now - low_20d) / (high_20d - low_20d)  # 0 = di low, 1 = di high

    score = 0
    score += 13 if momentum_pct > 5 else 9 if momentum_pct > 2 else 4 if momentum_pct > 0 else 0
    score += 12 if position_pct > 0.7 else 8 if position_pct > 0.4 else 4 if position_pct > 0.2 else 0
    return score


def accumulation_score(ticker: str, accum_lookup: dict | None) -> int:
    """0-30. Kalau Invezgo aktif, accum_lookup dibangun sekali per refresh dari
    GET /analysis/top/accumulation + /analysis/top/foreign (bukan per-ticker call —
    endpoint itu ngasih semua top mover market dalam 1 call). Ticker yang ke-flag
    di top accumulation list dikasih skor tinggi, top distribution dikasih rendah,
    yang gak ke-flag salah satu (paling banyak, wajar — cuma top mover yang di-list)
    dikasih skor netral, JUJUR nunjukin "gak ada sinyal kuat" bukan ngarang angka
    presisi kayak mock lama.

    CATATAN: pembagian skor ini first-pass heuristic, belum dikalibrasi lawan data
    Invezgo asli (belum ada API key aktif pas ini ditulis) — wajar disesuaikan lagi
    begitu keliatan bentuk & jumlah data asli dari top accumulation/foreign list.

    Kalau Invezgo belum di-subscribe (accum_lookup None), fallback ke hash mock lama."""
    if accum_lookup is None:
        return _deterministic_mock(ticker, "accumulation", 30)
    signal = accum_lookup.get(ticker)
    if signal == "accum":
        return 26
    if signal == "dist":
        return 4
    return 15  # netral — gak ke-flag di top accumulation atau top distribution


def technical_score(price_now: float, resistance_prior: float, volume_ratio: float,
                     close_position_pct: float, value_traded_idr: float) -> int:
    """0-20. "Buy on breakout+volume", bukan RSI/MA20 — filosofi trading yang dipake
    di sini: begitu berita/rame publik biasanya udah telat ("sell on news"), sinyal
    yang lebih awal itu harga tembus resistance range 20 hari DIKONFIRMASI volume
    gede (indikasi ada pemain besar masuk sebelum ramai diberitakan).

    2 filter tambahan biar gak gampang ketipu breakout palsu (modal kecil bisa
    "pompa" volume di saham tipis buat 1 hari doang):
    - close_position_pct: candle breakout HARUS nutup di bagian atas range
      harian-nya (bukan spike lalu didorong turun lagi / distribusi)
    - value_traded_idr: rupiah yang beneran ditransaksiin hari itu (price * volume)
      harus di atas ambang liquiditas kasar, biar gak gampang dimanipulasi modal kecil

    CATATAN: ini tetep heuristik murah dari data yfinance doang, BUKAN deteksi bandar/
    manipulasi beneran (butuh data broker summary asli, lihat Accumulation Score) —
    cuma ngurangin false positive paling gampang, bukan ngilangin risikonya total."""
    breakout_pct = (price_now - resistance_prior) / resistance_prior * 100 if resistance_prior else -100

    score = 0
    score += 10 if breakout_pct >= 0 else 5 if breakout_pct >= -3 else 0  # tembus, atau deket (dalem 3%)
    score += 6 if volume_ratio >= 3.0 else 4 if volume_ratio >= 2.0 else 2 if volume_ratio >= 1.5 else 0
    score += 2 if close_position_pct >= 0.7 else 1 if close_position_pct >= 0.4 else 0
    score += 2 if value_traded_idr >= 1_000_000_000 else 1 if value_traded_idr >= 300_000_000 else 0
    return score


def compute_score(ticker: str, volume_today: float, volume_avg20: float,
                   price_now: float, price_5d_ago: float, low_20d: float, high_20d: float,
                   resistance_prior: float, close_position_pct: float, value_traded_idr: float,
                   accum_lookup: dict | None = None) -> dict:
    vol = volume_score(volume_today, volume_avg20)
    price = price_score(price_now, price_5d_ago, low_20d, high_20d)
    accumulation = accumulation_score(ticker, accum_lookup)
    volume_ratio = volume_today / volume_avg20 if volume_avg20 else 0
    technical = technical_score(price_now, resistance_prior, volume_ratio, close_position_pct, value_traded_idr)

    total = vol + price + accumulation + technical
    return {
        "volume_score": vol,
        "price_score": price,
        "accumulation_score": accumulation,
        "technical_score": technical,
        "total_score": total,
        "signal": signal_label(total),
    }


def signal_label(score: int) -> str:
    if score >= 75:
        return "Strong"
    if score >= 50:
        return "Moderate"
    if score >= 25:
        return "Weak"
    return "None"