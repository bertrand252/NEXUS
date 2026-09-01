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


def ask_json(system_prompt: str, user_prompt: str, _retry: bool = True) -> dict:
    """Panggil Groq, paksa output JSON, parse, dan lempar error yang jelas kalau gagal.
    Retry SEKALI kalau Groq gagal generate JSON valid (`json_validate_failed` —
    transient, biasanya ilang begitu dicoba ulang, bukan masalah prompt)."""
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
        if _retry and "json_validate_failed" in str(e):
            return ask_json(system_prompt, user_prompt, _retry=False)
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
        if _retry:
            return ask_json(system_prompt, user_prompt, _retry=False)
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
    Return: {"pilih": str|None, "conviction": int, "faktor_pendukung": [str], "alasan_singkat": str}."""
    system_prompt = (
        "Kamu analis saham IDX yang pegang prinsip 'buy on breakout+volume, sell "
        "on news' — begitu suatu saham udah rame diberitakan/viral, biasanya udah "
        "TELAT buat masuk (pemain besar udah akumulasi duluan sebelum publik tau). "
        "Dikasih daftar kandidat saham: breakout_confirmed (udah tembus resistance "
        "20 hari + volume gede, dikonfirmasi data harga real), signal/score NEXUS, "
        "compression_setup + compression_vcp + sideways_days_before (SEBELUM "
        "breakout hari ini, saham ini udah lama sepi/sideways dengan MA5/10/20 "
        "ngumpul rapat — 'coiled spring'). compression_vcp itu versi LENGKAP "
        "(compression_setup + volume MENGECIL selama fase sideways + IHSG lagi "
        "uptrend) — DIBUKTIKAN backtest data real: compression_vcp true "
        "expectancy +0.75%/trade (PALING TINGGI dari semua kategori), breakout "
        "biasa +0.57%, sedangkan compression_setup SENDIRIAN (tanpa volume "
        "dry-up + market uptrend) cuma +0.3% — JUSTRU KALAH dari breakout biasa. "
        "Jadi: KALAU ada beberapa kandidat yang sama-sama breakout_confirmed, "
        "UTAMAKAN yang compression_vcp true. Kalau ADA BEBERAPA kandidat "
        "compression_vcp true sekaligus, UTAMAKAN yang sideways_days_before "
        "PALING PANJANG — prinsip mentor user: 'makin lama sideways/ngumpul "
        "tenaga sebelum breakout, makin kenceng lompatannya', jadi durasi "
        "sideways itu bukan cuma syarat lolos-gak-lolos, tapi juga pembeda "
        "kualitas ANTAR kandidat yang sama-sama lolos. compression_setup true "
        "TAPI compression_vcp false itu BUKAN sinyal unggulan (data buktiin gak lebih "
        "baik dari breakout biasa) — jangan diprioritasin di atas breakout biasa "
        "cuma gara-gara compression_setup true doang. Kalau ada: berita terkait "
        "beberapa hari terakhir, status call aktif mentor trading, "
        "macro_sector_match (event ekonomi global yang searah sektornya), itu "
        "juga jadi pertimbangan. Kalau ada field financial_statement (laporan "
        "keuangan Income Statement per quarter, RAW dari API — bentuk field-nya "
        "bisa macem-macem, baca sendiri apa yang kepake), jadiin konteks "
        "fundamental TAMBAHAN (misal tren pendapatan/laba beberapa periode "
        "terakhir) — BUKAN syarat wajib, dan JANGAN mengarang angka yang gak "
        "ada di situ kalau field-nya gak kamu pahami. Kalau ada order_flow "
        "(buy_volume vs sell_volume dari Volume Profile HARI INI, buy_sell_ratio "
        ">1 artinya demand lebih agresif dari supply) dan broker_net_top (3 broker "
        "net-buy TERBESAR 5 hari terakhir, net_value dalam Rupiah), jadiin sinyal "
        "TAMBAHAN juga — order_flow kuat + broker_net_top ada net-buy besar itu "
        "MEMPERKUAT keyakinan breakout-nya didukung demand institusi beneran, "
        "bukan cuma retail FOMO, tapi BUKAN syarat wajib (data ini bisa kosong "
        "kalau Invezgo gagal fetch). Kalau ada field bandar (broker paling "
        "akumulasi dari TIME SERIES beneran, bukan snapshot kayak broker_net_top "
        "- punya consistency_pct = persen hari broker itu net-buy, dan "
        "steady_accumulation_sideways = true kalau harga sideways lama + "
        "konsistensi tinggi, insight mentor user: pola ini bikin breakout lebih "
        "ringan/kuat), itu sinyal PENGUAT KUAT - WAJIB disebut di "
        "faktor_pendukung kalau steady_accumulation_sideways true. Kalau ada "
        "Kalau ada field seasonality_bulan_ini (rata-rata perubahan harga "
        "historis di bulan yang sama, beberapa tahun terakhir) atau "
        "money_flow_top (arus dana antar broker terbesar hari ini, source ke "
        "target), itu cuma PENDUKUNG SENTIMEN tambahan yang PALING LEMAH dari "
        "semua sinyal di sini — statistik musiman/snapshot doang, BUKAN "
        "katalis konkret kayak bandar/insider_activity, JANGAN jadi alasan "
        "utama milih ticker, boleh disebut cuma kalau sejalan sama sinyal "
        "lain yang lebih kuat. Kalau ada "
        "field insider_activity (histori perubahan kepemilikan pihak yang WAJIB "
        "lapor OJK - insider/direksi/komisaris/pemegang saham >5%, tiap entry "
        "punya prev_percent vs next_percent) dan next_percent NAIK TERUS dari "
        "entry ke entry (tren kepemilikan naik konsisten beberapa minggu/bulan "
        "terakhir), itu sinyal PALING KUAT dari semua (insider/pengendali "
        "sendiri yang narik barang dari pasar, bukan spekulan luar) - WAJIB "
        "disebut eksplisit di faktor_pendukung kalau polanya jelas naik "
        "konsisten. bandar dan insider_activity BUKAN syarat wajib (bisa kosong "
        "kalau Invezgo gagal fetch/gak ada data), tapi kalau ADA dan sinyalnya "
        "kuat, ini jauh lebih meyakinkan dari order_flow/broker_net_top biasa. "
        "Tugas kamu: pilih PALING "
        "BANYAK 1 ticker yang layak di-alert, DAN kasih conviction (1-5) buat "
        "seberapa yakin kamu — 5 = breakout+volume kuat DAN didukung banyak "
        "sinyal tambahan align (compression_vcp/bandar steady_accumulation_sideways/ "
        "insider_activity naik konsisten), 3 = breakout_confirmed doang tanpa "
        "sinyal tambahan yang jelas, 1-2 = breakout pas-pasan/borderline. Jujur "
        "aja, jangan digedein biar keliatan yakin kalau sebenernya biasa aja.\n\n"
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
        "kecuali sinyalnya BENERAN kuat banget (compression_vcp true + "
        "RR tinggi) — sebutin pertimbangan libur ini di alasan_singkat kalau "
        "itu yang bikin kamu skip atau tetep pilih meski ada risiko ini. "
        "Jangan mengarang faktor yang gak ada di data. Balikin JSON persis: "
        '{"pilih": "TICKER" atau null, "conviction": 1-5, '
        '"faktor_pendukung": ["poin 1", "poin 2"], "alasan_singkat": "1 kalimat"}'
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


def evaluate_portfolio_rotation(current_positions: list[dict], new_candidate: dict) -> dict:
    """Portfolio Swing user SELALU dijaga maksimal 5 saham konkuren (keputusan
    eksplisit user — lebih dari itu kesulitan diawasin "kayak supermarket").
    Dipanggil pas 5 slot lagi penuh SEMUA tapi ada kandidat baru yang lolos
    gate breakout+volume/VCP — Groq mutusin worth GANTI posisi PALING LEMAH
    yang lagi dipegang demi kandidat baru ini, atau enggak. Keputusan final
    TETEP di tangan user (dikirim sebagai tombol Terima/Tolak Telegram, NEXUS
    gak pernah auto-eksekusi) — ini cuma REKOMENDASI awal.
    Return: {"rotate": bool, "drop_ticker": str|None, "alasan": str}."""
    system_prompt = (
        "Kamu analis saham IDX yang jagain portofolio Swing user — SELALU "
        "maksimal 5 saham konkuren (keputusan sadar user: lebih dari itu "
        "kesulitan diawasin, bukan soal return doang). Semua 5 slot lagi "
        "kepake (current_positions, ada entry_price/price_now/pnl_pct/target/"
        "stop_loss tiap posisi), TAPI ada 1 kandidat BARU (new_candidate) "
        "yang udah lolos gate breakout+volume/VCP NEXUS. Tugas kamu: putusin "
        "worth GANTI salah satu posisi lama demi kandidat baru ini, atau "
        "enggak.\n\n"
        "PENTING — ini BUKAN keputusan ringan: ganti posisi = REALISASI P&L "
        "SEKARANG JUGA (untung atau rugi apapun posisinya saat ini juga), "
        "BUKAN nunggu TP/SL asli kesentuh. Cuma rotate kalau kandidat baru "
        "JAUH lebih meyakinkan (compression_vcp lebih kuat/sideways_days_"
        "before lebih panjang, rr_ratio lebih bagus) DIBANDING posisi PALING "
        "LEMAH yang lagi dipegang (floating rugi gede tanpa katalis jelas, "
        "atau momentum udah keliatan mati/gak sesuai thesis awal pas entry). "
        "Kalau semua 5 posisi masih on-thesis & wajar (floating untung, atau "
        "rugi kecil tapi belum ada tanda momentum mati), JANGAN rotate cuma "
        "gara-gara ada kandidat baru yang 'lumayan' — itu overtrading, bukan "
        "disiplin. Default-nya NOLAK, cuma rotate kalau alasannya BENERAN "
        "kuat & jelas.\n\n"
        "Jangan mengarang faktor yang gak ada di data. Balikin JSON persis: "
        '{"rotate": true/false, "drop_ticker": "TICKER yang mau diganti" '
        'atau null, "alasan": "penjelasan lengkap kenapa rotate atau enggak, '
        'sebut posisi mana yang paling lemah kalau rotate"}'
    )
    user_prompt = json.dumps(
        {"posisi_running": current_positions, "kandidat_baru": new_candidate},
        ensure_ascii=False,
    )
    result = ask_json(system_prompt, user_prompt)
    if not result.get("rotate"):
        result["rotate"] = False
        result["drop_ticker"] = None
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
    Return: {"pilih": str|None, "conviction": int, "faktor_pendukung": [str], "alasan_singkat": str}."""
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
        "mentor trading kalau ada. Kalau ada field channel_calls (call harga "
        "spesifik entry/target/stop-loss dari channel sekuritas yang "
        "dipantau — BEDA dari call mentor NEXUS), jadiin sinyal TAMBAHAN yang "
        "MENDUKUNG (bukan syarat wajib) — momentum_score tinggi + ada "
        "channel_calls yang searah lebih meyakinkan dari momentum doang.\n\n"
        "SYARAT WAJIB: momentum_score > 0, ATAU ada call aktif mentor. GAK "
        "ADA threshold resmi/baku dari mentor buat BPJS — ini judgment call "
        "kamu, boleh mempertimbangkan berita jangka pendek yang MENDUKUNG "
        "kelanjutan kenaikan (bukan sekadar berita netral). Pilih PALING "
        "BANYAK 1 ticker yang paling meyakinkan, atau null kalau gak ada "
        "yang cukup meyakinkan — mending gak ada call daripada call asal. "
        "Kasih juga conviction (1-5) seberapa yakin kamu — 5 = momentum_score "
        "tinggi banget + berita/mentor call mendukung, 3 = momentum_score lolos "
        "syarat doang tanpa pendukung lain, 1-2 = borderline. Jangan mengarang "
        "berita/faktor yang gak ada di data. Balikin JSON "
        'persis: {"pilih": "TICKER" atau null, "conviction": 1-5, '
        '"faktor_pendukung": ["poin 1"], "alasan_singkat": "1 kalimat"}'
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
        "Kalau konteks_pendukung ada field bandar (broker paling akumulasi + "
        "consistency_pct + steady_accumulation_sideways) atau insider_activity "
        "(histori kepemilikan insider/pengendali, prev_percent vs next_percent) "
        "dan sinyalnya kuat (konsistensi tinggi / kepemilikan naik terus), itu "
        "KATALIS KONKRET paling kuat — sebutin eksplisit di alasan_strong, "
        "jangan cuma order_flow/broker_net_top biasa kalau ada data yang lebih "
        "dalam dari itu. "
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


def ask_hold_or_exit(context: dict) -> dict:
    """Pertimbangan HOLD atau EXIT buat posisi yang UDAH ke-entry (BSJP/BPJS/
    Swing), TP belum kena tapi deadline exit strategi itu udah deket (BSJP:
    harus dijual PAGI besoknya, BPJS: harus dijual SORE ini/sebelum tutup).
    User eksplisit kasih logika intinya: broker paling banyak AKUMULASI itu
    paling banyak PEGANG barang — kalau volume HARI INI jauh di atas
    rata-rata TAPI harga gak ikutan naik kuat, kemungkinan broker itu yang
    lagi JUALAN (dia yang paling banyak punya barang buat dijual).
    Return: {"rekomendasi": "hold"|"exit", "alasan": "penjelasan detail"}."""
    system_prompt = (
        "Kamu analis saham IDX yang bantuin user mutusin HOLD atau EXIT posisi yang "
        "udah dibeli (BSJP/BPJS/Swing), TP belum kena tapi deadline exit strategi ini "
        "udah deket (BSJP: harus jual besok pagi, BPJS: harus jual sore ini sebelum "
        "market tutup). Logika utama: broker yang paling banyak AKUMULASI (net-buy "
        "terbesar, field top_broker_net_lot) itu yang paling banyak PEGANG barang "
        "saham ini sekarang. Kalau volume transaksi HARI INI (volume_today) jauh di "
        "atas rata-rata 20 hari (volume_avg20, lihat volume_ratio_today — misal "
        "2-3x lipat) TAPI harga (price_now vs entry_price) gak ikutan naik kuat/malah "
        "turun, itu indikasi KUAT broker itu lagi JUALAN barangnya — DISARANIN EXIT "
        "(cut loss kalau pnl_pct negatif, stop profit kalau masih untung dikit). "
        "Kalau volume hari ini masih kecil/normal (deket rata-rata, volume_ratio_today "
        "di bawah ~1.5x), itu kemungkinan cuma KOREKSI SEHAT (retail profit taking "
        "kecil-kecilan), BUKAN distribusi bandar beneran — DISARANIN HOLD. Jangan "
        "mengarang angka yang gak ada di context. Alasan HARUS spesifik pake angka "
        "asli dari context (nama/kode broker, lot, rasio volume) — contoh gaya: "
        "'Broker HP paling banyak akumulasi (100rb lot). Volume hari ini 30rb lot vs "
        "rata-rata 10rb lot (3x lipat) — kemungkinan HP yang jual karena dia paling "
        "banyak pegang barang, pertimbangkan keluar.' Bahasa Indonesia santai. "
        'Balikin JSON persis: {"rekomendasi": "hold" atau "exit", "alasan": '
        '"penjelasan detail 2-3 kalimat"}'
    )
    user_prompt = json.dumps(context, ensure_ascii=False)
    return ask_json(system_prompt, user_prompt)