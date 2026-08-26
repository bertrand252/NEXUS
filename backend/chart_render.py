"""
Render candlestick chart + volume + garis support/resistance jadi PNG, buat
lampiran foto di alert Telegram. Pake mplfinance (bukan line chart matplotlib
polos) biar mirip tampilan trading platform beneran.
"""
import io
import matplotlib
matplotlib.use("Agg")  # server-side render doang, gak butuh display
import mplfinance as mpf

BG = "#0B0F1A"
GRID = "#1F2937"
TEXT = "#94A3B8"
GREEN = "#10B981"
RED = "#EF4444"
CYAN = "#06B6D4"


def render_chart(ticker: str, hist, support: float, resistance: float) -> bytes:
    df = hist.rename(columns={"Open": "Open", "High": "High", "Low": "Low", "Close": "Close", "Volume": "Volume"})

    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=mpf.make_marketcolors(up=GREEN, down=RED, edge="inherit", wick="inherit", volume="inherit"),
        facecolor=BG, figcolor=BG, gridcolor=GRID, gridstyle="--",
        rc={"axes.edgecolor": GRID, "axes.labelcolor": TEXT, "xtick.color": TEXT, "ytick.color": TEXT},
    )

    hlines = dict(
        hlines=[support, resistance],
        colors=[GREEN, RED],
        linestyle="--",
        linewidths=1.2,
    )

    buf = io.BytesIO()
    mpf.plot(
        df,
        type="candle",
        style=style,
        volume=True,
        hlines=hlines,
        title=f"\n{ticker} — 2 Bulan Terakhir",
        figsize=(8, 5.5),
        savefig=dict(fname=buf, format="png", dpi=130, facecolor=BG),
    )
    buf.seek(0)
    return buf.getvalue()
