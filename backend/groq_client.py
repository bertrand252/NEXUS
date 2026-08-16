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

    completion = _client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    raw = completion.choices[0].message.content
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Groq gak balikin JSON valid: {raw[:300]}") from e