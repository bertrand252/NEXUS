"""
Thin wrapper around Groq chat completions — dipakai buat 2 prompt di spec:
  Step A: Summarize Intel
  Step B: Portfolio Simulation
Kedua prompt minta output JSON murni, jadi wrapper ini yang urus parsing +
error handling-nya, biar router-nya gak perlu ulang-ulang kode yang sama.
"""
import json
from groq import Groq
from config import GROQ_API_KEY, GROQ_MODEL

_client = Groq(api_key=GROQ_API_KEY)


def ask_json(system_prompt: str, user_prompt: str) -> dict:
    """Panggil Groq, paksa output JSON, parse, dan lempar error yang jelas kalau gagal."""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY belum diisi di .env")

    try:
        completion = _client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
    except Exception as e:
        # error asli (misal ConnectError) sering ke-summarize jadi pesan generik
        # kayak "Connection error." — bongkar cause chain-nya biar keliatan akar
        # masalahnya beneran apa (DNS? TLS? timeout? refused?)
        cause = e.__cause__ or e.__context__
        detail = f"{type(e).__name__}: {e}"
        if cause is not None and cause is not e:
            detail += f" | cause: {type(cause).__name__}: {cause}"
        raise RuntimeError(f"Groq request gagal — {detail}") from e

    raw = completion.choices[0].message.content
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Groq gak balikin JSON valid: {raw[:300]}") from e


def translate_to_indonesian(text: str) -> str:
    """Terjemahin teks (misal deskripsi bisnis perusahaan dari yfinance, bahasa
    Inggris) ke Bahasa Indonesia natural. Dipanggil on-demand, bukan tiap load."""
    system_prompt = (
        'Terjemahin teks berikut ke Bahasa Indonesia yang natural dan enak dibaca, '
        'gaya formal secukupnya (ini deskripsi bisnis perusahaan). Jangan nambah/ngurangin '
        'informasi. Balikin JSON: {"translated": "..."}'
    )
    result = ask_json(system_prompt, text)
    return result.get("translated", text)


def pick_alert_candidate(candidates: list[dict], macro_events: list[dict]) -> dict:
    """Fase 1 seleksi alert Telegram — dikasih pool kandidat (score NEXUS + berita
    terkait + status call mentor kalau ada) plus event ekonomi global minggu ini.
    Groq milih PALING BANYAK 1 ticker yang layak di-alert, atau nolak semua kalau
    gak ada yang meyakinkan. Return: {"pilih": str|None, "faktor_pendukung": [str],
    "alasan_singkat": str}."""
    system_prompt = (
        "Kamu analis saham IDX yang skeptis, bukan yang gampang excited. Dikasih "
        "daftar kandidat saham (skor teknikal NEXUS, dan kalau ada: berita terkait "
        "beberapa hari terakhir, status call aktif dari mentor trading) plus event "
        "ekonomi global minggu ini. Tugas kamu: pilih PALING BANYAK 1 ticker yang "
        "layak di-alert. SYARAT WAJIB: skor teknikal-nya Strong/Moderate DAN ada "
        "minimal 1 faktor pendukung KONKRET di luar skor — berita yang sejalan "
        "sama ticker/sektornya, ATAU jadi call aktif mentor, ATAU event ekonomi "
        "global yang mendukung sektornya. JANGAN pilih cuma modal skor tinggi "
        "tanpa faktor pendukung lain, dan jangan mengarang faktor yang gak ada di "
        "data. Kalau gak ada kandidat yang beneran meyakinkan, WAJIB balikin "
        "pilih: null — mending gak ada call daripada call asal-asalan. Balikin "
        "JSON persis: {\"pilih\": \"TICKER\" atau null, \"faktor_pendukung\": "
        '["poin 1", "poin 2"], "alasan_singkat": "1 kalimat"}'
    )
    user_prompt = json.dumps(
        {"kandidat": candidates, "event_ekonomi_global": macro_events}, ensure_ascii=False
    )
    result = ask_json(system_prompt, user_prompt)
    if not result.get("pilih") or result.get("pilih") == "null":
        result["pilih"] = None
    return result


def analyze_alert(ticker: str, score_breakdown: dict, levels: dict, context: dict | None = None) -> dict:
    """Generate alasan alert Telegram — dipanggil scheduler.py. `context` opsional
    (dari pick_alert_candidate + fase 2): berita, mentor call, event ekonomi global,
    ringkasan fundamental — biar alasannya ngerujuk bukti konkret, bukan cuma angka
    score. Return: {"alasan_strong": str, "alasan_risk": str}."""
    system_prompt = (
        "Kamu analis saham IDX. Dikasih breakdown score teknikal, level "
        "support/resistance, dan (kalau ada) konteks pendukung — berita, call "
        "mentor, event ekonomi global, ringkasan fundamental perusahaan. Jelasin "
        "singkat (maksimal 2-3 kalimat pendek per poin) kenapa sinyalnya kuat — "
        "SEBUTIN faktor pendukung konkret yang dikasih kalau ada, jangan cuma "
        "ngomongin angka score — dan kenapa risk-nya segitu. Bahasa Indonesia "
        "santai, to the point, jangan ngasih rekomendasi eksplisit 'beli sekarang' "
        "— jelasin data yang ada aja, jangan mengarang yang gak ada di data. "
        "Balikin JSON persis: {\"alasan_strong\": \"...\", \"alasan_risk\": \"...\"}"
    )
    payload = {"ticker": ticker, **score_breakdown, **levels}
    if context:
        payload["konteks_pendukung"] = context
    user_prompt = json.dumps(payload, ensure_ascii=False)
    return ask_json(system_prompt, user_prompt)