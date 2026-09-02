"""Test unit buat levels.py — support/resistance & rr_label, fungsi murni
(gak nyentuh yfinance/Supabase)."""
import pandas as pd
from levels import rr_label, support_resistance, well_defended_support, detect_chart_pattern, apply_buy_on_weakness_support


def test_rr_label_bands():
    assert rr_label(0.5) == "Buruk"
    assert rr_label(1.5) == "Cukup"
    assert rr_label(2.5) == "Bagus"
    assert rr_label(4.0) == "Sangat Bagus"


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
