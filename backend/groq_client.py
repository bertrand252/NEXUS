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


def analyze_alert(ticker: str, score_breakdown: dict, levels: dict) -> dict:
    """Generate alasan singkat buat alert Telegram — dipanggil scheduler.py.
    Return: {"alasan_strong": str, "alasan_risk": str}."""
    system_prompt = (
        "Kamu analis saham IDX. Dikasih breakdown score teknikal + level "
        "support/resistance 1 saham, jelasin singkat (maksimal 2 kalimat pendek "
        "per poin) kenapa sinyalnya kuat, dan kenapa risk-nya segitu. Bahasa "
        "Indonesia santai, to the point, jangan ngasih rekomendasi eksplisit "
        "'beli sekarang' — jelasin data yang ada aja. Balikin JSON persis: "
        '{"alasan_strong": "...", "alasan_risk": "..."}'
    )
    user_prompt = json.dumps({"ticker": ticker, **score_breakdown, **levels}, ensure_ascii=False)
    return ask_json(system_prompt, user_prompt)