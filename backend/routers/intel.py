from datetime import date
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import supabase, today_wib
from groq_client import ask_json

router = APIRouter()

SUMMARIZE_SYSTEM_PROMPT = """Kamu adalah analyst assistant. Ringkas teks market outlook berikut menjadi JSON terstruktur.
Jangan menambah opini baru, hanya ekstrak dari teks yang diberikan. Kalau gak ada
info buat suatu field, isi array kosong — JANGAN ngarang.

PENTING soal trade_calls vs poin_penting: kalau teks berisi CALL TRADING SPESIFIK
(rekomendasi beli/jual dengan angka entry/target/stop-loss/cutloss eksplisit —
biasa dari channel sekuritas, bukan analisa umum), JANGAN dimasukin ke
poin_penting. Ekstrak call kayak gitu ke field terpisah "trade_calls" —
poin_penting cuma buat berita/analisa umum TANPA angka entry/target/SL eksplisit
per saham (biar gak nyasar jadi rekomendasi trading pas disintesis jadi daily
briefing — itu bukan tugas kamu, ditangani sistem NEXUS sendiri).

Output HARUS JSON valid, format:
{
  "sentiment": "bullish" | "bearish" | "neutral",
  "sektor_terkait": ["sektor1", "sektor2"],
  "poin_penting": ["poin 1", "poin 2", "poin 3"],
  "saham_disebut": ["BBCA", "TLKM"],
  "event_penting": [{"saham": "BBCA", "jenis": "RUPS" | "dividen" | "stock split" | "lainnya", "tanggal": "YYYY-MM-DD atau teks aslinya kalau tanggal gak pasti", "detail": "ringkasan singkat event-nya"}],
  "trade_calls": [{"saham": "BBCA", "entry": 9500, "target": 9800, "stop_loss": 9300, "alasan": "1 kalimat pendek"}]
}"""


class IntelInput(BaseModel):
    sumber: str
    isi_teks: str
    tanggal: date | None = None


@router.post("")
def submit_intel(payload: IntelInput):
    tanggal = payload.tanggal or today_wib()

    # 1. simpan raw text dulu — isi_teks jangan pernah diubah/diedit
    insert_res = supabase.table("daily_market_intel").insert({
        "tanggal": tanggal.isoformat(),
        "sumber": payload.sumber,
        "isi_teks": payload.isi_teks,
    }).execute()

    if not insert_res.data:
        raise HTTPException(status_code=500, detail="Gagal simpan intel ke Supabase")

    row = insert_res.data[0]

    # 2. panggil Groq buat ringkas (Step A) — kalau gagal, data mentah tetap tersimpan
    try:
        user_prompt = f"Sumber: {payload.sumber}\nTanggal: {tanggal}\nTeks:\n{payload.isi_teks}"
        summary = ask_json(SUMMARIZE_SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        return {**row, "summary_ai": None, "warning": f"Tersimpan, tapi ringkasan AI gagal: {e}"}

    # 3. update row dengan hasil ringkasan
    update_res = supabase.table("daily_market_intel").update({
        "summary_ai": summary,
        "sentiment": summary.get("sentiment"),
        "sektor_terkait": summary.get("sektor_terkait", []),
    }).eq("id", row["id"]).execute()

    return update_res.data[0] if update_res.data else row


@router.get("")
def get_recent_intel(days: int = 3):
    """Riwayat intel terakhir, dipakai buat section 'Berita Terkini' di Portfolio Simulation."""
    res = (
        supabase.table("daily_market_intel")
        .select("*")
        .order("tanggal", desc=True)
        .limit(days)
        .execute()
    )
    return {"data": res.data}