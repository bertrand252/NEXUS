import json
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import supabase
from groq_client import ask_json
from forex_factory import get_forex_events

router = APIRouter()

SIMULATE_SYSTEM_PROMPT = """Kamu adalah AI analyst StockSense. Analisa dampak kondisi market terkini terhadap portofolio user.
Gunakan HANYA data yang diberikan di bawah, jangan mengarang data harga/berita yang tidak ada.
Jawaban harus JSON, tidak ada teks bebas di luar JSON.

Format output:
{
  "overall_risk": "low" | "medium" | "high",
  "portfolio_impact_summary": "1-2 kalimat",
  "per_saham": [
    {
      "kode": "BBCA",
      "exposure_pct": 30,
      "risk_level": "low",
      "alasan": "kalimat singkat"
    }
  ],
  "rekomendasi_aksi": ["aksi 1", "aksi 2"]
}"""


class Holding(BaseModel):
    kode: str
    lot: float
    avg_price: float


class SimulateInput(BaseModel):
    holdings: list[Holding]


def _recent_intel_summaries(days: int = 3) -> list[dict[str, Any]]:
    res = (
        supabase.table("daily_market_intel")
        .select("sumber,tanggal,summary_ai")
        .order("tanggal", desc=True)
        .limit(days)
        .execute()
    )
    # cuma summary_ai yang dipakai di prompt, bukan isi_teks mentah — biar hemat token Groq
    return [r for r in res.data if r.get("summary_ai")]


@router.post("/simulate")
def simulate_portfolio(payload: SimulateInput):
    if not payload.holdings:
        raise HTTPException(status_code=400, detail="Portofolio kosong")

    total_value = sum(h.lot * h.avg_price for h in payload.holdings)
    if total_value == 0:
        raise HTTPException(status_code=400, detail="Total nilai portofolio tidak boleh 0")

    holdings_text = "\n".join(
        f"- {h.kode}: {h.lot} lot @ Rp{h.avg_price:,.0f} (exposure {h.lot * h.avg_price / total_value * 100:.1f}%)"
        for h in payload.holdings
    )

    intel = _recent_intel_summaries(days=3)
    forex_events = get_forex_events()
    # kirim yang high/medium impact aja ke prompt biar hemat token — low impact jarang ngaruh ke portofolio
    relevant_events = [e for e in forex_events if e["impact"] in ("High", "Medium")]

    user_prompt = f"""=== PORTOFOLIO USER ===
{holdings_text}

=== EVENT FOREX FACTORY (minggu ini) ===
{json.dumps(relevant_events, ensure_ascii=False) if relevant_events else "Tidak ada event high/medium impact minggu ini."}

=== MARKET INTEL MANUAL (3 hari terakhir) ===
{json.dumps(intel, ensure_ascii=False) if intel else "Tidak ada intel manual yang diinput dalam 3 hari terakhir."}

=== GEOPOLITIK / BERITA RESMI ===
Belum tersedia (RSS feed belum diintegrasikan)."""

    try:
        result = ask_json(SIMULATE_SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Groq gagal memproses simulasi: {e}")

    return result