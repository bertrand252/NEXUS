# Grup emiten dengan pengendali/tema yang sama — dicuratori manual oleh user,
# bukan hasil scraping/API (gak ada sumber data buat ini). Tambah baris baru
# di sini kalau nemu grup lain (contoh dari mentor: PD konsisten akumulasi
# MDKA+MBMA sejak crash, 1 pengendali/tema yang sama).
# ponytail: hardcoded dict, bukan JSON/tabel DB — <20 grup selamanya, gak
# butuh tooling/admin UI buat maintain segini dikit.
TICKER_GROUPS = {
    "Grup Merdeka": ["MDKA", "MBMA"],
}

GROUP_BY_TICKER = {t: g for g, tickers in TICKER_GROUPS.items() for t in tickers}
