from fastapi import APIRouter
from config import supabase

router = APIRouter()


@router.get("/stats")
def get_signal_track_stats():
    """Win rate alert Telegram NEXUS — TP hit vs SL hit dari signal_alerts.
    Dipakai buat validasi beneran apa breakout+volume scoring akurat, bukan
    cuma keliatan masuk akal doang."""
    try:
        res = supabase.table("signal_alerts").select("status,outcome_pct").execute()
    except Exception:
        return {"total": 0, "open": 0, "tp_hit": 0, "sl_hit": 0, "timeout": 0, "win_rate_pct": None,
                "warning": "Tabel signal_alerts belum ada / gak bisa diakses — jalanin SQL setup dulu di Supabase."}

    rows = res.data
    total = len(rows)
    waiting_entry = sum(1 for r in rows if r["status"] == "waiting_entry")
    open_count = sum(1 for r in rows if r["status"] == "open")
    missed = sum(1 for r in rows if r["status"] == "missed")
    tp_hit = sum(1 for r in rows if r["status"] == "tp_hit")
    sl_hit = sum(1 for r in rows if r["status"] == "sl_hit")
    timeout_win = sum(1 for r in rows if r["status"] == "timeout" and (r.get("outcome_pct") or 0) > 0)
    timeout_loss = sum(1 for r in rows if r["status"] == "timeout" and (r.get("outcome_pct") or 0) <= 0)
    timeout = timeout_win + timeout_loss

    # win rate cuma dari posisi yang BENERAN kejalanin (tp/sl/timeout) —
    # waiting_entry & missed sengaja gak keitung menang/kalah, itu bukan
    # soal panggilannya bener/salah, cuma belum/gak sempet ke-entry
    wins = tp_hit + timeout_win
    losses = sl_hit + timeout_loss
    closed = wins + losses
    win_rate_pct = round(wins / closed * 100, 1) if closed else None

    return {
        "total": total, "waiting_entry": waiting_entry, "open": open_count, "missed": missed,
        "tp_hit": tp_hit, "sl_hit": sl_hit, "timeout": timeout,
        "win_rate_pct": win_rate_pct, "warning": None,
    }
