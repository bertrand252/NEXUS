"""
Accumulation scoring, max 100 total:
  Volume Score        /25  -> REAL, dari yfinance (rasio volume hari ini vs rata-rata 20 hari)
  Price Score         /25  -> REAL, dari yfinance (momentum harga 5 hari + posisi vs high/low)
  Accumulation Score  /30  -> REAL kalau INVEZGO_API_KEY keisi (top BDM/foreign flow),
                              MOCK (hash ticker) kalau belum subscribe Invezgo
  Technical Score     /20  -> REAL, dari yfinance (breakout resistance 20 hari + volume,
                              lihat technical_score() buat alasan kenapa bukan RSI+MA20)
"""
import hashlib


def _deterministic_mock(ticker: str, seed_salt: str, max_val: int) -> int:
    h = hashlib.md5(f"{ticker}-{seed_salt}".encode()).hexdigest()
    return int(h, 16) % (max_val + 1)


def volume_score(volume_today: float, volume_avg20: float) -> int:
    """0-25. Rasio volume hari ini vs rata-rata 20 hari — makin tinggi rasio, makin kuat sinyal."""
    if not volume_avg20:
        return 0
    ratio = volume_today / volume_avg20
    if ratio >= 3.0:
        return 25
    if ratio >= 2.0:
        return 19
    if ratio >= 1.5:
        return 13
    if ratio >= 1.0:
        return 6
    return 0


def price_score(price_now: float, price_5d_ago: float, low_20d: float, high_20d: float) -> int:
    """0-25. Momentum 5 hari + posisi harga relatif terhadap range 20 hari."""
    if not price_5d_ago or not (high_20d - low_20d):
        return 0
    momentum_pct = (price_now - price_5d_ago) / price_5d_ago * 100
    position_pct = (price_now - low_20d) / (high_20d - low_20d)  # 0 = di low, 1 = di high

    score = 0
    score += 13 if momentum_pct > 5 else 9 if momentum_pct > 2 else 4 if momentum_pct > 0 else 0
    score += 12 if position_pct > 0.7 else 8 if position_pct > 0.4 else 4 if position_pct > 0.2 else 0
    return score


def accumulation_score(ticker: str, accum_lookup: dict | None) -> int:
    """0-30. Kalau Invezgo aktif, accum_lookup dibangun sekali per refresh dari
    GET /analysis/top/accumulation + /analysis/top/foreign (bukan per-ticker call —
    endpoint itu ngasih semua top mover market dalam 1 call). Ticker yang ke-flag
    di top accumulation list dikasih skor tinggi, top distribution dikasih rendah,
    yang gak ke-flag salah satu (paling banyak, wajar — cuma top mover yang di-list)
    dikasih skor netral, JUJUR nunjukin "gak ada sinyal kuat" bukan ngarang angka
    presisi kayak mock lama.

    CATATAN: pembagian skor ini first-pass heuristic, belum dikalibrasi lawan data
    Invezgo asli (belum ada API key aktif pas ini ditulis) — wajar disesuaikan lagi
    begitu keliatan bentuk & jumlah data asli dari top accumulation/foreign list.

    Kalau Invezgo belum di-subscribe (accum_lookup None), fallback ke hash mock lama."""
    if accum_lookup is None:
        return _deterministic_mock(ticker, "accumulation", 30)
    signal = accum_lookup.get(ticker)
    if signal == "accum":
        return 26
    if signal == "dist":
        return 4
    return 15  # netral — gak ke-flag di top accumulation atau top distribution


def technical_score(price_now: float, resistance_prior: float, volume_ratio: float,
                     close_position_pct: float, value_traded_idr: float) -> int:
    """0-20. "Buy on breakout+volume", bukan RSI/MA20 — filosofi trading yang dipake
    di sini: begitu berita/rame publik biasanya udah telat ("sell on news"), sinyal
    yang lebih awal itu harga tembus resistance range 20 hari DIKONFIRMASI volume
    gede (indikasi ada pemain besar masuk sebelum ramai diberitakan).

    2 filter tambahan biar gak gampang ketipu breakout palsu (modal kecil bisa
    "pompa" volume di saham tipis buat 1 hari doang):
    - close_position_pct: candle breakout HARUS nutup di bagian atas range
      harian-nya (bukan spike lalu didorong turun lagi / distribusi)
    - value_traded_idr: rupiah yang beneran ditransaksiin hari itu (price * volume)
      harus di atas ambang liquiditas kasar, biar gak gampang dimanipulasi modal kecil

    CATATAN: ini tetep heuristik murah dari data yfinance doang, BUKAN deteksi bandar/
    manipulasi beneran (butuh data broker summary asli, lihat Accumulation Score) —
    cuma ngurangin false positive paling gampang, bukan ngilangin risikonya total."""
    breakout_pct = (price_now - resistance_prior) / resistance_prior * 100 if resistance_prior else -100

    score = 0
    score += 10 if breakout_pct >= 0 else 5 if breakout_pct >= -3 else 0  # tembus, atau deket (dalem 3%)
    score += 6 if volume_ratio >= 3.0 else 4 if volume_ratio >= 2.0 else 2 if volume_ratio >= 1.5 else 0
    score += 2 if close_position_pct >= 0.7 else 1 if close_position_pct >= 0.4 else 0
    score += 2 if value_traded_idr >= 1_000_000_000 else 1 if value_traded_idr >= 300_000_000 else 0
    return score


def compute_score(ticker: str, volume_today: float, volume_avg20: float,
                   price_now: float, price_5d_ago: float, low_20d: float, high_20d: float,
                   resistance_prior: float, close_position_pct: float, value_traded_idr: float,
                   accum_lookup: dict | None = None) -> dict:
    vol = volume_score(volume_today, volume_avg20)
    price = price_score(price_now, price_5d_ago, low_20d, high_20d)
    accumulation = accumulation_score(ticker, accum_lookup)
    volume_ratio = volume_today / volume_avg20 if volume_avg20 else 0
    technical = technical_score(price_now, resistance_prior, volume_ratio, close_position_pct, value_traded_idr)

    total = vol + price + accumulation + technical
    return {
        "volume_score": vol,
        "price_score": price,
        "accumulation_score": accumulation,
        "technical_score": technical,
        "total_score": total,
        "signal": signal_label(total),
    }


def bsjp_criteria(price_now: float, price_prev: float, volume_today: float,
                   volume_avg20: float, ma5: float, value_traded_idr: float) -> bool:
    """BSJP (Beli Sore Jual Pagi) — screener checklist pass/fail, BUKAN score
    0-100 kayak yang lain (ini emang gaya "lolos syarat atau enggak", bukan
    dinilai). Kriteria dari komunitas screener saham Indonesia (Stockbit/DSI,
    formula publik) — harga breakout ≥5% dari kemarin DIKONFIRMASI volume 2x
    rata-rata, di atas MA5, minat institusi (value >Rp5M), bukan saham gocap.

    Ini Stage-1 PROXY murah doang (dihitung dari data harian EOD, jalan ke
    semua 951 ticker tiap refresh scanner_cache, dipake buat badge "BSJP" di
    Scanner) — BUKAN teknik BSJP asli mentor (spike sesi 2 vs sesi 1
    intraday). Konfirmasi asli dihitung Stage-2 di `intraday.py::session_takeoff`
    + `bsjp_intraday_score()` di bawah, cuma jalan ke pool kecil kandidat yang
    lolos proxy ini, sesaat sebelum alert Telegram (scheduler.py::_check_bsjp_screener)."""
    if not price_prev or not volume_avg20:
        return False
    return (
        price_now >= price_prev * 1.05
        and volume_today >= volume_avg20 * 2
        and price_now > ma5
        and value_traded_idr > 5_000_000_000
        and price_prev > 50
    )


def bsjp_intraday_score(takeoff: dict | None, value_traded_idr: float) -> float:
    """Stage-2 BSJP — skor RELATIF buat ranking kandidat (BUKAN pass/fail
    dengan angka multiplier baku — user eksplisit gak ada indikator resmi dari
    mentor buat BSJP, cuma "sahamnya oke, terbang di sesi 2"). 0.0 kalau gak
    ada takeoff (data kurang), sesi 2 gak naik, atau likuiditas di bawah
    ambang institusi (sama Rp5M kayak proxy Stage-1). Base skor = volume_ratio
    sesi 2. Sesi 1 yang JUGA spike itu bonus/pendukung ringan, bukan syarat."""
    if not takeoff or takeoff["price_change_pct"] <= 0:
        return 0.0
    if value_traded_idr < 5_000_000_000:
        return 0.0
    score = takeoff["volume_ratio"]
    if takeoff.get("s1_spike_supporting"):
        score *= 1.1
    return round(score, 3)


def bpjs_momentum_score(takeoff: dict | None, value_traded_idr: float) -> float:
    """BPJS (Day Trade) — reuse `intraday.py::session_takeoff` tapi buat SESI
    APAPUN yang lagi jalan (bukan cuma sesi 2 kayak BSJP), karena BPJS
    horizonnya "harapan lanjut ke hari berikutnya", bukan reaktif sore-ini-jual-
    besok-pagi kayak BSJP. Ambang likuiditas Rp3M (lebih rendah dari BSJP
    Rp5M — ponytail: tebakan, gak ada dasar resmi mentor soal ini, gampang
    di-tweak kalau ternyata kurang pas)."""
    if not takeoff or takeoff["price_change_pct"] <= 0:
        return 0.0
    if value_traded_idr < 3_000_000_000:
        return 0.0
    return round(takeoff["volume_ratio"], 3)


MIN_COMPRESSION_RR = 1.5  # sama standar kayak MIN_RR_RATIO di scheduler.py


def compression_setup(ma5: float, ma10: float, ma20: float, price_now: float,
                       sideways_days: int, rr_ratio: float) -> bool:
    """Setup Swing versi mentor user — BEDA ARAH dari breakout+volume
    (`technical_score`) yang nangkep momen "harga LAGI gerak". Ini nangkep
    momen SEBELUMNYA: saham yang MASIH SEPI/belum gerak, MA5/10/20 ngumpul
    rapat (compression), RR bagus, harga aman (>Rp100, dicek caller lewat
    scanner_universe). Prinsip mentor: "makin lama sideways makin kenceng
    lompatannya" — jadi durasi sideways ikut jadi syarat, bukan bonus doang.
    Dipake sebagai KONTEKS TAMBAHAN buat Groq milih breakout mana yang paling
    meyakinkan (breakout dari saham yang emang lama "ngumpul tenaga" lebih
    kuat daripada breakout dari saham yang udah lincah dari awal) — BUKAN
    gantiin breakout+volume sebagai trigger alert (lihat scheduler.py)."""
    ma_values = [ma5, ma10, ma20]
    if not price_now or min(ma_values) <= 0:
        return False
    ma_spread_pct = (max(ma_values) - min(ma_values)) / price_now * 100
    return (
        ma_spread_pct <= 3.0  # MA5/10/20 dalam 3% satu sama lain
        and sideways_days >= 10  # minimal ~2 minggu sideways sebelum breakout
        and rr_ratio >= MIN_COMPRESSION_RR
        and price_now > 100
    )


def volume_dry_up(hist_upto, sideways_days: int) -> bool:
    """Elemen VCP (Minervini) kedua yang ilang di compression_setup lama:
    volume harusnya MENGECIL selama fase sideways (bukan cuma harga yang
    diem). Bandingin rata-rata volume SELAMA base vs rata-rata volume 20
    hari SEBELUM base mulai. Riset VCP (lihat CLAUDE.md) + backtest.py
    ::_volume_dry_up konfirmasi versi lengkap ini (+ market_uptrend di
    bawah) ngalahin compression versi longgar di data real."""
    n = len(hist_upto)
    if sideways_days < 5 or n < sideways_days + 20:
        return False
    vol_during_base = hist_upto["Volume"].iloc[-sideways_days:].mean()
    vol_before_base = hist_upto["Volume"].iloc[-(sideways_days + 20):-sideways_days].mean()
    if not vol_before_base:
        return False
    return vol_during_base < vol_before_base


def is_market_uptrend(ihsg_hist) -> bool:
    """Elemen VCP ketiga: broad market (IHSG) HARUS lagi uptrend, proxy paling
    standar — close di atas MA50 sendiri. VCP terbukti (riset + backtest)
    gak ampuh kalau market lagi sideways/turun, walau setup individual
    sahamnya kelihatan bagus."""
    if len(ihsg_hist) < 50:
        return False
    ma50 = ihsg_hist["Close"].tail(50).mean()
    price_now = float(ihsg_hist["Close"].iloc[-1])
    return bool(ma50 and price_now > ma50)


def invest_criteria(per: float | None, pbv: float | None, dividend_yield: float | None,
                     market_cap: float | None) -> bool:
    """Investasi (hold panjang) — big cap + dividen konsisten + harga gak
    kemahalan. CATATAN: ini bukan angka baku dari 1 sumber tunggal, gue
    reconcile dari beberapa kriteria screener value-investing + catatan lama
    project ("big cap + dividend") — kalau ada acuan lebih spesifik (misal
    dari mentor), tinggal di-tweak angkanya di sini, 1 tempat doang.

    BUG ketemu 2026-09-01: pbv di-require non-None (nolak kalau kosong) tapi
    NILAINYA gak pernah dicek — saham PBV segila apapun bisa lolos selama
    PER/dividend/market_cap oke (kejadian nyata: PGAS PBV 13.290x dari data
    live, kemungkinan besar distorsi buku, tetep bisa lolos tanpa cap ini).
    Fix: tambah cap PBV <=3 — standar value-investing longgar (Graham klasik
    malah <=1.5), cukup buat nolak outlier ekstrim tapi masih ngasih ruang
    blue-chip IDX normal (BBRI/TLKM/ASII semua di bawah 2 dari data live)."""
    if per is None or pbv is None or dividend_yield is None or market_cap is None:
        return False
    return (
        market_cap >= 10_000_000_000_000  # Rp10 triliun ke atas
        and dividend_yield >= 3
        and 0 < per <= 25
        and 0 < pbv <= 3
    )


def signal_label(score: int) -> str:
    if score >= 75:
        return "Strong"
    if score >= 50:
        return "Moderate"
    if score >= 25:
        return "Weak"
    return "None"