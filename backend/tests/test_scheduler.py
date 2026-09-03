"""Test unit buat scheduler.py — cuma fungsi murni (gak nyentuh Supabase/yfinance/Telegram)."""
from datetime import datetime
from scheduler import _format_bandar_line, _detect_bandar, _is_due_now, _broker_defended_support, _whale_threshold, WHALE_MIN_VALUE
from unittest.mock import patch


def test_is_due_now_catches_up_after_target_passed_today():
    # restart 07:30, target harian 06:00 -> udah lewat HARI INI, harus catch-up (True)
    now = datetime(2026, 9, 2, 7, 30)  # Rabu
    assert _is_due_now(now, hour=6, minute=0, weekday=None) is True


def test_is_due_now_false_before_target_today():
    now = datetime(2026, 9, 2, 5, 0, 0)
    assert _is_due_now(now, hour=6, minute=0, weekday=None) is False


def test_is_due_now_weekly_only_true_on_target_weekday():
    # Minggu (weekday=6) jam 21:05, target 21:00 -> lewat DI HARI YANG BENER -> True
    sunday_after = datetime(2026, 9, 6, 21, 5)  # 2026-09-06 = Minggu
    assert _is_due_now(sunday_after, hour=21, minute=0, weekday=6) is True
    # Senin (bukan hari targetnya) -> False walau jamnya sama, JANGAN catch-up
    # weekly job yang beberapa hari udah lewat (kadaluarsa, bukan telat sedikit)
    monday = datetime(2026, 9, 7, 21, 5)
    assert _is_due_now(monday, hour=21, minute=0, weekday=6) is False


def test_format_bandar_line_with_avg_price():
    line = _format_bandar_line({
        "broker": "AG", "trend": "akumulasi_meningkat", "avg_price_estimate": 1250.0,
    })
    assert "AG" in line
    assert "MENINGKAT" in line
    assert "1,250" in line


def test_format_bandar_line_without_avg_price():
    line = _format_bandar_line({"broker": "YP", "trend": "netral", "avg_price_estimate": None})
    assert "YP" in line
    assert "avg beli" not in line  # gak nampilin estimasi kalau gak ada sample


def test_whale_threshold_thin_stock_drops_below_flat_floor():
    # JECX real 2026-09-03: avg value 20d ~Rp10,2M, flat 500jt gak pernah kena
    # (trade terbesar hari itu cuma 448jt, 4% avg ~409jt) -> ambang relatif
    # harus di bawah flat DAN di bawah trade itu biar sekarang kedetek
    threshold = _whale_threshold(WHALE_MIN_VALUE, 10_223_186_200)
    assert threshold < WHALE_MIN_VALUE
    assert 448_174_000 >= threshold  # trade JECX yang lolos sekarang


def test_whale_threshold_very_thin_stock_far_below_flat_floor():
    # HADE real 2026-09-03: avg value 20d ~Rp84jt, trade terbesar cuma Rp9jt
    # (~10% avg) -> ambang relatif harus jauh di bawah flat biar kedetek
    threshold = _whale_threshold(WHALE_MIN_VALUE, 84_428_600)
    assert threshold < 10_000_000
    assert 9_000_000 >= threshold


def test_whale_threshold_no_avg_value_falls_back_to_flat_floor():
    assert _whale_threshold(WHALE_MIN_VALUE, None) == WHALE_MIN_VALUE


def test_whale_threshold_liquid_stock_stays_at_flat_floor():
    # avg value gede (saham likuid beneran) -> 5% nya jauh di atas flat, harus
    # tetep pake flat_floor (gak dibikin lebih ketat dari sebelumnya)
    threshold = _whale_threshold(WHALE_MIN_VALUE, 50_000_000_000)
    assert threshold == WHALE_MIN_VALUE


def test_format_bandar_line_steady_accumulation_sideways():
    line = _format_bandar_line({
        "broker": "AG", "trend": "netral", "avg_price_estimate": None,
        "steady_accumulation_sideways": True, "consistency_pct": 80.0,
    })
    assert "STEADY" in line
    assert "80.0%" in line


def test_detect_bandar_flags_steady_accumulation_when_sideways_and_consistent():
    # value per broker itu KUMULATIF sejak from_date (dikonfirmasi lawan API
    # asli 2026-09-01) — 1000,2000,3000,4000,5000 = delta +1000 TIAP hari,
    # 5/5 hari positif setelah di-diff().
    inv = {
        "price": [{"close": c} for c in [100, 101, 99, 100, 102, 100]],  # sideways, range 3%
        "broker": [{
            "broker": "AG",
            "data": [{"date": f"2026-08-{d:02d}", "value": v} for d, v in zip(range(1, 6), [1000, 2000, 3000, 4000, 5000])],
        }],
    }
    with patch("scheduler.invezgo_client.is_configured", return_value=True), \
         patch("scheduler.invezgo_client.get_inventory_chart_stock", return_value=inv), \
         patch("scheduler.invezgo_client.get_running_trade", return_value={"data": []}):
        result = _detect_bandar("TEST", "2026-08-01", "2026-08-06")
    assert result["steady_accumulation_sideways"] is True
    assert result["consistency_pct"] == 100.0
    assert result["cumulative_net_value"] == 5000  # value TERAKHIR, bukan di-sum


def test_detect_bandar_no_flag_when_not_sideways():
    inv = {
        "price": [{"close": c} for c in [100, 110, 120, 130, 140, 150]],  # trending, gak sideways
        "broker": [{
            "broker": "AG",
            "data": [{"date": f"2026-08-{d:02d}", "value": v} for d, v in zip(range(1, 6), [1000, 2000, 3000, 4000, 5000])],
        }],
    }
    with patch("scheduler.invezgo_client.is_configured", return_value=True), \
         patch("scheduler.invezgo_client.get_inventory_chart_stock", return_value=inv), \
         patch("scheduler.invezgo_client.get_running_trade", return_value={"data": []}):
        result = _detect_bandar("TEST", "2026-08-01", "2026-08-06")
    assert result["steady_accumulation_sideways"] is False


def test_detect_bandar_not_confused_by_cumulative_growth():
    # broker net-buy CUMA di hari-1 (delta +9000), abis itu diem (cumulative
    # gak berubah) — sebelum fix, kode lama nge-SUM raw cumulative value
    # (1000+9000+9000+9000+9000=37000, salah besar) alih-alih ambil value
    # TERAKHIR (9000, bener). Konsistensi juga harus rendah (1/5 hari doang
    # yang ada delta positif), BUKAN 100% kayak kalau raw value > 0 dicek.
    inv = {
        "price": [{"close": 100} for _ in range(6)],
        "broker": [{
            "broker": "AG",
            "data": [{"date": f"2026-08-{d:02d}", "value": v} for d, v in zip(range(1, 6), [9000, 9000, 9000, 9000, 9000])],
        }],
    }
    with patch("scheduler.invezgo_client.is_configured", return_value=True), \
         patch("scheduler.invezgo_client.get_inventory_chart_stock", return_value=inv), \
         patch("scheduler.invezgo_client.get_running_trade", return_value={"data": []}):
        result = _detect_bandar("TEST", "2026-08-01", "2026-08-06")
    assert result["cumulative_net_value"] == 9000
    assert result["consistency_pct"] == 20.0


def test_detect_bandar_trend_not_akumulasi_melambat_when_prior_is_selling():
    # BUG ketemu 2026-09-02: cabang "akumulasi_melambat" tadinya cuma cek
    # recent_sum < prior_sum * 0.5 tanpa mastiin prior_sum POSITIF dulu.
    # Skenario ini: broker net-JUAL di window sebelumnya (prior_sum -5000)
    # terus makin parah jualnya (recent_sum -8000) — bukan akumulasi yang
    # "melambat" (emang gak pernah ada akumulasi), harusnya netral bukan
    # dilabel akumulasi_melambat.
    dates = [f"2026-08-{d:02d}" for d in range(1, 14)]
    values = [10000, 20000, 30000,                    # warm-up, di luar window trend
              29000, 28000, 27000, 26000, 25000,        # prior window: -1000/hari, prior_sum=-5000
              24000, 23000, 22000, 21000, 17000]         # recent window: -1000x4,-4000, recent_sum=-8000
    inv = {
        "price": [{"close": 100} for _ in dates],
        "broker": [{"broker": "AG", "data": [{"date": d, "value": v} for d, v in zip(dates, values)]}],
    }
    with patch("scheduler.invezgo_client.is_configured", return_value=True), \
         patch("scheduler.invezgo_client.get_inventory_chart_stock", return_value=inv), \
         patch("scheduler.invezgo_client.get_running_trade", return_value={"data": []}):
        result = _detect_bandar("TEST", "2026-08-01", "2026-08-13")
    assert result["trend"] == "netral"


def test_broker_defended_support_detects_consistent_broker():
    # broker "AG" dominan net-buy di SEMUA tanggal touch -> konfirmasi kuat
    def fake_running_trade(ticker, date, limit=200):
        return {"data": [
            {"buyer": "AG", "seller": "XY", "volume": 1000},
            {"buyer": "AG", "seller": "ZQ", "volume": 500},
        ]}
    with patch("scheduler.invezgo_client.is_configured", return_value=True), \
         patch("scheduler.invezgo_client.get_running_trade", side_effect=fake_running_trade):
        result = _broker_defended_support("TEST", ["2026-08-01", "2026-08-05", "2026-08-10"])
    assert result is not None
    assert result["broker"] == "AG"
    assert result["appearances"] == 3


def test_broker_defended_support_none_when_no_consistent_broker():
    # beda broker dominan tiap tanggal -> gak ada yang "konsisten defend"
    dominant_by_date = {"2026-08-01": "AG", "2026-08-05": "XY", "2026-08-10": "ZQ"}
    def fake_running_trade(ticker, date, limit=200):
        return {"data": [{"buyer": dominant_by_date[date], "seller": "OTHER", "volume": 1000}]}
    with patch("scheduler.invezgo_client.is_configured", return_value=True), \
         patch("scheduler.invezgo_client.get_running_trade", side_effect=fake_running_trade):
        result = _broker_defended_support("TEST", list(dominant_by_date.keys()))
    assert result is None


def test_broker_defended_support_none_when_invezgo_not_configured():
    with patch("scheduler.invezgo_client.is_configured", return_value=False):
        assert _broker_defended_support("TEST", ["2026-08-01"]) is None


def test_broker_defended_support_none_without_touch_dates():
    with patch("scheduler.invezgo_client.is_configured", return_value=True):
        assert _broker_defended_support("TEST", []) is None
