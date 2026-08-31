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


def assess_running_positions(positions: list[dict]) -> dict:
    """Digest harian buat semua signal_alerts yang masih 'open' — 1 call Groq
    buat SEMUA posisi sekaligus (bukan per-ticker, hemat TPM — pernah kena
    limit gara-gara kebanyakan call kecil-kecil). Tiap posisi dikasih:
    ticker, entry_price, price_now, pnl_pct, target, stop_loss, berita
    terkini (kalau ada). Groq nilai tiap posisi: masih layak dipegang, atau
    ada alasan konkret buat cut loss SEKARANG (bukan modal harga nyentuh SL
    doang — itu udah otomatis kedeteksi Python, ini soal ADA GAK alasan
    fundamental/berita yang bikin lebih urgent dari technical stop-loss).
    Return: {"verdicts": [{"ticker": str, "verdict": "lanjut"|"urgent_cl",
    "alasan": str}]}."""
    system_prompt = (
        "Kamu analis saham IDX yang mantau posisi trading yang lagi 'running' "
        "(udah di-alert sebelumnya, belum ditutup). Dikasih daftar posisi: "
        "ticker, harga beli (entry_price), harga sekarang (price_now), PnL saat "
        "ini (pnl_pct), target TP, stop loss, dan berita terkini kalau ada. "
        "Tugas kamu: nilai tiap posisi — 'lanjut' (masih layak dipegang sampai "
        "TP) atau 'urgent_cl' (ada alasan KONKRET buat cut loss lebih awal dari "
        "stop-loss teknikal — misal berita buruk spesifik, sentimen sektor "
        "berbalik, dll). JANGAN kasih 'urgent_cl' cuma karena harga lagi turun "
        "dikit — itu udah dihandle otomatis kalau nyentuh angka stop_loss. "
        "'urgent_cl' cuma buat kondisi yang BENERAN butuh perhatian sebelum "
        "nyentuh stop-loss teknikal. Kalau gak ada berita/konteks yang berubah, "
        "default 'lanjut' dengan alasan singkat berdasarkan PnL & jarak ke "
        "TP/SL. Jangan mengarang berita yang gak ada di data. Balikin JSON "
        'persis: {"verdicts": [{"ticker": "TICKER", "verdict": "lanjut" atau '
        '"urgent_cl", "alasan": "1-2 kalimat"}]}'
    )
    user_prompt = json.dumps({"posisi": positions}, ensure_ascii=False)
    return ask_json(system_prompt, user_prompt)


def pick_alert_candidate(candidates: list[dict], macro_events: list[dict], upcoming_holidays: list[dict] | None = None) -> dict:
    """Fase 1 seleksi alert Telegram — dikasih pool kandidat (technical_score/
    breakout_confirmed dari NEXUS, berita terkait kalau ada, status call mentor
    kalau ada) plus event ekonomi global minggu ini + hari libur bursa yang
    deket (weekend/tanggal merah — rawan profit taking sebelum bursa tutup
    lama). Groq milih PALING BANYAK 1 ticker yang layak di-alert, atau nolak
    semua kalau gak ada yang meyakinkan.
    Return: {"pilih": str|None, "faktor_pendukung": [str], "alasan_singkat": str}."""
    system_prompt = (
        "Kamu analis saham IDX yang pegang prinsip 'buy on breakout+volume, sell "
        "on news' — begitu suatu saham udah rame diberitakan/viral, biasanya udah "
        "TELAT buat masuk (pemain besar udah akumulasi duluan sebelum publik tau). "
        "Dikasih daftar kandidat saham: breakout_confirmed (udah tembus resistance "
        "20 hari + volume gede, dikonfirmasi data harga real), signal/score NEXUS, "
        "compression_setup + sideways_days_before (SEBELUM breakout hari ini, "
        "saham ini udah lama sepi/sideways dengan MA5/10/20 ngumpul rapat — "
        "'coiled spring', prinsip: makin lama sideways makin kenceng lompatannya "
        "pas akhirnya breakout — ini SINYAL KUAT, breakout dari setup compression "
        "jauh lebih meyakinkan daripada breakout dari saham yang emang udah "
        "lincah/gak pernah diem), dan kalau ada: berita terkait beberapa hari "
        "terakhir, status call aktif mentor trading, macro_sector_match (event "
        "ekonomi global yang searah sektornya). Tugas kamu: pilih PALING BANYAK 1 "
        "ticker yang layak di-alert — KALAU ada beberapa kandidat yang sama-sama "
        "breakout_confirmed, UTAMAKAN yang compression_setup true.\n\n"
        "SYARAT WAJIB: breakout_confirmed harus true, ATAU ada call aktif dari "
        "mentor trading (itu analisa manusia beneran, bukan hype). JANGAN pilih "
        "ticker cuma karena ada berita bagus tanpa breakout_confirmed — itu "
        "persis kesalahan yang mau dihindari (beli di puncak pas berita udah "
        "nyebar). Kalau ada kandidat yang beritanya udah rame/bullish TAPI "
        "breakout_confirmed-nya false, JANGAN dipilih, dan boleh disebut di "
        "alasan_singkat sebagai 'udah telat, breakout belum kekonfirmasi'. "
        "Berita, mentor call, dan macro cuma jadi KONTEKS TAMBAHAN buat kandidat "
        "yang udah breakout_confirmed — bukan pengganti breakout itu sendiri. "
        "Kalau gak ada kandidat yang penuhi syarat wajib, WAJIB balikin pilih: "
        "null — mending gak ada call daripada call asal-asalan atau telat. "
        "\n\nSOAL LIBUR BURSA (upcoming_holidays): kalau ada hari libur/weekend "
        "dalam 1-3 hari ke depan (apalagi kalau lebih dari 2 hari tutup "
        "berturut-turut — bukan cuma weekend biasa), itu masa rawan PROFIT "
        "TAKING (banyak trader jual sebelum bursa tutup lama, harga cenderung "
        "turun). Kalau kandidat breakout-nya PAS deket tanggal-tanggal itu, "
        "lebih baik SKIP dulu (pilih null) walau breakout_confirmed true, "
        "kecuali sinyalnya BENERAN kuat banget (compression_setup true + "
        "RR tinggi) — sebutin pertimbangan libur ini di alasan_singkat kalau "
        "itu yang bikin kamu skip atau tetep pilih meski ada risiko ini. "
        "Jangan mengarang faktor yang gak ada di data. Balikin JSON persis: "
        '{"pilih": "TICKER" atau null, "faktor_pendukung": ["poin 1", "poin 2"], '
        '"alasan_singkat": "1 kalimat"}'
    )
    user_prompt = json.dumps(
        {"kandidat": candidates, "event_ekonomi_global": macro_events,
         "upcoming_holidays": upcoming_holidays or []},
        ensure_ascii=False,
    )
    result = ask_json(system_prompt, user_prompt)
    if not result.get("pilih") or result.get("pilih") == "null":
        result["pilih"] = None
    return result


def pick_bpjs_candidate(candidates: list[dict]) -> dict:
    """Fase 1 seleksi alert BPJS (Day Trade) — BEDA gate dari Swing
    (pick_alert_candidate): BPJS gak butuh breakout_confirmed resmi 20-hari,
    basisnya momentum_score (volume+harga naik di SESI berjalan hari ini,
    dari intraday.py::session_takeoff, dibanding rata-rata sesi yang sama
    hari-hari sebelumnya) ATAU call aktif mentor, digabung berita jangka
    pendek — HARAPANNYA harga lanjut naik ke hari berikutnya (beda BSJP yang
    reaktif sore-ini-jual-besok-pagi). Gak ada indikator resmi/baku dari
    mentor buat BPJS — ini judgment call gabungan, mirip pick_alert_candidate
    tapi gate-nya lebih longgar sesuai sifatnya.
    Return: {"pilih": str|None, "faktor_pendukung": [str], "alasan_singkat": str}."""
    system_prompt = (
        "Kamu analis saham IDX yang nyari kandidat 'BPJS' (Day Trade) — beda "
        "dari Swing (breakout resistance 20 hari, dipegang berminggu-minggu) "
        "dan beda dari BSJP (beli sore jual pagi besoknya, reaktif 1 hari). "
        "BPJS itu momentum yang lagi kejadian HARI INI (volume+harga naik di "
        "sesi perdagangan yang lagi jalan, dibanding kebiasaan sesi yang sama "
        "hari-hari sebelumnya — field momentum_score, makin tinggi makin "
        "kuat) DIGABUNG berita/katalis jangka pendek, dengan HARAPAN harga "
        "lanjut naik 1-2 hari ke depan (bukan cuma hari ini doang). Dikasih "
        "daftar kandidat: ticker, momentum_score, session (sesi 1 atau sesi 2 "
        "yang lagi diukur), berita terkait kalau ada, status call aktif "
        "mentor trading kalau ada.\n\n"
        "SYARAT WAJIB: momentum_score > 0, ATAU ada call aktif mentor. GAK "
        "ADA threshold resmi/baku dari mentor buat BPJS — ini judgment call "
        "kamu, boleh mempertimbangkan berita jangka pendek yang MENDUKUNG "
        "kelanjutan kenaikan (bukan sekadar berita netral). Pilih PALING "
        "BANYAK 1 ticker yang paling meyakinkan, atau null kalau gak ada "
        "yang cukup meyakinkan — mending gak ada call daripada call asal. "
        "Jangan mengarang berita/faktor yang gak ada di data. Balikin JSON "
        'persis: {"pilih": "TICKER" atau null, "faktor_pendukung": ["poin 1"], '
        '"alasan_singkat": "1 kalimat"}'
    )
    user_prompt = json.dumps({"kandidat": candidates}, ensure_ascii=False)
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
        "Kamu analis saham IDX yang pegang prinsip 'buy on breakout+volume, sell "
        "on news'. Dikasih breakdown score teknikal (technical_score = breakout "
        "resistance 20 hari + volume, INI alasan utamanya), level support/"
        "resistance, dan (kalau ada) konteks tambahan — berita, call mentor, "
        "event ekonomi global, ringkasan fundamental perusahaan. Jelasin singkat "
        "(maksimal 2-3 kalimat pendek per poin) kenapa sinyalnya kuat — UTAMAKAN "
        "breakout+volume-nya, sebutin konteks tambahan cuma sebagai pelengkap. "
        "Kalau ada berita bullish yang udah beredar duluan, sebutin itu sebagai "
        "CATATAN HATI-HATI (kemungkinan udah gak terlalu awal lagi), bukan "
        "sebagai alasan utama kenapa masuk. "
        "KHUSUS alasan_strong: KALAU ada konteks pendukung (berita/fundamental/"
        "macro) yang ngasih katalis konkret di luar teknikal (misal: musiman, "
        "permintaan komoditas, aksi korporasi, kebijakan), WAJIB disebutin — "
        "jangan cuma ngomongin breakout+volume doang kalau ada cerita yang lebih "
        "kuat dari itu. Kalau MEMANG gak ada katalis apa-apa di konteks yang "
        "dikasih, bilang jujur 'berdasarkan teknikal doang, belum ada katalis "
        "fundamental yang kedeteksi' — JANGAN ngarang katalis yang gak ada. "
        "KHUSUS alasan_risk: jangan cuma bilang 'karena resistance/support 20 "
        "hari', jelasin KENAPA level itu masuk akal buat target/stop (misal "
        "riwayat harga mantul di situ, atau posisinya deket resistance lama). "
        "Bahasa Indonesia santai, to the point, jangan ngasih rekomendasi "
        "eksplisit 'beli sekarang' — jelasin data yang ada aja, jangan mengarang "
        "yang gak ada di data. Balikin JSON persis: "
        '{"alasan_strong": "...", "alasan_risk": "..."}'
    )
    payload = {"ticker": ticker, **score_breakdown, **levels}
    if context:
        payload["konteks_pendukung"] = context
    user_prompt = json.dumps(payload, ensure_ascii=False)
    return ask_json(system_prompt, user_prompt)


def explain_levels(ticker: str, score_breakdown: dict, levels: dict) -> str:
    """Penjelasan singkat buat chart Stock Detail — KENAPA level support/
    resistance yang UDAH DIHITUNG (levels.py, rule-based 20-hari min/max) itu
    relevan buat ticker ini sekarang. Sengaja gak nyuruh Groq nemuin level
    baru sendiri — itu bikin 2 sumber kebenaran level yang bisa kontradiksi
    sama breakout logic yang dipake scoring/alert. On-demand doang (tombol),
    bukan auto tiap buka halaman, biar gak boros call Groq."""
    system_prompt = (
        "Kamu analis saham IDX. Dikasih breakdown score teknikal (technical_score "
        "= breakout resistance 20 hari + volume) dan level support/resistance yang "
        "UDAH dihitung (jangan nemuin level baru, cuma jelasin yang ada). Jelasin "
        "dalam 1-2 kalimat pendek, bahasa Indonesia santai: posisi harga sekarang "
        "relatif ke level-level itu ngapain (deket breakout? masih jauh? udah "
        "lewat resistance?), dan apa artinya buat trader yang lagi liat chart ini. "
        "Jangan kasih rekomendasi eksplisit 'beli/jual', jangan mengarang data yang "
        "gak ada. Balikin JSON persis: {\"penjelasan\": \"...\"}"
    )
    user_prompt = json.dumps({"ticker": ticker, **score_breakdown, **levels}, ensure_ascii=False)
    result = ask_json(system_prompt, user_prompt)
    return result.get("penjelasan", "")


def generate_postmortem(summary: dict) -> dict:
    """Weekly Postmortem — dikasih rekap SEMUA posisi (Swing+BPJS) yang closed
    7 hari terakhir (menang/kalah/timeout per ticker), Groq cari POLA (bukan
    nge-judge tiap trade satu-satu). JANGAN ngarang alasan spesifik yang gak
    ada di data (misal nyebut berita/sektor spesifik kalau gak dikasih) —
    kalau datanya kurang buat nemuin pola jelas, bilang jujur apa adanya."""
    system_prompt = (
        "Kamu analis trading yang review histori seminggu terakhir. Dikasih rekap "
        "posisi yang udah closed (ticker, source Swing/BPJS, status tp_hit/sl_hit/"
        "timeout, outcome_pct). Cari POLA yang kelihatan dari data ini doang "
        "(misal: 'kebanyakan kena SL', 'timeout mendominasi berarti target "
        "kejauhan', 'BPJS lebih akurat dari Swing minggu ini') — JANGAN ngarang "
        "alasan spesifik (berita/sektor/apapun) yang gak ada di data yang dikasih. "
        "Kalau datanya sedikit/gak ada pola jelas, bilang jujur apa adanya, jangan "
        "maksa nyimpulin sesuatu. Bahasa Indonesia santai, 2-3 kalimat tiap bagian. "
        "Balikin JSON persis: {\"pola\": \"...\", \"saran\": \"...\"}"
    )
    user_prompt = json.dumps(summary, ensure_ascii=False)
    return ask_json(system_prompt, user_prompt)