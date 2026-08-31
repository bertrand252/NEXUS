import json
from typing import Any
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from config import supabase
from groq_client import ask_json
from forex_factory import get_forex_events
from rate_limit import limiter
import invezgo_client

router = APIRouter()

SIMULATE_SYSTEM_PROMPT = """Kamu adalah AI analyst StockSense. Analisa dampak kondisi market terkini terhadap portofolio user.
Gunakan HANYA data yang diberikan di bawah, jangan mengarang data harga/berita yang tidak ada.
Kalau ada laporan keuangan (Income Statement per quarter, RAW dari API, bentuk field bisa macem-macem — baca sendiri apa yang kepake) buat salah satu saham, jadiin konteks fundamental TAMBAHAN pas nilai risk_level saham itu (misal tren laba lagi turun = risk lebih tinggi) — JANGAN mengarang angka yang gak ada di datanya.
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
    total_capital: float | None = None  # modal total trading (holdings + cash nganggur) —
                                          # opsional, cuma dibutuhin buat money management check


MM_SLOT_PCT = 16   # sinkron manual portfolio_backtest.py::MENTOR_SLOT_PCT — max % modal per saham.
                    # 5×16%=80% deployed, sisa 20% reserve.
MM_MAX_SLOTS = 5   # sinkron manual portfolio_backtest.py::MENTOR_MAX_POSITIONS & scheduler.py::
                    # MAX_CONCURRENT_SWING — keputusan user: portofolio SELALU 5 saham Swing
                    # konkuren (lebih dari itu susah diawasin "kaya supermarket")


def _money_management_check(holdings: list[dict], total_capital: float) -> dict:
    """Money management ala mentor user (bukan AI/Groq — ini kalkulasi
    deterministik doang): max 20% modal per saham, max 80% modal dipakai
    (4 slot), 20% sisa jadi 'amunisi' — kalau 1 slot abis kena CL, slot
    berikutnya TETEP dapet 'peluru' penuh 20% dari modal AWAL (bukan dari
    modal berjalan), bukan cuma sisa abis rugi. Lihat portfolio_backtest.py
    ::simulate_portfolio_mentor buat versi backtest historisnya."""
    max_per_stock = total_capital * (MM_SLOT_PCT / 100)
    deployed = sum(h["lot"] * h["avg_price"] for h in holdings)
    cash_available = total_capital - deployed

    warnings = []
    for h in holdings:
        value = h["lot"] * h["avg_price"]
        if value > max_per_stock * 1.001:  # toleransi pembulatan kecil
            pct = round(value / total_capital * 100, 1)
            warnings.append(f"{h['kode']} kelewat {MM_SLOT_PCT}% modal (sekarang {pct}%) — over-konsentrasi.")

    slots_used = len(holdings)
    slots_remaining = max(0, MM_MAX_SLOTS - slots_used)
    next_position_ammo = min(max_per_stock, cash_available) if cash_available > 0 else 0

    return {
        "max_per_stock_pct": MM_SLOT_PCT,
        "max_per_stock_amount": round(max_per_stock, 2),
        "max_slots": MM_MAX_SLOTS,
        "slots_used": slots_used,
        "slots_remaining": slots_remaining,
        "deployed_amount": round(deployed, 2),
        "deployed_pct": round(deployed / total_capital * 100, 1) if total_capital else 0,
        "cash_available": round(cash_available, 2),
        "next_position_ammo": round(next_position_ammo, 2),
        "warnings": warnings,
    }


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


def _simulate(holdings: list[dict]) -> dict:
    """Logic inti simulasi — reusable dari route HTTP maupun scheduler.py
    (buat _check_portfolio_risk, cek risk harian tanpa nunggu user klik
    tombol). `holdings`: list of {kode, lot, avg_price}."""
    total_value = sum(h["lot"] * h["avg_price"] for h in holdings)
    if total_value == 0:
        raise ValueError("Total nilai portofolio tidak boleh 0")

    holdings_text = "\n".join(
        f"- {h['kode']}: {h['lot']} lot @ Rp{h['avg_price']:,.0f} (exposure {h['lot'] * h['avg_price'] / total_value * 100:.1f}%)"
        for h in holdings
    )

    intel = _recent_intel_summaries(days=3)
    forex_events = get_forex_events()
    # kirim yang high/medium impact aja ke prompt biar hemat token — low impact jarang ngaruh ke portofolio
    relevant_events = [e for e in forex_events if e["impact"] in ("High", "Medium")]

    # laporan keuangan (Income Statement per quarter) per saham di portofolio —
    # konteks fundamental TAMBAHAN buat Groq nilai risk_level (lihat prompt).
    # Diem total kalau Invezgo belum aktif. RAW dict apa adanya, gak di-parsing
    # manual (struktur field belum diverifikasi lawan API asli).
    financials = {}
    if invezgo_client.is_configured():
        for h in holdings:
            try:
                financials[h["kode"]] = invezgo_client.get_financial_statement(h["kode"], statement="IS")
            except Exception:
                financials[h["kode"]] = None

    user_prompt = f"""=== PORTOFOLIO USER ===
{holdings_text}

=== LAPORAN KEUANGAN (Income Statement per quarter, per saham) ===
{json.dumps(financials, ensure_ascii=False) if financials else "Belum tersedia — nunggu Invezgo API aktif."}

=== EVENT FOREX FACTORY (minggu ini) ===
{json.dumps(relevant_events, ensure_ascii=False) if relevant_events else "Tidak ada event high/medium impact minggu ini."}

=== MARKET INTEL MANUAL (3 hari terakhir) ===
{json.dumps(intel, ensure_ascii=False) if intel else "Tidak ada intel manual yang diinput dalam 3 hari terakhir."}

=== GEOPOLITIK / EVENT KHUSUS IDX (RUPS, dividen, MSCI, dll) ===
Belum tersedia — nunggu sumber data otomatis (belum ada API gratis buat data ini)."""

    return ask_json(SIMULATE_SYSTEM_PROMPT, user_prompt)


@router.post("/simulate")
@limiter.limit("5/minute")  # tiap panggilan manggil Groq (biaya + TPM limit)
def simulate_portfolio(request: Request, payload: SimulateInput):
    """Simulasi TEST doang — gak persist ke portfolio_holdings. Pisah dari
    /active biar coba-coba skenario ("what if beli CSMI 200 lot") gak
    kebawa jadi portofolio yang di-review scheduler.py::_check_portfolio_risk
    tiap malam (bug lama: dulu tiap simulate SELALU nimpa portfolio_holdings,
    termasuk auto-run pas halaman baru dibuka)."""
    if not payload.holdings:
        raise HTTPException(status_code=400, detail="Portofolio kosong")

    holdings = [h.model_dump() for h in payload.holdings]
    try:
        result = _simulate(holdings)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Groq gagal memproses simulasi: {e}")

    if payload.total_capital and payload.total_capital > 0:
        result["money_management"] = _money_management_check(holdings, payload.total_capital)
    else:
        result["money_management"] = None

    return result


@router.get("/active")
def get_active_portfolio():
    """Portofolio AKTIF (beda dari simulasi test) — ini yang dipantau
    scheduler.py::_check_portfolio_risk tiap malam. Cuma keisi kalau user
    eksplisit klik "Simpan sebagai Portofolio Aktif"."""
    try:
        res = supabase.table("portfolio_holdings").select("holdings").eq("id", 1).limit(1).execute()
    except Exception:
        return {"holdings": None}
    return {"holdings": res.data[0]["holdings"] if res.data else None}


@router.post("/active")
def save_active_portfolio(payload: SimulateInput):
    """Simpan holdings sebagai portofolio AKTIF — eksplisit, terpisah dari
    /simulate (test). Ini yang dibaca scheduler.py::_check_portfolio_risk
    buat Nightly Portfolio Review."""
    if not payload.holdings:
        raise HTTPException(status_code=400, detail="Portofolio kosong")
    holdings = [h.model_dump() for h in payload.holdings]
    supabase.table("portfolio_holdings").upsert({"id": 1, "holdings": holdings}).execute()
    return {"ok": True}