"""Test unit buat levels.py — support/resistance & rr_label, fungsi murni
(gak nyentuh yfinance/Supabase)."""
import pandas as pd
from levels import rr_label, support_resistance, nearest_support_resistance, well_defended_support, detect_chart_pattern, apply_buy_on_weakness_support, idx_tick_size, price_plus_ticks, classify_tp_sl_touch, resolve_ambiguous_touch


def test_rr_label_bands():
    assert rr_label(0.5) == "Buruk"
    assert rr_label(1.5) == "Cukup"
    assert rr_label(2.5) == "Bagus"
    assert rr_label(4.0) == "Sangat Bagus"


def test_idx_tick_size_bands():
    assert idx_tick_size(93) == 1
    assert idx_tick_size(199) == 1
    assert idx_tick_size(200) == 2
    assert idx_tick_size(499) == 2
    assert idx_tick_size(500) == 5
    assert idx_tick_size(1999) == 5
    assert idx_tick_size(2000) == 10
    assert idx_tick_size(4999) == 10
    assert idx_tick_size(5000) == 25


def test_price_plus_ticks_uses_band_at_base_price():
    assert price_plus_ticks(93, 3) == 96  # tick Rp1 di bawah Rp200
    assert price_plus_ticks(1780, 3) == 1795  # tick Rp5 di rentang Rp500-2000


def test_classify_tp_sl_touch_gap_up_then_arb_is_ambiguous_at_daily_level():
    """Kasus nyata AGAR (2026-09-24): gap-up pagi jauh ngelewatin target,
    abis itu ARB sampe di bawah stop_loss juga — dari daily bar doang
    (High/Low/Close), DUA-DUANYA kesentuh hari yang sama, gak bisa
    dipastikan mana duluan tanpa data intraday (lihat resolve_ambiguous_touch
    test di bawah, itu yang beneran mutusin 'tp' buat kasus ini)."""
    kind, exit_price = classify_tp_sl_touch(daily_high=3153, daily_low=2380, daily_close=2460, target=2874, stop_loss=2734)
    assert kind == "ambiguous"
    assert exit_price is None


def test_resolve_ambiguous_touch_agar_gap_up_then_arb_resolves_to_tp():
    """Bar 15-menit AGAR-style: gap-up nyentuh target di bar PALING AWAL
    (open pagi udah di atas target), baru abis itu ARB ngebanting ke bawah
    stop_loss di bar-bar belakangan — order TP REAL ke-fill duluan pas
    market buka, jangan dianggep loss cuma gara-gara harga akhir anjlok."""
    bars = [
        {"High": 3153, "Low": 2900},  # bar pertama: langsung gap-up ngelewatin target
        {"High": 2900, "Low": 2734},
        {"High": 2734, "Low": 2380},  # ARB, tembus stop_loss belakangan
    ]
    assert resolve_ambiguous_touch(bars, target=2874, stop_loss=2734) == "tp"


def test_classify_tp_sl_touch_overshoot_still_above_target_uses_actual_close():
    """Kasus ULTJ/GDST — Close MASIH di atas target pas dicek (gak sempet
    jatuh lagi), exit_price pake harga asli (overshoot), bukan diklem ke target."""
    kind, exit_price = classify_tp_sl_touch(daily_high=139, daily_low=134, daily_close=139, target=138, stop_loss=131)
    assert kind == "tp"
    assert exit_price == 139


def test_classify_tp_sl_touch_sl_symmetric():
    kind, exit_price = classify_tp_sl_touch(daily_high=105, daily_low=90, daily_close=95, target=120, stop_loss=98)
    assert kind == "sl"
    assert exit_price == 95  # Close masih di bawah stop_loss, pake harga asli


def test_classify_tp_sl_touch_sl_bounced_back_above_stop_loss():
    kind, exit_price = classify_tp_sl_touch(daily_high=105, daily_low=90, daily_close=102, target=120, stop_loss=98)
    assert kind == "sl"
    assert exit_price == 98  # bounce balik di atas SL, tetep dianggep ke-fill di stop_loss


def test_classify_tp_sl_touch_neither():
    kind, exit_price = classify_tp_sl_touch(daily_high=105, daily_low=98, daily_close=100, target=120, stop_loss=90)
    assert kind is None
    assert exit_price == 100


def test_classify_tp_sl_touch_ambiguous_when_both_hit_same_day():
    kind, exit_price = classify_tp_sl_touch(daily_high=130, daily_low=80, daily_close=100, target=120, stop_loss=90)
    assert kind == "ambiguous"
    assert exit_price is None


def test_resolve_ambiguous_touch_picks_whichever_bar_hit_first():
    bars = [{"High": 105, "Low": 99}, {"High": 122, "Low": 100}, {"High": 110, "Low": 85}]
    assert resolve_ambiguous_touch(bars, target=120, stop_loss=90) == "tp"


def test_resolve_ambiguous_touch_sl_hit_first():
    bars = [{"High": 105, "Low": 88}, {"High": 122, "Low": 100}]
    assert resolve_ambiguous_touch(bars, target=120, stop_loss=90) == "sl"


def test_resolve_ambiguous_touch_conservative_fallback_when_empty():
    assert resolve_ambiguous_touch([], target=120, stop_loss=90) == "sl"


def _make_hist(closes: list[float]) -> pd.DataFrame:
    """DataFrame OHLC sintetis — High/Low dikasih spread kecil dari Close
    biar realistis, index tanggal harian berurutan."""
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({
        "Open": closes, "Close": closes,
        "High": [c * 1.01 for c in closes],
        "Low": [c * 0.99 for c in closes],
        "Volume": [1_000_000] * len(closes),
    }, index=idx)


def test_support_resistance_excludes_today_no_tautology():
    """Bug yang pernah kejadian beneran (KETR/LIFE, lihat CLAUDE.md insiden):
    resistance HARUS dari 20 hari SEBELUM hari ini, bukan ikutan hari ini —
    kalau hari ini bikin high baru, resistance gak boleh sama persis price_now."""
    closes = [100.0] * 20 + [150.0]  # hari terakhir breakout gede ke 150
    hist = _make_hist(closes)
    levels = support_resistance(hist)
    price_now = 150.0
    assert levels["resistance"] != price_now
    assert levels["resistance"] < price_now  # resistance dari histori (~101), bukan hari ini


def test_support_resistance_rr_ratio_computed():
    closes = [100.0 + i * 0.5 for i in range(21)]  # naik pelan-pelan, gak breakout ekstrem
    hist = _make_hist(closes)
    levels = support_resistance(hist)
    assert levels["rr_label"] == rr_label(levels["rr_ratio"])
    assert levels["stop_loss"] < levels["entry_low"]  # SL harus di bawah zona entry


_SUPPORT_BOUNCE_CLOSES = [
    110, 107, 103, 100, 103, 107, 111, 108, 104, 100,
    103, 108, 112, 109, 105, 100, 104, 109, 113, 110, 106, 102,
]  # support ~100 disentuh 3x jelas (tiap bounce+turun cukup bar buat swing_window=3 kedeteksi)


def test_nearest_support_resistance_ignores_stale_deep_dip():
    """Insiden #16 (BPJS, 2026-09-04): support_resistance() 20-hari ambil
    MIN/MAX mentah trailing-20 — 1 dip lama yang gak lagi relevan (bukan
    swing point asli, cuma noise sesaat) ketarik jadi 'support', bikin
    risk_pct/stop_loss jauh gak masuk akal buat trade yang mesti resolve
    besok. nearest_support_resistance() reuse swing point asli (butuh jadi
    titik balik beneran), jadi dip 1 hari yang gak dikonfirmasi swing_window
    di dua sisi gak ketarik, hasil support-nya lebih deket & risk_pct lebih
    kecil."""
    closes = [100, 100, 60, 100, 100, 103, 107, 111, 108, 104,
              100, 103, 108, 112, 109, 105, 100, 104, 109, 113, 140]
    hist = _make_hist(closes)

    old = support_resistance(hist)
    new = nearest_support_resistance(hist)

    assert old["support"] < 65  # ketarik ke dip lama (~59.4)
    assert new["support"] > 90  # ambil swing low asli terdekat (~99), bukan dip noise
    assert new["risk_pct"] < old["risk_pct"]


def test_well_defended_support_detects_repeated_bounce():
    hist = _make_hist(_SUPPORT_BOUNCE_CLOSES)
    result = well_defended_support(hist, price_now=103.0)
    assert result is not None
    assert result["touches"] >= 3
    assert result["support_price"] < 103.0


def test_well_defended_support_none_when_price_too_far():
    hist = _make_hist(_SUPPORT_BOUNCE_CLOSES)
    assert well_defended_support(hist, price_now=140.0) is None  # udah lari jauh, bukan lagi buy-on-weakness


def test_well_defended_support_none_without_enough_touches():
    closes = [100.0 + i for i in range(20)]  # naik lurus, gak ada support berulang
    hist = _make_hist(closes)
    assert well_defended_support(hist, price_now=119.0) is None


def test_apply_buy_on_weakness_support_overrides_sl_to_match_narrative():
    """BUG ketemu (code review): caption narasiin "support disentuh 3x di
    ~Rp100" (dari well_defended_support), TAPI stop_loss yang dikirim tetep
    dari support_resistance() trailing-20-hari — 2 level BEDA, gak nyambung.
    Setelah apply_buy_on_weakness_support, stop_loss/support HARUS berbasis
    support_price yang sama kayak yang dinarasiin, bukan level lama.

    Closes SENGAJA beda dari _SUPPORT_BOUNCE_CLOSES — ada 1 dip tunggal ke
    88 (bukan bagian cluster ~100 yang disentuh 3x) SUPAYA support_resistance()
    (ambil MIN mentah trailing-20-hari) kepancing turun ke 88, sementara
    well_defended_support (butuh >=3 sentuhan di harga yang SAMA) tetep milih
    ~100 — 2 level yang beneran BEDA, baru ketauan kalau reconciliation-nya
    gak jalan."""
    closes = [110, 107, 103, 100, 103, 107, 111, 108, 104, 100,
              103, 108, 112, 109, 105, 100, 104, 109, 113, 110, 88, 102]
    hist = _make_hist(closes)
    price_now = 103.0
    levels = support_resistance(hist)
    bow = well_defended_support(hist, price_now)
    assert bow is not None
    assert abs(bow["support_price"] - 100) < 2  # cluster ~100, BUKAN dip tunggal 88
    old_stop_loss = levels["stop_loss"]
    assert old_stop_loss < 90  # buktiin support_resistance() emang kepancing dip 88 sebelum di-fix

    apply_buy_on_weakness_support(levels, price_now, bow)

    assert levels["support"] == round(bow["support_price"], 2)
    assert levels["stop_loss"] == round(bow["support_price"] * 0.98, 2)
    assert levels["stop_loss"] != old_stop_loss  # beneran ke-override, bukan kebetulan sama
    assert levels["rr_label"] == rr_label(levels["rr_ratio"])


def test_apply_buy_on_weakness_support_noop_when_none():
    hist = _make_hist(_SUPPORT_BOUNCE_CLOSES)
    levels = support_resistance(hist)
    before = dict(levels)
    apply_buy_on_weakness_support(levels, 103.0, None)
    assert levels == before


def _zigzag(pivots: list[float]) -> list[float]:
    """Bangun deret harga yang genuinely ngelewatin tiap titik di `pivots`
    sebagai swing point ASLI (ramp 3 langkah antar titik, cukup jarak biar
    swing_window=3 gak ke-exclude titik ujung)."""
    seq: list[float] = []
    for v in pivots:
        if seq:
            prev = seq[-1]
            for i in range(1, 4):
                seq.append(prev + (v - prev) * i / 4)
        seq.append(v)
    return seq


def test_detect_chart_pattern_ascending_triangle():
    # resistance flat ~125, support naik 100->108->116 (higher lows)
    hist = _make_hist(_zigzag([125, 100, 125, 108, 125, 116, 125]))
    result = detect_chart_pattern(hist)
    assert result is not None
    assert result["pattern"] == "ascending_triangle"


def test_detect_chart_pattern_descending_triangle():
    # support flat ~100, resistance turun 130->118->108 (lower highs)
    hist = _make_hist(_zigzag([100, 130, 100, 118, 100, 108, 100]))
    result = detect_chart_pattern(hist)
    assert result is not None
    assert result["pattern"] == "descending_triangle"


def test_detect_chart_pattern_symmetrical_triangle():
    # highs turun 130->122->114, lows naik 90->98->106 (konvergen 2 sisi)
    hist = _make_hist(_zigzag([130, 90, 122, 98, 114, 106, 110]))
    result = detect_chart_pattern(hist)
    assert result is not None
    assert result["pattern"] == "symmetrical_triangle"


def test_detect_chart_pattern_none_for_parallel_channel():
    # upper & lower dua-duanya NAIK bareng — channel paralel biasa, bukan triangle
    hist = _make_hist(_zigzag([100, 115, 108, 123, 116, 131, 124]))
    assert detect_chart_pattern(hist) is None
