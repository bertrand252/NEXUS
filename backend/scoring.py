"""
Accumulation scoring, max 100 total:
  Volume Score        /25  -> REAL, dari yfinance (rasio volume hari ini vs rata-rata 20 hari)
  Price Score         /25  -> REAL, dari yfinance (momentum harga 5 hari + posisi vs high/low)
  Accumulation Score  /30  -> MOCK, butuh data broker summary (berbayar, belum ada sumber gratis)
  Technical Score     /20  -> MOCK, butuh Bollinger Bands + Parabolic SAR (belum diimplementasi)

Mock pakai hash ticker biar deterministik antar refresh (bukan random tiap call),
supaya demo di sidang gak keliatan lompat-lompat gak jelas.
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


def compute_score(ticker: str, volume_today: float, volume_avg20: float,
                   price_now: float, price_5d_ago: float, low_20d: float, high_20d: float) -> dict:
    vol = volume_score(volume_today, volume_avg20)
    price = price_score(price_now, price_5d_ago, low_20d, high_20d)
    accumulation = _deterministic_mock(ticker, "accumulation", 30)  # TODO: real broker summary data
    technical = _deterministic_mock(ticker, "technical", 20)         # TODO: real BB + Parabolic SAR

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