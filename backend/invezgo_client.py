"""
Thin wrapper around Invezgo API (https://api.invezgo.com) — mirror gaya groq_client.py.
Semua fungsi butuh INVEZGO_API_KEY di .env. Kalau key belum diisi, is_configured()
balikin False dan caller (scoring.py/scanner.py) fallback ke yfinance/mock — NEXUS
gak ikut down cuma karena belum subscribe.

STATUS: API key AKTIF (2026-09-01). Fungsi paling sering dipanggil (running_trade,
broker_summary, inventory_chart_stock, sankey_chart, order_queue, price_table,
get_calendar, get_financial_statement, get_sector_rotation, get_top_ritel) UDAH
dites lawan API asli. Sisanya masih based on contoh OpenAPI spec, kemungkinan
ada penyesuaian kecil kalau ternyata field-nya meleset (kejadian 2x sesi ini:
financial_statement & sector_rotation DUA-DUANYA ditandain "confirmed lawan
spec" sebelumnya tapi shape ASLI beda — jangan percaya spec doang).

BUDGET HARIAN (2026-09-01): Invezgo dashboard mereka gak punya fitur limit
harian sendiri (cuma hard monthly quota 30.000, keliatan di dashboard mereka),
jadi ini batasan SENDIRI dari NEXUS — in-memory, ke-reset tiap restart backend
(bukan hard guarantee across restart, tapi cukup buat nangkep bug boros kuota
kayak yang kejadian sesi ini sebelum keburu ngabisin jatah bulanan). Kalau
limit kelewat, _check_budget() raise Exception — SEMUA caller (scheduler.py/
routers) udah wrap tiap panggilan Invezgo dalam try/except & fail-soft (skip
field itu doang), jadi otomatis kepake tanpa perlu ubah kode caller.
"""
import datetime
import httpx
from config import INVEZGO_API_KEY
from logger import get_logger

log = get_logger("invezgo_client")

BASE_URL = "https://api.invezgo.com"
DAILY_BUDGET = 1000

_usage = {"date": None, "count": 0}


class InvezgoBudgetExceeded(Exception):
    pass


def is_configured() -> bool:
    return bool(INVEZGO_API_KEY)


def _headers() -> dict:
    return {"Authorization": f"Bearer {INVEZGO_API_KEY}"}


def _check_budget() -> None:
    today = datetime.date.today().isoformat()
    if _usage["date"] != today:
        _usage["date"] = today
        _usage["count"] = 0
    if _usage["count"] >= DAILY_BUDGET:
        if _usage["count"] == DAILY_BUDGET:  # log sekali doang pas nyentuh limit, jangan spam tiap panggilan abis itu
            log.warning(f"Budget harian Invezgo ({DAILY_BUDGET}) abis buat {today} — panggilan berikutnya di-skip sampe besok")
            _usage["count"] += 1
        raise InvezgoBudgetExceeded(f"Budget harian Invezgo ({DAILY_BUDGET}) abis buat {today}")
    _usage["count"] += 1


def _get(path: str, params: dict | None = None) -> dict | list:
    _check_budget()
    res = httpx.get(f"{BASE_URL}{path}", headers=_headers(), params=params, timeout=30)
    res.raise_for_status()
    return res.json()


def _post(path: str, json: dict | None = None) -> dict | list:
    _check_budget()
    res = httpx.post(f"{BASE_URL}{path}", headers=_headers(), json=json, timeout=30)
    res.raise_for_status()
    return res.json()


def get_stock_list() -> list[dict]:
    """[{code, name, sector, logo}, ...] — semua ticker IDX, real-time."""
    return _get("/analysis/list/stock")


def get_top_accumulation(date: str) -> dict:
    """{"accum": [{code, name, price, change, value, volume, graph}], "dist": [...]}
    Top saham BDM flow (bandarmologi) di tanggal itu. date format: YYYY-MM-DD."""
    return _get("/analysis/top/accumulation", {"date": date})


def get_top_foreign(date: str) -> dict:
    """Struktur sama kayak get_top_accumulation, buat foreign flow."""
    return _get("/analysis/top/foreign", {"date": date})


def get_broker_summary(code: str, from_date: str, to_date: str, investor: str = "all", market: str = "RG") -> list[dict]:
    """[{code, buy_freq, buy_volume, buy_value, sell_freq, sell_volume, sell_value,
    buy_avg, sell_avg, net_value, net_volume, net_freq, name}, ...] — semua broker
    yang transaksi di saham ini dalam rentang from_date-to_date. DIVERIFIKASI lawan
    API asli. CATATAN: field angka (buy_volume dkk) balik sebagai STRING dari API,
    bukan number — cast sendiri kalau mau itung/sort (contoh: routers/scanner.py)."""
    return _get(f"/analysis/summary/stock/{code}", {"from": from_date, "to": to_date, "investor": investor, "market": market})


def get_broker_stalker(broker: str, stock: str, from_date: str, to_date: str,
                        investor: str = "all", market: str = "RG") -> dict:
    """{"broker", "stock", "summary": {"active","total","avg","peak"}, "calendar": [...]}
    — histori akumulasi 1 broker di 1 saham dari waktu ke waktu ("siapa numpuk barang")."""
    return _get(f"/analysis/stalker/broker/{broker}/{stock}", {"from": from_date, "to": to_date, "investor": investor, "market": market})


def get_shareholder_above(code: str, from_date: str, to_date: str, page: int = 1, limit: int = 10) -> dict:
    """{"totalPage","page","nextPage","data": [{date, code, name, format_securities,
    prev_val, prev_pct, next_val, next_pct, change, nationality}]} — perubahan
    kepemilikan >5% (institusi/investor besar, BEDA dari insider yang direksi/
    komisaris) dari laporan bursa. from_date/to_date format YYYY-MM-DD."""
    return _get("/analysis/shareholder-above", {"code": code, "from": from_date, "to": to_date, "page": page, "limit": limit})


def get_notation_list() -> list[dict]:
    """[{code, date, list: [{notation, description}]}, ...] — SEMUA 951 saham
    sekaligus (1 call), notasi khusus (UMA/suspend/dst) dari BEI. Dipake buat
    filter keamanan (jangan alert saham yang lagi kena notasi bermasalah)."""
    return _get("/analysis/notation")


def get_insider_activity(code: str, from_date: str, to_date: str, page: int = 1, limit: int = 10) -> dict:
    """{"totalPage","page","nextPage","data": [{date, code, name, prev_percent,
    prev_val, next_percent, next_val, change, badge}]} — perubahan kepemilikan
    direksi/komisaris/pengendali. from_date/to_date format YYYY-MM-DD."""
    return _get("/analysis/shareholder-insider", {"code": code, "from": from_date, "to": to_date, "page": page, "limit": limit})


def get_financial_statement(code: str, statement: str = "BS", period_type: str = "Q", limit: int = 8) -> dict:
    """Shape CONFIRMED lawan API asli (2026-09-01): {"rows": [{id, name, level,
    values: [{col, year, amount, period}], parent_id, is_abstract, display_order}]}
    — pivot table, baris = akun, values = tiap periode (col = "Q2 2026" dst).
    statement: BS/IS/CF. period_type: FY/Q/Q1-Q4."""
    return _get(f"/analysis/financial-statement/{code}", {"statement": statement, "type": period_type, "limit": limit})


def get_price_seasonality(code: str, range_years: str = "3") -> dict:
    """[{month, start_price, end_price, percentage_change}, ...] — pola musiman
    historis harga per bulan. DIVERIFIKASI lawan OpenAPI spec asli."""
    return _get(f"/analysis/price-seasonality/{code}", {"range": range_years})


def get_sankey_chart(code: str, date: str, chart_type: str = "value", buyer: str = "ALL",
                      seller: str = "ALL", market: str = "RG") -> dict:
    """{"nodes": [{"name"}], "links": [{"source","target","value"}]} — format
    standar D3 Sankey (arus dana antar broker). DIVERIFIKASI lawan API asli —
    nama broker kadang di-anonymize API-nya sendiri (" -- "), bukan bug kita.
    chart_type: value/volume."""
    return _get(f"/analysis/sankey-chart/{code}", {"date": date, "type": chart_type, "buyer": buyer, "seller": seller, "market": market})


def get_inventory_chart_stock(code: str, from_date: str, to_date: str, scope: str = "val",
                               investor: str = "all", market: str = "ALL", limit: int = 20) -> dict:
    """{"price": [{code,date,open,high,low,close,volume}], "broker": [{"broker",
    "data": [{"date","value"}]}]} — akumulasi/distribusi broker per saham dari
    waktu ke waktu, chart-ready (BEDA dari get_broker_summary yang cuma snapshot
    1 rentang tanggal digabung jadi 1 angka). scope: vol/val/freq. DIVERIFIKASI
    lawan API asli."""
    return _get(f"/analysis/inventory-chart/stock/{code}", {"from": from_date, "to": to_date, "scope": scope, "investor": investor, "market": market, "limit": limit})


def get_top_ritel(date: str) -> dict:
    """{"accum": [{code,name,price,change,value,volume,calculated_value,graph}],
    "dist": [...]} — top mover versi retail. DIVERIFIKASI lawan API asli — `change`
    UDAH dalam persen (BUKAN fraction dikali 100)."""
    return _get("/analysis/top/ritel", {"date": date})


def get_order_queue(code: str, price: float, side: str, page: int = 0, limit: int = 50) -> list[dict]:
    """[{time, order_id, order_volume, open_volume, done_volume, order_value,
    open_value, done_value}, ...] — antrian order di 1 level harga. side: BUY/SELL.
    Dipake buat deteksi order institusi gede yang ngantri. DIVERIFIKASI lawan API
    asli (balikin [] kalau gak ada antrian di harga itu, bukan error)."""
    return _get(f"/analysis/queue/{code}", {"price": price, "side": side, "page": page, "limit": limit})


def get_running_trade(code: str, date: str, page: int = 1, limit: int = 50) -> dict:
    """{"totalPage","page","nextPage","data": [{board, time, price, volume, buyer,
    seller, buyer_dom, seller_dom, type, avg_price}]} — tape reading, transaksi
    per-trade. date format YYYY-MM-DD. DIVERIFIKASI lawan API asli."""
    return _get(f"/analysis/running-trade/{code}", {"date": date, "page": page, "limit": limit})


def get_price_table(code: str, date: str) -> list[dict]:
    """[{price, buy_volume, sell_volume, buy_freq, sell_freq}, ...] — Volume
    Profile, per level harga. date format YYYY-MM-DD. DIVERIFIKASI lawan API asli."""
    return _get(f"/analysis/price-table/{code}", {"date": date})


def get_calendar(code: str | None = None, action_type: str | None = None, page: int = 1, limit: int = 20) -> dict:
    """{"totalPage","page","nextPage","data": [{code, type, payload}]} — corporate
    action. payload BEDA STRUKTUR per type, DIVERIFIKASI lawan API asli:
    RUPS_SCHEDULE {Date,Venue,Remark,Result,DateStr,RecDate,TimeStr},
    DIVIDEND {ExDate,Status,CumDate,RecDate,DistDate,PaymentType,TotalDividen,
    DividenPerShare}. WAJIB isi code ATAU action_type (gak boleh dua-duanya
    kosong, syarat API mereka). action_type enum: IPO/PUBLIC_EXPOSE/REVERSE/
    RIGHT/RUPS_RESULT/RUPS_SCHEDULE/SPLIT/WARRANT/BONUS/CONVERTION/DIVIDEND."""
    params = {"page": page, "limit": limit}
    if code:
        params["code"] = code
    if action_type:
        params["type"] = action_type
    return _get("/analysis/calendar", params)


def get_sector_rotation(from_date: str, to_date: str, base: str = "COMPOSITE", length: int = 10,
                         interval: str = "weekly", tail: int = 5) -> dict:
    """Shape CONFIRMED lawan API asli (2026-09-01): {"benchmark","lastDate",
    "data": [{code, name, trail: [{date, x, y}], quadrant}]} — RRG (Relative
    Rotation Graph), x/y itu TIME SERIES per sektor (`trail`, mingguan), posisi
    SEKARANG = titik terakhir. `quadrant` (leading/weakening/lagging/improving)
    UDAH diklasifikasiin API-nya sendiri, gak perlu dihitung manual. base=COMPOSITE
    buat rotasi antar sektor, base=index lain (IDX30/LQ45/dst) buat rotasi antar
    saham dalam index itu."""
    return _get("/analysis/sector/rotation", {"from": from_date, "to": to_date, "base": base, "length": length, "interval": interval, "tail": tail})


def run_screener(formula: str) -> list[dict]:
    """[{code, matched, ...field lain sesuai formula}, ...] — screener custom
    pake formula string (contoh: "prev < close"). RATE LIMIT KETAT: 3 request
    per 15 menit (beda dari endpoint lain) — JANGAN dipanggil sering/otomatis,
    cuma buat query manual user."""
    return _post("/screener/screen", {"formula": formula})
