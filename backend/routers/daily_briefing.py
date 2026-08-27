from datetime import date, timedelta
from fastapi import APIRouter
from config import supabase
from groq_client import ask_json

router = APIRouter()

BRIEFING_SYSTEM_PROMPT = """Kamu analyst assistant buat NEXUS, platform screening saham IDX. Dikasih
kumpulan ringkasan berita/intel market beberapa hari terakhir (masing-masing udah
ada sentiment, poin penting, saham yang disebut, dan event penting kayak
RUPS/dividen/stock split kalau ada). Sintesis jadi 1 daily briefing.

ATURAN PENTING: JANGAN ngarang data yang gak ada di input. Kalau gak ada cukup
info buat rekomendasi solid, bilang jujur di ringkasan dan biarin rekomendasi
kosong — jangan maksa. Tiap rekomendasi HARUS nyebutin alasan spesifik yang
merujuk ke data yang diberikan, bukan opini baru.

Output HARUS JSON valid, format:
{
  "market_sentiment": "bullish" | "bearish" | "neutral" | "mixed",
  "ringkasan": "1-2 paragraf ringkasan kondisi market hari ini berdasarkan berita yang masuk",
  "tanggal_penting": [{"saham": "BBCA", "jenis": "RUPS", "tanggal": "2026-09-05", "detail": "ringkasan singkat"}],
  "rekomendasi": [{"saham": "BBCA", "alasan": "kenapa direkomendasiin, berdasarkan data mana"}]
}"""


def _generate_briefing(days: int = 3) -> dict:
    since = (date.today() - timedelta(days=days)).isoformat()
    res = (
        supabase.table("daily_market_intel")
        .select("sumber,tanggal,summary_ai")
        .gte("tanggal", since)
        .execute()
    )
    entries = [e for e in res.data if e.get("summary_ai")]

    if not entries:
        briefing = {
            "market_sentiment": "neutral",
            "ringkasan": "Belum ada intel yang masuk beberapa hari terakhir — belum bisa disintesis.",
            "tanggal_penting": [],
            "rekomendasi": [],
        }
    else:
        lines = [
            f"[{e['tanggal']} - {e['sumber']}] sentiment={e['summary_ai'].get('sentiment')}, "
            f"saham={e['summary_ai'].get('saham_disebut', [])}, "
            f"poin={e['summary_ai'].get('poin_penting', [])}, "
            f"event={e['summary_ai'].get('event_penting', [])}"
            for e in entries
        ]
        briefing = ask_json(BRIEFING_SYSTEM_PROMPT, "\n".join(lines))

    briefing["tanggal"] = date.today().isoformat()
    supabase.table("daily_briefing").upsert(briefing, on_conflict="tanggal").execute()
    return briefing


@router.get("")
def get_daily_briefing():
    res = supabase.table("daily_briefing").select("*").order("tanggal", desc=True).limit(1).execute()
    if not res.data:
        return {"data": None, "warning": "Belum ada briefing — jalanin POST /daily-briefing/refresh dulu."}
    return {"data": res.data[0], "warning": None}


@router.post("/refresh")
def refresh_daily_briefing():
    return _generate_briefing()
