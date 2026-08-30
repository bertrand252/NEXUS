"""Limiter shared, dipake main.py (setup global) + router yang butuh limit
lebih ketat dari default. Modul terpisah (bukan didefinisiin di main.py
langsung) biar router bisa import tanpa circular import (main.py -> routers,
kalau limiter di main.py, routers -> main.py jadi lingkaran).

Key per-IP (bukan per-user JWT) — single-user tool, siapapun yang bisa nyampe
endpoint (JWT atau service key bocor) yang mau dicegah, bukan bedain user A
vs user B (emang cuma 1 user). Storage in-memory default (bukan Redis) —
1 instance Railway doang, gak butuh shared state antar proses."""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])
