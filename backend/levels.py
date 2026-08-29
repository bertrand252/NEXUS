"""
Deteksi support/resistance simpel dari OHLC, buat alert Telegram (chart
annotation + entry/risk calculation). Metodenya sengaja simpel — 20-hari
low/high sebagai support/resistance, bukan pivot-point kompleks — jujur &
gampang dijelasin di alert, bukan angka presisi yang keliatan meyakinkan
padahal ngarang. Upgrade nanti kalau kerasa kurang akurat.
"""


def rr_label(rr_ratio: float) -> str:
    """Standar umum trading (riset: forex.com, heygotrade, dll) — buat Swing
    spesifik direkomendasiin 1:3 ke atas (beda dari scalping 1:1-1:1.5 atau
    day trading 1:1.5-1:2, yang time horizon-nya lebih pendek jadi RR
    minimalnya boleh lebih kecil)."""
    if rr_ratio < 1:
        return "Buruk"
    if rr_ratio < 2:
        return "Cukup"
    if rr_ratio < 3:
        return "Bagus"
    return "Sangat Bagus"


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
    reward_pct = round((resistance - price_now) / price_now * 100, 2)
    rr_ratio = round(reward_pct / risk_pct, 2) if risk_pct > 0 else 0.0

    return {
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "risk_pct": risk_pct,
        "reward_pct": reward_pct,
        "rr_ratio": rr_ratio,
        "rr_label": rr_label(rr_ratio),
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


def _detect_gaps(hist) -> list[dict]:
    """Unfilled gap — space harga yang belum pernah "dikunjungi ulang" sejak
    gap-nya kebentuk. Trader anggap ini kayak "luka" yang pasar pengen
    tutup — gap naik yang belum keisi jadi RESISTANCE (order jual yang
    belum ke-eksekusi nunggu di situ), gap turun yang belum keisi jadi
    SUPPORT (dukungan beli yang belum teruji)."""
    highs, lows = hist["High"].to_numpy(), hist["Low"].to_numpy()
    n = len(hist)
    gaps = []
    for i in range(1, n):
        if lows[i] > highs[i - 1]:  # gap naik
            gap_low, gap_high = highs[i - 1], lows[i]
            filled = bool((lows[i + 1:] <= gap_high).any()) if i + 1 < n else False
            if not filled:
                gaps.append({"price": round((gap_low + gap_high) / 2, 2), "type": "resistance", "source": "gap"})
        elif highs[i] < lows[i - 1]:  # gap turun
            gap_low, gap_high = highs[i], lows[i - 1]
            filled = bool((highs[i + 1:] >= gap_low).any()) if i + 1 < n else False
            if not filled:
                gaps.append({"price": round((gap_low + gap_high) / 2, 2), "type": "support", "source": "gap"})
    return gaps


def _fibonacci_levels(hist) -> list[dict]:
    """Retracement standar (23.6/38.2/50/61.8/78.6%) dari swing low-high
    TERBESAR di window yang dikasih — makin panjang histori yang dikasih,
    makin "signifikan" swing-nya (bukan cuma noise jangka pendek)."""
    swing_high = float(hist["High"].max())
    swing_low = float(hist["Low"].min())
    diff = swing_high - swing_low
    if diff <= 0:
        return []
    return [
        {"price": round(swing_high - diff * r, 2), "source": "fibonacci", "ratio": r}
        for r in (0.236, 0.382, 0.5, 0.618, 0.786)
    ]


def _fibonacci_extensions(hist) -> list[dict]:
    """Buat kasus "blue sky" — harga udah tembus SEMUA resistance historis
    (gak ada gap/fib-retracement/swing high di atasnya sama sekali, kejadian
    beneran pas breakout ekstrem). Fib extension nge-proyeksi TARGET DI LUAR
    range swing low-high (127.2%/161.8%/261.8%), bukan level di DALAM range
    kayak retracement — ini teknik standar buat nentuin TP pas gak ada
    resistance historis buat dijadiin acuan."""
    swing_high = float(hist["High"].max())
    swing_low = float(hist["Low"].min())
    diff = swing_high - swing_low
    if diff <= 0:
        return []
    return [
        {"price": round(swing_high + diff * (r - 1), 2), "source": "fibonacci_extension", "ratio": r, "type": "resistance"}
        for r in (1.272, 1.618, 2.618)
    ]


def _level_candidates(hist) -> list[dict]:
    """Gabungan unfilled gap + Fibonacci retracement + swing pivot dari 1
    timeframe/hist tertentu — dipake bareng-bareng lintas timeframe di
    find_smart_tp()."""
    gaps = _detect_gaps(hist)
    fibs = _fibonacci_levels(hist)
    zones = detect_pivot_zones(hist, lookback=len(hist))
    zones = [{"price": z["price"], "type": z["type"], "source": "swing", "touches": z["touches"]} for z in zones]
    return gaps + fibs + zones


def find_smart_tp(hist_by_timeframe: dict, price_now: float) -> dict:
    """TP/SL multi-timeframe — jauh lebih robust dari `support_resistance()`
    (yang cuma window 20 hari 1 timeframe doang). `hist_by_timeframe`:
    {"daily": hist, "weekly": hist, "monthly": hist} (caller nentuin period/
    interval tiap timeframe). Level yang muncul di BEBERAPA timeframe
    sekaligus (misal keliatan di daily DAN weekly) itu konfirmasi lebih kuat
    daripada yang cuma nongol di 1 timeframe — analisa multi-timeframe
    beneran, bukan 1 sudut pandang doang. TP1 = resistance dengan konfirmasi
    timeframe TERBANYAK (baru jarak terdekat jadi tie-breaker).

    CATATAN: timeframe menit/jam/detik SENGAJA gak dimasukin — itu butuh
    data intraday yang NEXUS belum punya arsitekturnya (sama kelas masalah
    kayak BPJS/Scalping yang udah di-tunda, lihat CLAUDE.md)."""
    all_candidates = []
    for tf_name, hist in hist_by_timeframe.items():
        if hist is None or hist.empty:
            continue
        for c in _level_candidates(hist):
            if "type" not in c:  # fibonacci retracement belum ditag type-nya
                c["type"] = "resistance" if c["price"] > price_now else "support"
            c["timeframe"] = tf_name
            all_candidates.append(c)

    def _merge(cands: list[dict], cluster_pct: float = 0.015) -> list[dict]:
        cands = sorted(cands, key=lambda c: c["price"])
        merged = []
        for c in cands:
            if merged and abs(c["price"] - merged[-1]["price"]) / merged[-1]["price"] <= cluster_pct:
                merged[-1]["timeframes"].add(c["timeframe"])
                merged[-1]["sources"].add(c["source"])
                merged[-1]["price"] = round((merged[-1]["price"] + c["price"]) / 2, 2)
            else:
                merged.append({"price": c["price"], "timeframes": {c["timeframe"]}, "sources": {c["source"]}})
        return merged

    resistances = _merge([c for c in all_candidates if c["type"] == "resistance" and c["price"] > price_now])
    supports = _merge([c for c in all_candidates if c["type"] == "support" and c["price"] < price_now])

    if not resistances:
        # blue sky di SEMUA timeframe — breakout tembus semua level lama,
        # fallback fib extension dari timeframe daily (paling detail)
        daily = hist_by_timeframe.get("daily")
        if daily is not None and not daily.empty:
            resistances = [
                {"price": c["price"], "timeframes": {"daily"}, "sources": {c["source"]}}
                for c in _fibonacci_extensions(daily)
            ]

    resistances.sort(key=lambda c: (-len(c["timeframes"]), c["price"]))
    supports.sort(key=lambda c: (-len(c["timeframes"]), -c["price"]))

    def _fmt(c):
        return None if c is None else {"price": c["price"], "sources": sorted(c["sources"]), "timeframes": sorted(c["timeframes"])}

    return {
        "tp1": _fmt(resistances[0]) if resistances else None,
        "tp2": _fmt(resistances[1]) if len(resistances) > 1 else None,
        "sl_anchor": _fmt(supports[0]) if supports else None,
    }


def detect_trend_channel(hist, lookback: int = 60, swing_window: int = 3) -> dict | None:
    """Trend channel — 2 garis diagonal (kayak yang biasa digambar manual di
    TradingView) — connect 2 swing low PALING BARU jadi batas bawah, 2 swing
    high PALING BARU jadi batas atas, diproyeksiin lurus sampe bar terakhir.
    None kalau swing point kurang dari 2 di salah satu sisi — JANGAN maksa
    gambar channel dari titik yang gak cukup buat nentuin garis."""
    recent = hist.tail(lookback)
    highs, lows = recent["High"].to_numpy(), recent["Low"].to_numpy()
    dates = recent.index
    n = len(recent)

    swing_high_pts, swing_low_pts = [], []
    for i in range(swing_window, n - swing_window):
        window = slice(i - swing_window, i + swing_window + 1)
        if highs[i] == highs[window].max():
            swing_high_pts.append((dates[i], float(highs[i])))
        if lows[i] == lows[window].min():
            swing_low_pts.append((dates[i], float(lows[i])))

    if len(swing_low_pts) < 2 or len(swing_high_pts) < 2:
        return None

    last_date = dates[-1]

    def _project(p1, p2, to_date):
        (d1, v1), (d2, v2) = p1, p2
        if d2 == d1:
            return v2
        slope = (v2 - v1) / (d2.value - d1.value)
        return v1 + slope * (to_date.value - d1.value)

    lower2, upper2 = swing_low_pts[-2:], swing_high_pts[-2:]
    return {
        "lower": [lower2[0], (last_date, _project(*lower2, last_date))],
        "upper": [upper2[0], (last_date, _project(*upper2, last_date))],
    }
