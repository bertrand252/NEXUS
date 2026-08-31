"""Test unit buat scheduler.py — cuma fungsi murni (gak nyentuh Supabase/yfinance/Telegram)."""
from scheduler import _format_bandar_line, _detect_bandar
from unittest.mock import patch


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


def test_format_bandar_line_steady_accumulation_sideways():
    line = _format_bandar_line({
        "broker": "AG", "trend": "netral", "avg_price_estimate": None,
        "steady_accumulation_sideways": True, "consistency_pct": 80.0,
    })
    assert "STEADY" in line
    assert "80.0%" in line


def test_detect_bandar_flags_steady_accumulation_when_sideways_and_consistent():
    inv = {
        "price": [{"close": c} for c in [100, 101, 99, 100, 102, 100]],  # sideways, range 3%
        "broker": [{
            "broker": "AG",
            "data": [{"date": f"2026-08-{d:02d}", "value": 1000} for d in range(1, 6)],  # 5/5 hari positif
        }],
    }
    with patch("scheduler.invezgo_client.is_configured", return_value=True), \
         patch("scheduler.invezgo_client.get_inventory_chart_stock", return_value=inv), \
         patch("scheduler.invezgo_client.get_running_trade", return_value={"data": []}):
        result = _detect_bandar("TEST", "2026-08-01", "2026-08-06")
    assert result["steady_accumulation_sideways"] is True
    assert result["consistency_pct"] == 100.0


def test_detect_bandar_no_flag_when_not_sideways():
    inv = {
        "price": [{"close": c} for c in [100, 110, 120, 130, 140, 150]],  # trending, gak sideways
        "broker": [{
            "broker": "AG",
            "data": [{"date": f"2026-08-{d:02d}", "value": 1000} for d in range(1, 6)],
        }],
    }
    with patch("scheduler.invezgo_client.is_configured", return_value=True), \
         patch("scheduler.invezgo_client.get_inventory_chart_stock", return_value=inv), \
         patch("scheduler.invezgo_client.get_running_trade", return_value={"data": []}):
        result = _detect_bandar("TEST", "2026-08-01", "2026-08-06")
    assert result["steady_accumulation_sideways"] is False
