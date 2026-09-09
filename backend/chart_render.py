"""
Render candlestick chart + volume + garis support/resistance jadi PNG, buat
lampiran foto di alert Telegram. Pake mplfinance (bukan line chart matplotlib
polos) biar mirip tampilan trading platform beneran.
"""
import io
import matplotlib
matplotlib.use("Agg")  # server-side render doang, gak butuh display
import numpy as np
import pandas as pd
import mplfinance as mpf

BG = "#0B0F1A"
GRID = "#1F2937"
TEXT = "#94A3B8"
GREEN = "#10B981"
RED = "#EF4444"
CYAN = "#06B6D4"


def _base_style():
    return mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=mpf.make_marketcolors(up=GREEN, down=RED, edge="inherit", wick="inherit", volume="inherit"),
        facecolor=BG, figcolor=BG, gridcolor=GRID, gridstyle="--",
        rc={"axes.edgecolor": GRID, "axes.labelcolor": TEXT, "xtick.color": TEXT, "ytick.color": TEXT},
    )


def render_chart(ticker: str, hist, support: float, resistance: float, channel: dict | None = None) -> bytes:
    df = hist.rename(columns={"Open": "Open", "High": "High", "Low": "Low", "Close": "Close", "Volume": "Volume"})

    style = _base_style()

    hlines = dict(
        hlines=[support, resistance],
        colors=[GREEN, RED],
        linestyle="--",
        linewidths=1.2,
    )

    plot_kwargs = dict(
        type="candle",
        style=style,
        volume=True,
        hlines=hlines,
        title=f"\n{ticker} — 2 Bulan Terakhir",
        figsize=(8, 5.5),
    )

    # trend channel (levels.py::detect_trend_channel) — 2 garis diagonal
    # nyambungin swing high/low terbaru, kayak yang biasa digambar manual di
    # TradingView. Optional (None kalau swing point-nya kurang buat nentuin
    # garis yang masuk akal, JANGAN maksa gambar garis ngasal).
    if channel:
        plot_kwargs["alines"] = dict(
            alines=[channel["upper"], channel["lower"]],
            colors=[CYAN, CYAN],
            linestyle="-.",
            linewidths=1.0,
        )

    buf = io.BytesIO()
    mpf.plot(df, savefig=dict(fname=buf, format="png", dpi=130, facecolor=BG), **plot_kwargs)
    buf.seek(0)
    return buf.getvalue()


def render_outcome_chart(
    ticker: str, hist, entry_price: float, entry_date, exit_price: float, exit_date,
    target: float, stop_loss: float, outcome: str, pad_before: int = 18, pad_after: int = 5,
) -> bytes:
    """Chart posisi DITUTUP (TP/SL kena) — 1 gambar nunjukin PERJALANAN dari
    entry ke exit (bukan 2 chart 'before/after' terpisah, gak worth kompleksitas
    tambahan buat 1 pesan Telegram). Beda dari render_chart() (dipake pas alert
    PERTAMA kali, posisi masih hipotetis): di sini entry/exit-nya BENERAN
    kejadian, jadi digambar sebagai marker (bukan cuma garis rencana) + area
    entry-exit diarsir tipis (ijo kalau untung/tp_hit, merah kalau rugi/sl_hit).
    Window otomatis di-crop ke sekitar entry-exit (pad_before/pad_after hari)
    biar journey-nya keliatan jelas, bukan ketelen chart 2 bulan penuh kalau
    entry-exitnya cuma seminggu."""
    tz = hist.index.tz

    def _to_index_tz(dt) -> pd.Timestamp:
        ts = pd.Timestamp(dt)
        if tz is None:
            return ts.tz_localize(None) if ts.tzinfo else ts
        return ts.tz_convert(tz) if ts.tzinfo else ts.tz_localize(tz)

    entry_ts = _to_index_tz(entry_date)
    exit_ts = _to_index_tz(exit_date)
    entry_idx_full = hist.index.get_indexer([entry_ts], method="nearest")[0]
    exit_idx_full = hist.index.get_indexer([exit_ts], method="nearest")[0]
    lo = max(0, entry_idx_full - pad_before)
    hi = min(len(hist), exit_idx_full + pad_after + 1)
    df = hist.iloc[lo:hi].rename(columns={"Open": "Open", "High": "High", "Low": "Low", "Close": "Close", "Volume": "Volume"})

    outcome_color = GREEN if outcome == "tp_hit" else RED
    outcome_label = "TP TERCAPAI" if outcome == "tp_hit" else "STOP LOSS"

    entry_idx = df.index[df.index.get_indexer([entry_ts], method="nearest")[0]]
    exit_idx = df.index[df.index.get_indexer([exit_ts], method="nearest")[0]]
    span = float(df["High"].max() - df["Low"].min()) or entry_price * 0.05
    entry_y = entry_price - span * 0.09
    exit_y = exit_price + span * 0.09 if outcome == "tp_hit" else exit_price - span * 0.09

    entry_marker = pd.Series(np.nan, index=df.index)
    exit_marker = pd.Series(np.nan, index=df.index)
    entry_marker.loc[entry_idx] = entry_y
    exit_marker.loc[exit_idx] = exit_y

    addplot = [
        mpf.make_addplot(entry_marker, type="scatter", markersize=200, marker="^", color=CYAN, panel=0, edgecolors="white", linewidths=0.8),
        mpf.make_addplot(exit_marker, type="scatter", markersize=220, marker=("v" if outcome == "sl_hit" else "^"), color=outcome_color, panel=0, edgecolors="white", linewidths=0.8),
    ]
    hlines = dict(hlines=[entry_price, target, stop_loss], colors=["#CBD5E1", GREEN, RED], linestyle=["-", "--", "--"], linewidths=[1.1, 1.3, 1.3])

    fig, axes = mpf.plot(
        df, type="candle", style=_base_style(), volume=True, addplot=addplot, hlines=hlines,
        title=f"\n{ticker} — {outcome_label}", figsize=(9, 5.8), returnfig=True, datetime_format="%d %b",
    )
    ax = axes[0]
    ax.axvspan(df.index.get_loc(entry_idx), df.index.get_loc(exit_idx), color=outcome_color, alpha=0.10)
    ax.annotate(
        f"ENTRY\nRp{entry_price:,.0f}", xy=(df.index.get_loc(entry_idx), entry_y), xytext=(0, -32),
        textcoords="offset points", ha="center", color=CYAN, fontsize=9, fontweight="bold",
    )
    exit_label = "TP" if outcome == "tp_hit" else "SL"
    ax.annotate(
        f"{exit_label}\nRp{exit_price:,.0f}", xy=(df.index.get_loc(exit_idx), exit_y),
        xytext=(0, 18 if outcome == "tp_hit" else -32), textcoords="offset points",
        ha="center", color=outcome_color, fontsize=9, fontweight="bold",
    )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, facecolor=BG, bbox_inches="tight")
    buf.seek(0)
    return buf.getvalue()
