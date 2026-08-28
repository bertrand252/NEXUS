"""
Deteksi support/resistance simpel dari OHLC, buat alert Telegram (chart
annotation + entry/risk calculation). Metodenya sengaja simpel — 20-hari
low/high sebagai support/resistance, bukan pivot-point kompleks — jujur &
gampang dijelasin di alert, bukan angka presisi yang keliatan meyakinkan
padahal ngarang. Upgrade nanti kalau kerasa kurang akurat.
"""


def support_resistance(hist) -> dict:
    support = float(hist["Low"].tail(20).min())
    resistance = float(hist["High"].tail(20).max())
    price_now = float(hist["Close"].iloc[-1])

    # zona entry deket HARGA SEKARANG (bukan nempel di support 20 hari) — kalau
    # udah breakout, harga udah gerak jauh dari support lama, entry yang nempel
    # di situ gak realistis buat "kejemput". SL/TP tetep dari support/resistance
    # terdekat, itu emang bener.
    entry_low = round(price_now * 0.99, 2)
    entry_high = round(price_now * 1.02, 2)
    stop_loss = round(support * 0.98, 2)   # stop loss: 2% di bawah support terdekat
    risk_pct = round((price_now - stop_loss) / price_now * 100, 2)

    return {
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "risk_pct": risk_pct,
    }


def detect_pivot_zones(hist, lookback: int = 60, swing_window: int = 3,
                        cluster_pct: float = 0.015, top_n: int = 3) -> list[dict]:
    """Level support/resistance TAMBAHAN ("AI Zones" di chart) — BEDA dari
    `support_resistance()` di atas (yang resmi, dipake buat SL/TP/breakout
    scoring/alert, JANGAN diubah). Ini sinyal pelengkap, dihitung ALGORITMIK
    (bukan Groq nebak angka — LLM gampang ngarang angka presisi):
    1. Cari swing high/low — bar yang high/low-nya lebih ekstrem dari
       `swing_window` bar di kanan-kirinya (titik balik harga asli).
    2. Cluster swing point yang berdekatan (dalam `cluster_pct`) — makin
       sering harga "nyentuh" & mantul di sekitar situ, makin kuat zona-nya.
    3. Balikin top-N cluster per tipe, diurut dari yang paling sering
       disentuh."""
    recent = hist.tail(lookback)
    highs = recent["High"].to_numpy()
    lows = recent["Low"].to_numpy()
    n = len(recent)

    swing_highs, swing_lows = [], []
    for i in range(swing_window, n - swing_window):
        window = slice(i - swing_window, i + swing_window + 1)
        if highs[i] == highs[window].max():
            swing_highs.append(float(highs[i]))
        if lows[i] == lows[window].min():
            swing_lows.append(float(lows[i]))

    def _cluster(points: list[float]) -> list[dict]:
        if not points:
            return []
        points.sort()
        clusters = [[points[0]]]
        for p in points[1:]:
            if abs(p - clusters[-1][-1]) / clusters[-1][-1] <= cluster_pct:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        return [{"price": round(sum(c) / len(c), 2), "touches": len(c)} for c in clusters]

    resistance_zones = sorted(_cluster(swing_highs), key=lambda z: z["touches"], reverse=True)[:top_n]
    support_zones = sorted(_cluster(swing_lows), key=lambda z: z["touches"], reverse=True)[:top_n]

    zones = (
        [{**z, "type": "resistance"} for z in resistance_zones]
        + [{**z, "type": "support"} for z in support_zones]
    )
    return sorted(zones, key=lambda z: z["price"], reverse=True)
