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
