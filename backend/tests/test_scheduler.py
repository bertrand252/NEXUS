"""Test unit buat scheduler.py — cuma fungsi murni (gak nyentuh Supabase/yfinance/Telegram)."""
from scheduler import _format_bandar_line


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
