"""
Thin wrapper around Invezgo API (https://api.invezgo.com) — mirror gaya groq_client.py.
Semua fungsi butuh INVEZGO_API_KEY di .env. Kalau key belum diisi, is_configured()
balikin False dan caller (scoring.py/scanner.py) fallback ke yfinance/mock — NEXUS
gak ikut down cuma karena belum subscribe.

CATATAN JUJUR: bentuk response di bawah ini based on contoh yang nempel di OpenAPI
spec resmi mereka (api.invezgo.com/openapi.json), BUKAN hasil tes lawan API asli
(belum ada API key aktif pas kode ini ditulis). Kemungkinan ada penyesuaian kecil
field/struktur begitu dites pertama kali pake key beneran.
"""
import httpx
from config import INVEZGO_API_KEY

BASE_URL = "https://api.invezgo.com"


def is_configured() -> bool:
    return bool(INVEZGO_API_KEY)


def _headers() -> dict:
    return {"Authorization": f"Bearer {INVEZGO_API_KEY}"}


def get_stock_list() -> list[dict]:
    """[{code, name, sector, logo}, ...] — semua ticker IDX, real-time."""
    res = httpx.get(f"{BASE_URL}/analysis/list/stock", headers=_headers(), timeout=30)
    res.raise_for_status()
    return res.json()


def get_top_accumulation(date: str) -> dict:
    """{"accum": [{code, name, price, change, value, volume, graph}], "dist": [...]}
    Top saham BDM flow (bandarmologi) di tanggal itu. date format: YYYY-MM-DD."""
    res = httpx.get(f"{BASE_URL}/analysis/top/accumulation", headers=_headers(),
                     params={"date": date}, timeout=30)
    res.raise_for_status()
    return res.json()


def get_top_foreign(date: str) -> dict:
    """Struktur sama kayak get_top_accumulation, buat foreign flow."""
    res = httpx.get(f"{BASE_URL}/analysis/top/foreign", headers=_headers(),
                     params={"date": date}, timeout=30)
    res.raise_for_status()
    return res.json()


def get_broker_summary(code: str, from_date: str, to_date: str, investor: str = "all", market: str = "RG") -> list[dict]:
    """[{code, buy_freq, buy_volume, buy_value, sell_freq, sell_volume, sell_value,
    buy_avg, sell_avg, net_value, net_volume, net_freq, name}, ...] — semua broker
    yang transaksi di saham ini dalam rentang from_date-to_date. FIX #2 (sesi
    sebelumnya kebalik-balik 2x): endpoint BENER itu /analysis/summary/stock/{code}
    (bukan /summary/broker/{code}, yang itu buat 1 BROKER lintas semua saham,
    parameter code-nya kode broker bukan kode saham) — diverifikasi lawan OpenAPI
    spec ASLI (api-1.json). from/to/investor/market SEMUA wajib diisi API mereka,
    versi sebelumnya sama sekali gak ngirim ini (bakal 422 kalau API udah aktif).
    CATATAN: field angka (buy_volume dkk) balik sebagai STRING dari API, bukan
    number — cast sendiri kalau mau itung/sort (contoh: routers/scanner.py)."""
    res = httpx.get(f"{BASE_URL}/analysis/summary/stock/{code}", headers=_headers(),
                     params={"from": from_date, "to": to_date, "investor": investor, "market": market}, timeout=30)
    res.raise_for_status()
    return res.json()


def get_broker_stalker(broker: str, stock: str, from_date: str, to_date: str,
                        investor: str = "all", market: str = "RG") -> dict:
    """{"broker", "stock", "summary": {"active","total","avg","peak"}, "calendar": [...]}
    — histori akumulasi 1 broker di 1 saham dari waktu ke waktu ("siapa numpuk
    barang"). from/to/investor/market wajib diisi (verifikasi lawan OpenAPI spec
    asli — versi sebelumnya gak ngirim sama sekali, bakal 422)."""
    res = httpx.get(f"{BASE_URL}/analysis/stalker/broker/{broker}/{stock}", headers=_headers(),
                     params={"from": from_date, "to": to_date, "investor": investor, "market": market}, timeout=30)
    res.raise_for_status()
    return res.json()


def get_shareholder_above(code: str, from_date: str, to_date: str, page: int = 1, limit: int = 10) -> dict:
    """{"totalPage","page","nextPage","data": [{date, code, name, format_securities,
    prev_val, prev_pct, next_val, next_pct, change, nationality}]} — perubahan
    kepemilikan >5% (institusi/investor besar, BEDA dari insider yang direksi/
    komisaris) dari laporan bursa. from_date/to_date format YYYY-MM-DD."""
    res = httpx.get(f"{BASE_URL}/analysis/shareholder-above", headers=_headers(),
                     params={"code": code, "from": from_date, "to": to_date, "page": page, "limit": limit}, timeout=30)
    res.raise_for_status()
    return res.json()


def get_notation_list() -> list[dict]:
    """[{code, date, list: [{notation, description}]}, ...] — SEMUA 951 saham
    sekaligus (1 call), notasi khusus (UMA/suspend/dst) dari BEI. Dipake buat
    filter keamanan (jangan alert saham yang lagi kena notasi bermasalah)."""
    res = httpx.get(f"{BASE_URL}/analysis/notation", headers=_headers(), timeout=30)
    res.raise_for_status()
    return res.json()


def get_insider_activity(code: str, from_date: str, to_date: str, page: int = 1, limit: int = 10) -> dict:
    """{"totalPage","page","nextPage","data": [{date, code, name, prev_percent,
    prev_val, next_percent, next_val, change, badge}]} — perubahan kepemilikan
    direksi/komisaris/pengendali. from_date/to_date format YYYY-MM-DD."""
    res = httpx.get(f"{BASE_URL}/analysis/shareholder-insider", headers=_headers(),
                     params={"code": code, "from": from_date, "to": to_date, "page": page, "limit": limit}, timeout=30)
    res.raise_for_status()
    return res.json()


def get_financial_statement(code: str, statement: str = "BS", period_type: str = "Q", limit: int = 8) -> dict:
    """Laporan keuangan (BS=Balance Sheet, IS=Income Statement, CF=Cash Flow dugaan
    enum-nya, belum diverifikasi lawan API asli). period_type: FY/Q/Q1-Q4."""
    res = httpx.get(f"{BASE_URL}/analysis/financial-statement/{code}", headers=_headers(),
                     params={"statement": statement, "type": period_type, "limit": limit}, timeout=30)
    res.raise_for_status()
    return res.json()


def get_price_seasonality(code: str, range_years: str = "3") -> dict:
    """[{month, start_price, end_price, percentage_change}, ...] — pola musiman
    historis harga per bulan. DIVERIFIKASI lawan OpenAPI spec asli (api-1.json)
    — field-nya PERSIS di atas, bukan dugaan lagi. range_years wajib diisi API
    mereka (versi sebelumnya gak ngirim sama sekali, bakal 422)."""
    res = httpx.get(f"{BASE_URL}/analysis/price-seasonality/{code}", headers=_headers(),
                     params={"range": range_years}, timeout=30)
    res.raise_for_status()
    return res.json()


def get_sankey_chart(code: str, date: str, chart_type: str = "value", buyer: str = "ALL",
                      seller: str = "ALL", market: str = "RG") -> dict:
    """{"nodes": [{"name"}], "links": [{"source","target","value"}]} — format
    standar D3 Sankey (arus dana antar broker). DIVERIFIKASI lawan OpenAPI spec
    asli. date/type/buyer/seller/market SEMUA wajib diisi API mereka (versi
    sebelumnya gak ngirim sama sekali, bakal 422). chart_type: value/volume."""
    res = httpx.get(f"{BASE_URL}/analysis/sankey-chart/{code}", headers=_headers(), params={
        "date": date, "type": chart_type, "buyer": buyer, "seller": seller, "market": market,
    }, timeout=30)
    res.raise_for_status()
    return res.json()


def get_inventory_chart_stock(code: str, from_date: str, to_date: str, scope: str = "val",
                               investor: str = "all", market: str = "ALL", limit: int = 20) -> dict:
    """{"price": [{code,date,open,high,low,close,volume}], "broker": [{"broker",
    "data": [{"date","value"}]}]} — akumulasi/distribusi broker per saham dari
    waktu ke waktu, chart-ready (BEDA dari get_broker_summary yang cuma snapshot
    1 rentang tanggal digabung jadi 1 angka — ini time series per hari, jauh
    lebih cocok buat "apakah smart money akumulasi saham ini belakangan" ketimbang
    Accumulation Score yang sekarang masih mock). scope: vol/val/freq."""
    res = httpx.get(f"{BASE_URL}/analysis/inventory-chart/stock/{code}", headers=_headers(), params={
        "from": from_date, "to": to_date, "scope": scope, "investor": investor, "market": market, "limit": limit,
    }, timeout=30)
    res.raise_for_status()
    return res.json()


def get_top_ritel(date: str) -> dict:
    """Struktur dugaan sama kayak get_top_accumulation (top mover versi retail)
    — belum diverifikasi lawan API asli."""
    res = httpx.get(f"{BASE_URL}/analysis/top/ritel", headers=_headers(), params={"date": date}, timeout=30)
    res.raise_for_status()
    return res.json()


def get_order_queue(code: str, price: float, side: str, page: int = 0, limit: int = 50) -> list[dict]:
    """[{time, order_id, order_volume, open_volume, done_volume, order_value,
    open_value, done_value}, ...] — antrian order di 1 level harga. side: BUY/SELL.
    Dipake buat deteksi order institusi gede yang ngantri."""
    res = httpx.get(f"{BASE_URL}/analysis/queue/{code}", headers=_headers(),
                     params={"price": price, "side": side, "page": page, "limit": limit}, timeout=30)
    res.raise_for_status()
    return res.json()


def get_running_trade(code: str, date: str, page: int = 1, limit: int = 50) -> dict:
    """{"totalPage","page","nextPage","data": [{board, time, price, volume, buyer,
    seller, buyer_dom, seller_dom, type, avg_price}]} — tape reading, transaksi
    per-trade. date format YYYY-MM-DD."""
    res = httpx.get(f"{BASE_URL}/analysis/running-trade/{code}", headers=_headers(),
                     params={"date": date, "page": page, "limit": limit}, timeout=30)
    res.raise_for_status()
    return res.json()


def get_price_table(code: str, date: str) -> list[dict]:
    """[{price, buy_volume, sell_volume, buy_freq, sell_freq}, ...] — Volume
    Profile, per level harga. date format YYYY-MM-DD."""
    res = httpx.get(f"{BASE_URL}/analysis/price-table/{code}", headers=_headers(), params={"date": date}, timeout=30)
    res.raise_for_status()
    return res.json()


def get_calendar(code: str | None = None, action_type: str | None = None, page: int = 1, limit: int = 20) -> dict:
    """{"totalPage","page","nextPage","data": [{code, type, payload: {...beda2
    per type}}]} — corporate action (IPO/RUPS/DIVIDEND/SPLIT/dst). WAJIB isi
    code ATAU action_type (gak boleh dua-duanya kosong, syarat API mereka).
    action_type enum: IPO/PUBLIC_EXPOSE/REVERSE/RIGHT/RUPS_RESULT/
    RUPS_SCHEDULE/SPLIT/WARRANT/BONUS/CONVERTION/DIVIDEND."""
    params = {"page": page, "limit": limit}
    if code:
        params["code"] = code
    if action_type:
        params["type"] = action_type
    res = httpx.get(f"{BASE_URL}/analysis/calendar", headers=_headers(), params=params, timeout=30)
    res.raise_for_status()
    return res.json()


def get_sector_rotation(from_date: str, to_date: str, base: str = "COMPOSITE", length: int = 10,
                         interval: str = "weekly", tail: int = 5) -> dict:
    """RRG (Relative Rotation Graph) — kuadran leading/weakening/lagging/
    improving. base=COMPOSITE buat rotasi antar sektor, base=index lain
    (IDX30/LQ45/dst) buat rotasi antar saham dalam index itu."""
    res = httpx.get(f"{BASE_URL}/analysis/sector/rotation", headers=_headers(), params={
        "from": from_date, "to": to_date, "base": base, "length": length, "interval": interval, "tail": tail,
    }, timeout=30)
    res.raise_for_status()
    return res.json()


def run_screener(formula: str) -> list[dict]:
    """[{code, matched, ...field lain sesuai formula}, ...] — screener custom
    pake formula string (contoh: "prev < close"). RATE LIMIT KETAT: 3 request
    per 15 menit (beda dari endpoint lain) — JANGAN dipanggil sering/otomatis,
    cuma buat query manual user."""
    res = httpx.post(f"{BASE_URL}/screener/screen", headers=_headers(), json={"formula": formula}, timeout=30)
    res.raise_for_status()
    return res.json()


