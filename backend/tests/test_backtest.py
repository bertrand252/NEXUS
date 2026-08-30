"""Parity check technical_score vectorized (backtest.py) vs scalar (scoring.py)
— WAJIB sama, kalau beda backtest ngukur gate yang salah. Reuse _self_check()
yang udah ada (dipanggil juga manual tiap `python backtest.py` dijalanin),
biar 1 sumber kebenaran doang, bukan 2 assert terpisah yang bisa nyimpang."""
from backtest import _self_check


def test_technical_score_vectorized_matches_scalar():
    _self_check()  # assert internal, raise kalau mismatch
