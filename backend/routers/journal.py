from datetime import date
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import supabase

router = APIRouter()


class JournalEntry(BaseModel):
    emiten: str
    profit_loss: str  # "profit" | "loss"
    amount: float      # IDR, selalu positif — arahnya ditentukan profit_loss


class SaveDayInput(BaseModel):
    tanggal: date
    entries: list[JournalEntry]


@router.post("")
def save_day(payload: SaveDayInput):
    """Simpan semua entry emiten buat 1 tanggal. Timpa total (delete lalu insert ulang)
    biar user bisa edit/ganti entry hari yang sama tanpa numpuk duplikat."""
    if not payload.entries:
        raise HTTPException(status_code=400, detail="Minimal 1 emiten harus diisi")

    tanggal_str = payload.tanggal.isoformat()

    supabase.table("trading_journal").delete().eq("tanggal", tanggal_str).execute()

    rows = [
        {"tanggal": tanggal_str, "emiten": e.emiten.upper(), "profit_loss": e.profit_loss, "amount": e.amount}
        for e in payload.entries
    ]
    res = supabase.table("trading_journal").insert(rows).execute()

    if not res.data:
        raise HTTPException(status_code=500, detail="Gagal simpan journal ke Supabase")

    return {"data": res.data}


@router.get("")
def get_month(year: int, month: int):
    """Semua entry dalam 1 bulan, dikelompokkan per tanggal buat render kalender."""
    start = date(year, month, 1).isoformat()
    end_month = month + 1 if month < 12 else 1
    end_year = year if month < 12 else year + 1
    end = date(end_year, end_month, 1).isoformat()

    res = (
        supabase.table("trading_journal")
        .select("*")
        .gte("tanggal", start)
        .lt("tanggal", end)
        .order("tanggal")
        .execute()
    )

    by_date: dict[str, list[dict]] = {}
    for row in res.data:
        by_date.setdefault(row["tanggal"], []).append(row)

    days = []
    for tanggal, entries in by_date.items():
        net = sum(e["amount"] if e["profit_loss"] == "profit" else -e["amount"] for e in entries)
        days.append({"tanggal": tanggal, "net": net, "entries": entries})

    return {"data": days}


MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@router.get("/suggestions")
def get_suggestions(year: int, month: int):
    """Prefill semi-auto dari signal_alerts yang UDAH DITUTUP (tp_hit/sl_hit/
    timeout) bulan ini — bukan full-auto (NEXUS gak tau lu beneran ikutin
    call-nya atau lot berapa, broker retail Indonesia gak ada API publik
    individual buat itu), tapi minimal emiten+arah profit/loss udah keisi,
    tinggal user konfirm + isi amount beneran (yang cuma user yang tau)."""
    start = date(year, month, 1).isoformat()
    end_month = month + 1 if month < 12 else 1
    end_year = year if month < 12 else year + 1
    end = date(end_year, end_month, 1).isoformat()

    try:
        res = (
            supabase.table("signal_alerts")
            .select("ticker,closed_at,outcome_pct,status")
            .in_("status", ["tp_hit", "sl_hit", "timeout"])
            .gte("closed_at", start).lt("closed_at", end)
            .execute()
        )
    except Exception:
        return {"data": []}

    suggestions = []
    for r in res.data:
        if r.get("outcome_pct") is None or not r.get("closed_at"):
            continue
        suggestions.append({
            "tanggal": r["closed_at"][:10],
            "emiten": r["ticker"],
            "profit_loss": "profit" if r["outcome_pct"] > 0 else "loss",
            "outcome_pct": r["outcome_pct"],
        })
    return {"data": suggestions}


@router.get("/analytics")
def get_analytics(year: int):
    """Rekap buat halaman Analytics: monthly P&L, win rate, best/worst month,
    emiten paling sering ditrading, dan perbandingan tahunan (semua tahun yang ada data)."""

    # semua entry tahun ini, buat breakdown bulanan + win rate + most traded
    res_year = (
        supabase.table("trading_journal")
        .select("tanggal,emiten,profit_loss,amount")
        .gte("tanggal", date(year, 1, 1).isoformat())
        .lt("tanggal", date(year + 1, 1, 1).isoformat())
        .execute()
    )
    entries = res_year.data

    monthly_pnl = [0.0] * 12
    emiten_count: dict[str, int] = {}
    wins = losses = 0

    for e in entries:
        month_idx = int(e["tanggal"][5:7]) - 1
        signed = e["amount"] if e["profit_loss"] == "profit" else -e["amount"]
        monthly_pnl[month_idx] += signed
        emiten_count[e["emiten"]] = emiten_count.get(e["emiten"], 0) + 1
        if e["profit_loss"] == "profit":
            wins += 1
        else:
            losses += 1

    total_pnl = sum(monthly_pnl)
    win_rate = round(wins / (wins + losses) * 100, 1) if (wins + losses) else 0

    months_with_data = [(MONTH_NAMES[i], v) for i, v in enumerate(monthly_pnl) if v != 0]
    best_month = max(months_with_data, key=lambda m: m[1]) if months_with_data else None
    worst_month = min(months_with_data, key=lambda m: m[1]) if months_with_data else None
    most_traded = max(emiten_count.items(), key=lambda kv: kv[1]) if emiten_count else None

    # semua tahun yang ada data, buat grafik perbandingan tahunan
    res_all = supabase.table("trading_journal").select("tanggal,profit_loss,amount").execute()
    yearly_totals: dict[int, float] = {}
    for e in res_all.data:
        y = int(e["tanggal"][:4])
        signed = e["amount"] if e["profit_loss"] == "profit" else -e["amount"]
        yearly_totals[y] = yearly_totals.get(y, 0) + signed

    return {
        "year": year,
        "total_pnl": total_pnl,
        "win_rate": win_rate,
        "wins": wins,
        "losses": losses,
        "best_month": {"month": best_month[0], "net": best_month[1]} if best_month else None,
        "worst_month": {"month": worst_month[0], "net": worst_month[1]} if worst_month else None,
        "most_traded": {"emiten": most_traded[0], "count": most_traded[1]} if most_traded else None,
        "monthly_pnl": [{"month": MONTH_NAMES[i], "net": v} for i, v in enumerate(monthly_pnl)],
        "yearly_comparison": [{"year": y, "total": t} for y, t in sorted(yearly_totals.items())],
    }