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
    # resistance/support dari 20 hari SEBELUM hari ini (exclude hari ini) —
    # kalau ikut ngitung hari ini, pas lagi breakout (bikin high baru) resistance
    # jadi SAMA kayak harga sekarang sendiri (tautologi, TP=0%). Bug ini
    # kejadian beneran: KETR & LIFE punya entry_price==target==close_price
    # PERSIS gara-gara ini, muncul sebagai "tp_hit 0%" yang gak berarti apa-apa.
    # Pola fix sama kayak scoring.py::resistance_prior.
    prior = hist.iloc[:-1].tail(20)
    support = float(prior["Low"].min()) if not prior.empty else float(hist["Low"].tail(20).min())
    resistance = float(prior["High"].max()) if not prior.empty else float(hist["High"].tail(20).max())
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


def apply_buy_on_weakness_support(levels: dict, price_now: float, buy_on_weakness: dict | None) -> None:
    """BUG ketemu 2026-09-02 (code review): candidate yang qualify lewat jalur
    "buy on weakness" (well_defended_support — support disentuh berkali-kali
    & mantul) dapet caption yang narasiin level itu, TAPI stop_loss/support
    yang beneran DIKIRIM (dari support_resistance() biasa) tetep pake
    trailing-20-hari low — 2 angka BEDA, gak pernah direkonsiliasi. Bisa
    kejadian SL yang dikirim ada DI ATAS support yang jadi alasan trade-nya
    (posisi stop out sebelum support asli sempet diuji ulang). Fix: kalau
    ada buy_on_weakness, override support/stop_loss/risk_pct/rr_ratio pake
    level itu — resistance/reward_pct/entry TETEP dari support_resistance()
    (target & zona entry gak berubah, cuma basis SL yang perlu nyambung ke
    narasinya). Mutasi in-place, dipanggil SEBELUM gate RR/risk."""
    if not buy_on_weakness:
        return
    support = buy_on_weakness["support_price"]
    stop_loss = round(support * 0.98, 2)
    risk_pct = round((price_now - stop_loss) / price_now * 100, 2)
    levels["support"] = round(support, 2)
    levels["stop_loss"] = stop_loss
    levels["risk_pct"] = risk_pct
    levels["rr_ratio"] = round(levels["reward_pct"] / risk_pct, 2) if risk_pct > 0 else 0.0
    levels["rr_label"] = rr_label(levels["rr_ratio"])


def detect_pivot_zones(hist, lookback: int = 60, swing_window: int = 3,
                        cluster_pct: float = 0.015, top_n: int = 1, min_touches: int = 2) -> list[dict]:
    """Level support/resistance TAMBAHAN ("AI Zones" di chart) — BEDA dari
    `support_resistance()` di atas (yang resmi, dipake buat SL/TP/breakout
    scoring/alert, JANGAN diubah). Ini sinyal pelengkap, dihitung ALGORITMIK
    (bukan Groq nebak angka — LLM gampang ngarang angka presisi):
    1. Cari swing high/low — bar yang high/low-nya lebih ekstrem dari
       `swing_window` bar di kanan-kirinya (titik balik harga asli).
    2. Cluster swing point yang berdekatan (dalam `cluster_pct`) — makin
       sering harga "nyentuh" & mantul di sekitar situ, makin kuat zona-nya.
    3. Buang cluster yang sentuhannya kurang dari `min_touches` (1 sentuhan
       doang gak cukup buat dianggep valid), balikin top-N (default 1) per
       tipe — biar chart gak numpuk garis (user komplain chart-nya berantakan
       pas top_n masih 3, sampe 6 garis "Zona AI" numpuk sama Support/
       Resistance/SMA)."""
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

    resistance_zones = [z for z in _cluster(swing_highs) if z["touches"] >= min_touches]
    support_zones = [z for z in _cluster(swing_lows) if z["touches"] >= min_touches]
    resistance_zones = sorted(resistance_zones, key=lambda z: z["touches"], reverse=True)[:top_n]
    support_zones = sorted(support_zones, key=lambda z: z["touches"], reverse=True)[:top_n]

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


def _swing_clusters_with_roles(hist, swing_window: int = 3, cluster_pct: float = 0.02) -> list[dict]:
    """Kayak detect_pivot_zones, TAPI type-nya BUKAN ditentuin dari asal (peak
    vs trough) — soalnya 1 level harga bisa PERNAH jadi resistance (ditolak
    dari atas) DAN PERNAH jadi support (mantul dari bawah) di waktu beda
    (role reversal — "support jadi resistance" pas ditembus, atau
    sebaliknya). Balikin is_peak/is_trough/touches_as_peak/touches_as_trough
    + peak_dates/trough_dates (ISO string, buat cross-check broker summary/
    tape reading di tanggal itu, lihat scheduler.py::_broker_defended_support)
    per cluster — caller yang nentuin type-nya relatif ke harga SEKARANG.
    Reuse _find_swing_points (sama helper kayak detect_trend_channel/
    detect_chart_pattern) — lookback=len(hist) biar behavior SAMA kayak
    sebelum refactor (dulu selalu pake SELURUH hist yang dikasih, gak ada
    truncation lookback)."""
    swing_high_pts, swing_low_pts = _find_swing_points(hist, len(hist), swing_window)

    def _cluster(points: list[tuple]) -> list[dict]:
        if not points:
            return []
        points = sorted(points, key=lambda p: p[1])
        clusters = [[points[0]]]
        for p in points[1:]:
            if abs(p[1] - clusters[-1][-1][1]) / clusters[-1][-1][1] <= cluster_pct:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        return [
            {"price": round(sum(p[1] for p in c) / len(c), 2), "touches": len(c),
             "dates": [d.date().isoformat() for d, v in c]}
            for c in clusters
        ]

    peak_clusters = _cluster(swing_high_pts)
    trough_clusters = _cluster(swing_low_pts)

    merged, used_troughs = [], set()
    for pc in peak_clusters:
        entry = {
            "price": pc["price"], "touches_as_peak": pc["touches"], "touches_as_trough": 0,
            "peak_dates": pc["dates"], "trough_dates": [],
        }
        for j, tc in enumerate(trough_clusters):
            if j in used_troughs:
                continue
            if abs(pc["price"] - tc["price"]) / pc["price"] <= cluster_pct:
                entry["price"] = round((pc["price"] + tc["price"]) / 2, 2)
                entry["touches_as_trough"] = tc["touches"]
                entry["trough_dates"] = tc["dates"]
                used_troughs.add(j)
                break
        merged.append(entry)
    for j, tc in enumerate(trough_clusters):
        if j not in used_troughs:
            merged.append({
                "price": tc["price"], "touches_as_peak": 0, "touches_as_trough": tc["touches"],
                "peak_dates": [], "trough_dates": tc["dates"],
            })

    for e in merged:
        e["touches"] = e["touches_as_peak"] + e["touches_as_trough"]
        e["role_reversal"] = e["touches_as_peak"] > 0 and e["touches_as_trough"] > 0
    return merged


def well_defended_support(hist, price_now: float, max_distance_pct: float = 6.0, min_touches: int = 3) -> dict | None:
    """"Buy on weakness" — jalur qualify Swing ALTERNATIF dari breakout (user
    eksplisit: dua-duanya valid, bukan gantiin). Beda arah entry: breakout
    beli di harga SEKARANG (udah gerak naik duluan), ini beli DEKET support
    yang berkali-kali disentuh & SELALU mantul (indikasi ada yang "jagain"
    barang di situ) — stop ketat di bawah support, target balik ke
    resistance/range atas, RR sering lebih gede karena entry lebih rendah.

    Reuse _swing_clusters_with_roles — swing-low pivot by DEFINITION udah
    "mantul" (titik balik naik lagi di kedua sisi, syarat swing_window),
    jadi >=min_touches swing-low di cluster yang sama = level itu emang
    berulang kali didukung, gak perlu cek "broken" terpisah. Skip cluster
    yang touches_as_peak > touches_as_trough (lebih sering jadi RESISTANCE
    dari atas belakangan — polaritasnya ambigu, bukan support bersih).
    price_now WAJIB masih deket (max_distance_pct) — kalau udah lari jauh
    dari support, ini bukan lagi entry point yang relevan."""
    candidates_ = [
        c for c in _swing_clusters_with_roles(hist)
        if c["touches_as_trough"] >= min_touches
        and c["touches_as_trough"] >= c["touches_as_peak"]
        and c["price"] < price_now
    ]
    if not candidates_:
        return None
    candidates_.sort(key=lambda c: price_now - c["price"])  # paling deket harga sekarang
    support = candidates_[0]
    distance_pct = (price_now - support["price"]) / support["price"] * 100
    if distance_pct > max_distance_pct:
        return None
    return {
        "support_price": support["price"],
        "touches": support["touches_as_trough"],
        "distance_pct": round(distance_pct, 2),
        # tanggal tiap sentuhan — dipake scheduler.py::_broker_defended_support
        # buat cross-check broker summary/tape reading TEPAT di tanggal itu
        # (bukan cuma pola harga doang, tapi beneran ADA broker yang narik
        # barang pas harga nyentuh situ)
        "touch_dates": support["trough_dates"],
    }


def _level_candidates(hist, price_now: float, min_touches: int = 2) -> list[dict]:
    """Gabungan unfilled gap + Fibonacci retracement + swing pivot (dengan
    role reversal) dari 1 timeframe/hist tertentu — dipake bareng-bareng
    lintas timeframe di find_smart_tp(). Swing pivot WAJIB minimal
    `min_touches` candle confirm (1 sentuhan doang gak cukup buat dianggep
    level valid) — gap & fibonacci gak difilter touches, itu emang validitasnya
    dari konsep lain (bukan dari jumlah sentuhan berulang)."""
    gaps = _detect_gaps(hist)
    fibs = _fibonacci_levels(hist)
    swings = _swing_clusters_with_roles(hist)
    swing_candidates = [
        {"price": s["price"], "source": "swing", "touches": s["touches"], "role_reversal": s["role_reversal"]}
        for s in swings if s["touches"] >= min_touches
    ]
    candidates = gaps + fibs + swing_candidates
    for c in candidates:
        # type SELALU relatif ke harga SEKARANG, BUKAN dari asal peak/trough —
        # biar level yang dulu resistance terus ditembus (jadi support) atau
        # sebaliknya gak ke-drop diem-diem
        c["type"] = "resistance" if c["price"] > price_now else "support"
    return candidates


def determine_trend(hist) -> str:
    """Bullish/bearish/sideways dari posisi harga vs MA50 & MA200 (golden/
    death cross klasik) — dipake buat kasih konteks ke TP/SL, JANGAN
    diinterpretasi sendirian tanpa liat level lain."""
    close = hist["Close"]
    price_now = float(close.iloc[-1])
    if len(close) < 200:
        ma = close.rolling(min(len(close), 50)).mean().iloc[-1]
        if ma != ma:
            return "sideways"
        return "bullish" if price_now > ma else "bearish"
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1])
    if price_now > ma50 > ma200:
        return "bullish"
    if price_now < ma50 < ma200:
        return "bearish"
    return "sideways"


def find_smart_tp(hist_by_timeframe: dict, price_now: float) -> dict:
    """TP/SL multi-timeframe — jauh lebih robust dari `support_resistance()`
    (yang cuma window 20 hari 1 timeframe doang). `hist_by_timeframe`:
    {"daily": hist, "weekly": hist, "monthly": hist} (caller nentuin period/
    interval tiap timeframe — weekly/monthly idealnya "max" biar dari awal
    saham listing, bukan cuma beberapa tahun). Level yang muncul di BEBERAPA
    timeframe sekaligus itu konfirmasi lebih kuat, level yang PERNAH jadi
    resistance DAN support (role reversal) juga ditandain sebagai konfirmasi
    ekstra kuat. TP1 = resistance paling terkonfirmasi (jumlah timeframe,
    baru jarak terdekat jadi tie-breaker).

    CATATAN: timeframe menit/jam/detik SENGAJA gak dimasukin — itu butuh
    data intraday yang NEXUS belum punya arsitekturnya (sama kelas masalah
    kayak BPJS/Scalping yang udah di-tunda, lihat CLAUDE.md)."""
    all_candidates = []
    for tf_name, hist in hist_by_timeframe.items():
        if hist is None or hist.empty:
            continue
        for c in _level_candidates(hist, price_now):
            c["timeframe"] = tf_name
            all_candidates.append(c)

    def _merge(cands: list[dict], cluster_pct: float = 0.015) -> list[dict]:
        cands = sorted(cands, key=lambda c: c["price"])
        merged = []
        for c in cands:
            if merged and abs(c["price"] - merged[-1]["price"]) / merged[-1]["price"] <= cluster_pct:
                merged[-1]["timeframes"].add(c["timeframe"])
                merged[-1]["sources"].add(c["source"])
                merged[-1]["role_reversal"] = merged[-1].get("role_reversal") or c.get("role_reversal", False)
                merged[-1]["price"] = round((merged[-1]["price"] + c["price"]) / 2, 2)
            else:
                merged.append({
                    "price": c["price"], "timeframes": {c["timeframe"]}, "sources": {c["source"]},
                    "role_reversal": c.get("role_reversal", False),
                })
        return merged

    resistances = _merge([c for c in all_candidates if c["type"] == "resistance" and c["price"] > price_now])
    supports = _merge([c for c in all_candidates if c["type"] == "support" and c["price"] < price_now])

    if not resistances:
        # blue sky di SEMUA timeframe — breakout tembus semua level lama,
        # fallback fib extension dari timeframe daily (paling detail)
        daily = hist_by_timeframe.get("daily")
        if daily is not None and not daily.empty:
            resistances = [
                {"price": c["price"], "timeframes": {"daily"}, "sources": {c["source"]}, "role_reversal": False}
                for c in _fibonacci_extensions(daily)
            ]

    # prioritas: role reversal (pernah jadi resistance DAN support) > jumlah
    # timeframe konfirmasi > jarak terdekat
    resistances.sort(key=lambda c: (not c.get("role_reversal"), -len(c["timeframes"]), c["price"]))
    supports.sort(key=lambda c: (not c.get("role_reversal"), -len(c["timeframes"]), -c["price"]))

    def _fmt(c):
        if c is None:
            return None
        return {
            "price": c["price"], "sources": sorted(c["sources"]), "timeframes": sorted(c["timeframes"]),
            "role_reversal": c.get("role_reversal", False),
        }

    return {
        "tp1": _fmt(resistances[0]) if resistances else None,
        "tp2": _fmt(resistances[1]) if len(resistances) > 1 else None,
        "sl_anchor": _fmt(supports[0]) if supports else None,
    }


def _find_swing_points(hist, lookback: int, swing_window: int) -> tuple[list, list]:
    """Titik balik harga asli (swing high/low, bar yang high/low-nya lebih
    ekstrem dari swing_window bar di kanan-kirinya) — dipake BARENG oleh
    detect_trend_channel & detect_chart_pattern, biar logic pivot-finding
    gak duplikat di 2 tempat. Balikin (swing_high_pts, swing_low_pts),
    tiap poin (tanggal, harga), urut kronologis."""
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
    return swing_high_pts, swing_low_pts


def detect_trend_channel(hist, lookback: int = 60, swing_window: int = 3) -> dict | None:
    """Trend channel — 2 garis diagonal (kayak yang biasa digambar manual di
    TradingView) — connect 2 swing low PALING BARU jadi batas bawah, 2 swing
    high PALING BARU jadi batas atas, diproyeksiin lurus sampe bar terakhir.
    None kalau swing point kurang dari 2 di salah satu sisi — JANGAN maksa
    gambar channel dari titik yang gak cukup buat nentuin garis."""
    swing_high_pts, swing_low_pts = _find_swing_points(hist, lookback, swing_window)
    if len(swing_low_pts) < 2 or len(swing_high_pts) < 2:
        return None

    last_date = hist.tail(lookback).index[-1]

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


CHART_PATTERN_FLAT_THRESHOLD_PCT_PER_DAY = 0.05  # ponytail heuristic, belum divalidasi backtest —
                                                   # slope di bawah ini dianggap "flat" (support/resistance sama)


def detect_chart_pattern(hist, lookback: int = 60, swing_window: int = 3) -> dict | None:
    """Klasifikasi TRIANGLE (ascending/descending/symmetrical) — pattern klasik
    TA, dari slope garis upper/lower yang SAMA persis kayak detect_trend_channel
    (2 swing high terbaru = upper, 2 swing low terbaru = lower), bedanya di
    sini garisnya DIKLASIFIKASIIN (naik/turun/flat), bukan cuma digambar.

    Definisi klasik:
    - ascending_triangle: lower NAIK (higher lows, ada yang nahan beli makin
      tinggi) + upper FLAT (resistance sama berkali-kali) -> continuation
      BULLISH, breakout cenderung ke atas.
    - descending_triangle: upper TURUN (lower highs, tekanan jual makin
      rendah) + lower FLAT (support sama berkali-kali) -> continuation
      BEARISH, breakout cenderung ke bawah.
    - symmetrical_triangle: upper TURUN + lower NAIK (dua-duanya konvergen
      dari 2 sisi) -> arah breakout gak pasti dari bentuknya doang, TAPI
      range harga makin SEMPIT (mirip scoring.py::bollinger_signal squeeze
      dari sudut beda — kalau dua-duanya nyala bareng, SALING KONFIRMASI).

    None kalau swing point kurang, ATAU kombinasi slope-nya gak masuk salah
    satu dari 3 pola di atas (misal dua-duanya naik/turun bareng — itu
    channel biasa/paralel, bukan triangle, tetep kepake detect_trend_channel
    buat itu). Threshold "flat" HEURISTIK (belum divalidasi backtest) — jangan
    anggap ini deteksi presisi 100%, konteks tambahan doang buat Groq."""
    swing_high_pts, swing_low_pts = _find_swing_points(hist, lookback, swing_window)
    if len(swing_low_pts) < 2 or len(swing_high_pts) < 2:
        return None

    def _slope_pct_per_day(p1, p2) -> float:
        (d1, v1), (d2, v2) = p1, p2
        days = (d2 - d1).days
        if days == 0 or not v1:
            return 0.0
        return (v2 - v1) / v1 / days * 100

    def _direction(slope: float) -> str:
        if abs(slope) <= CHART_PATTERN_FLAT_THRESHOLD_PCT_PER_DAY:
            return "flat"
        return "rising" if slope > 0 else "falling"

    upper_slope = _slope_pct_per_day(*swing_high_pts[-2:])
    lower_slope = _slope_pct_per_day(*swing_low_pts[-2:])
    upper_dir, lower_dir = _direction(upper_slope), _direction(lower_slope)

    if upper_dir == "flat" and lower_dir == "rising":
        pattern = "ascending_triangle"
    elif lower_dir == "flat" and upper_dir == "falling":
        pattern = "descending_triangle"
    elif upper_dir == "falling" and lower_dir == "rising":
        pattern = "symmetrical_triangle"
    else:
        return None

    return {
        "pattern": pattern,
        "upper_slope_pct_per_day": round(upper_slope, 3),
        "lower_slope_pct_per_day": round(lower_slope, 3),
    }
