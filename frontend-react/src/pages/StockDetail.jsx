import { useEffect, useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { API_BASE } from '../lib/api';
import { signalMeta, zoneLabel, zoneColorClass } from '../lib/signal';
import { useCandlestickChart } from '../hooks/useCandlestickChart';

// Shape CONFIRMED lawan API asli (2026-09-01): {"rows":[{id, name, level,
// values:[{col, year, amount, period}], is_abstract}]} — pivot table (baris =
// akun, kolom = quarter). Kolom diambil dari urutan `col` yang muncul di data
// (API udah ngurutin terbaru dulu), bukan diasumsiin nama field flat lagi.
function GenericTable({ raw }) {
  const rows = Array.isArray(raw?.rows) ? raw.rows : null;
  if (rows && !rows.length) return <p className="text-slate-500 text-[11px]">Belum ada data.</p>;
  if (!rows) {
    return <pre className="text-[10px] text-slate-400 bg-black/30 rounded-lg p-3 overflow-auto max-h-40 font-mono">{JSON.stringify(raw, null, 2)}</pre>;
  }
  const cols = [];
  for (const row of rows) {
    for (const v of row.values || []) {
      if (!cols.includes(v.col)) cols.push(v.col);
    }
  }
  return (
    <div className="overflow-x-auto">
      <table className="text-[11px] text-slate-300 w-full">
        <thead>
          <tr className="text-slate-500 uppercase text-[9px]">
            <th className="text-left pr-3 pb-1 font-semibold">Akun</th>
            {cols.map((c) => <th key={c} className="text-right pr-3 pb-1 font-semibold whitespace-nowrap">{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 40).map((r) => (
            <tr key={r.id} className={`border-t border-border/50 ${r.is_abstract ? 'font-semibold text-slate-200' : ''}`}>
              <td className="pr-3 py-1 whitespace-nowrap" style={{ paddingLeft: `${(r.level || 0) * 12}px` }}>{r.name}</td>
              {cols.map((c) => {
                const v = (r.values || []).find((x) => x.col === c);
                return <td key={c} className="pr-3 py-1 text-right font-mono">{v?.amount != null ? Number(v.amount).toLocaleString('id-ID') : '—'}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const STATEMENT_TABS = { BS: 'Neraca', IS: 'Laba Rugi', CF: 'Arus Kas' };

// Nama baris "Pendapatan"/"Laba Bersih" itu STANDAR terminologi PSAK/IDX (bukan
// dugaan sembarang) — dicoba berurutan, ambil match PERTAMA. Nama baris ASLI
// tetep ditampilin apa adanya di bawah angka (bukan di-rename "Net Profit" dkk)
// biar keliatan jujur kalau ternyata match-nya kurang pas buat emiten tertentu.
const KEY_ROW_PATTERNS = {
  revenue: ['pendapatan usaha', 'penjualan dan pendapatan'],
  netProfit: ['diatribusikan ke entitas induk', 'jumlah laba (rugi)'],
};

function findKeyRow(rows, patterns) {
  for (const p of patterns) {
    const found = rows.find((r) => r.name.toLowerCase().includes(p) && !r.name.toLowerCase().includes('komprehensif'));
    if (found) return found;
  }
  return null;
}

// values[] API-nya udah urut TERBARU DULU — values[0]=quarter ini, values[1]=QoQ.
// YoY: cari quarter SAMA (period) tapi tahun-1.
function qoqYoy(row) {
  if (!row?.values?.length) return null;
  const latest = row.values[0];
  const prior = row.values[1];
  const yoy = row.values.find((v) => v.period === latest.period && v.year === latest.year - 1);
  const pct = (a, b) => (b ? ((a - b) / Math.abs(b)) * 100 : null);
  return {
    label: row.name, col: latest.col, amount: latest.amount,
    qoqPct: prior ? pct(latest.amount, prior.amount) : null,
    yoyPct: yoy ? pct(latest.amount, yoy.amount) : null,
  };
}

function DeltaBadge({ pct, label }) {
  if (pct == null) return null;
  return <span className={pct >= 0 ? 'text-emerald-400' : 'text-red-400'}>{label} {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%</span>;
}

// Angka laporan keuangan bisa belasan digit (Rp62.714.280.000.000) — gak
// kebaca sekali liat. Singkat pake K/M/B (Thousand/Million/Billion) — user
// minta satuan Inggris, bukan T/M/Jt Indonesia.
function fmtRupiahCompact(v) {
  const n = Number(v);
  if (isNaN(n)) return '—';
  const abs = Math.abs(n);
  if (abs >= 1e9) return `Rp${(n / 1e9).toLocaleString('id-ID', { maximumFractionDigits: 2 })} B`;
  if (abs >= 1e6) return `Rp${(n / 1e6).toLocaleString('id-ID', { maximumFractionDigits: 2 })} M`;
  if (abs >= 1e3) return `Rp${(n / 1e3).toLocaleString('id-ID', { maximumFractionDigits: 2 })} K`;
  return `Rp${n.toLocaleString('id-ID')}`;
}

function FinancialMetric({ m, title }) {
  if (!m) return <p className="text-slate-500 text-[11px]">{title}: gak nemu baris standar di data ini.</p>;
  return (
    <div>
      <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">{title} ({m.col})</p>
      <p className="text-lg font-mono font-bold text-white">{fmtRupiahCompact(m.amount)}</p>
      <div className="flex gap-3 mt-1 text-[11px]">
        <DeltaBadge pct={m.qoqPct} label="QoQ" />
        <DeltaBadge pct={m.yoyPct} label="YoY" />
      </div>
      <p className="text-[9px] text-slate-600 mt-1">{m.label}</p>
    </div>
  );
}

// Dulu langsung dump tabel pivot akun×quarter penuh (40 baris) — "pusing
// bacanya" (feedback user, contoh dikasih format ringkas ala laporan sekuritas).
// Sekarang: kesimpulan (Pendapatan + Laba Bersih, QoQ/YoY) di depan, tabel
// pivot lengkap disembunyiin di balik toggle buat yang mau detail per-akun.
function FinancialStatementTable({ data }) {
  const [tab, setTab] = useState('BS');
  const [showDetail, setShowDetail] = useState(false);
  const raw = data?.[tab];

  const isRows = data?.IS?.rows;
  const revenue = isRows?.length ? qoqYoy(findKeyRow(isRows, KEY_ROW_PATTERNS.revenue)) : null;
  const netProfit = isRows?.length ? qoqYoy(findKeyRow(isRows, KEY_ROW_PATTERNS.netProfit)) : null;

  return (
    <div>
      {isRows?.length ? (
        <div className="grid grid-cols-2 gap-4 mb-3 pb-3 border-b border-border">
          <FinancialMetric m={revenue} title="Pendapatan" />
          <FinancialMetric m={netProfit} title="Laba Bersih" />
        </div>
      ) : (
        <p className="text-xs text-slate-500 mb-3">Belum ada data Laba Rugi buat kesimpulan.</p>
      )}
      <button onClick={() => setShowDetail((v) => !v)} className="text-[10px] font-semibold text-accent hover:text-white transition mb-2">
        {showDetail ? '▾ Sembunyiin detail lengkap' : '▸ Lihat detail lengkap per-akun'}
      </button>
      {showDetail && (
        <div>
          <div className="flex items-center gap-1 mb-2">
            {Object.entries(STATEMENT_TABS).map(([key, label]) => (
              <button key={key} onClick={() => setTab(key)}
                className={`text-[10px] font-semibold px-2 py-1 rounded-lg border transition ${tab === key ? 'bg-accent/10 text-accent border-accent/30' : 'bg-white/5 text-slate-500 border-border hover:text-white'}`}>
                {label}
              </button>
            ))}
          </div>
          {raw ? <GenericTable raw={raw} /> : <p className="text-xs text-slate-500">Gak ada data {STATEMENT_TABS[tab]} buat ticker ini.</p>}
        </div>
      )}
    </div>
  );
}

function CompanyInfo({ company, ticker, financialStatement, notation }) {
  const [lang, setLang] = useState('en');
  const [translated, setTranslated] = useState(null);
  const [loading, setLoading] = useState(false);

  async function toggleLang() {
    if (lang === 'en') {
      if (!translated && company.summary) {
        setLoading(true);
        try {
          const res = await fetch(`${API_BASE}/scanner/translate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: company.summary }),
          });
          const { translated } = await res.json();
          setTranslated(translated);
        } catch {
          alert('Gagal translate, coba lagi.');
          setLoading(false);
          return;
        }
        setLoading(false);
      }
      setLang('id');
    } else {
      setLang('en');
    }
  }

  return (
    <div className="glow-border rounded-2xl bg-card border border-border p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-bold text-white tracking-tight">{company.name || ticker}</h3>
          {notation?.list?.length > 0 && (
            <span title="Notasi khusus BEI (UMA/suspend/dst) — sinyal hati-hati sebelum masuk/nambah posisi" className="text-[10px] font-bold text-strong bg-strong/10 border border-strong/30 rounded-full px-2 py-0.5 cursor-help">
              ⚠ {notation.list.map((n) => n.notation).join(', ')}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {company.website && (
            <a href={company.website} target="_blank" rel="noreferrer" className="text-[11px] text-cyan hover:text-accent font-medium">{company.website.replace(/^https?:\/\//, '')} ↗</a>
          )}
          {company.summary && (
            <button
              onClick={toggleLang} disabled={loading} title="Terjemahkan ke Bahasa Indonesia"
              className="flex items-center gap-1 text-[11px] font-semibold px-2 py-1 rounded-lg bg-white/5 text-slate-400 border border-border hover:border-accent/50 hover:text-white transition disabled:opacity-50"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" /><path d="M3 12H21M12 3C14.5 5.7 15.8 9 15.8 12C15.8 15 14.5 18.3 12 21C9.5 18.3 8.2 15 8.2 12C8.2 9 9.5 5.7 12 3Z" stroke="currentColor" strokeWidth="1.8" /></svg>
              {loading ? '...' : lang === 'en' ? 'ID' : 'EN'}
            </button>
          )}
        </div>
      </div>
      <div className="flex items-center gap-2 mb-3">
        {company.sector && <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-accent/10 text-accent border border-accent/30">{company.sector}</span>}
        {company.industry && <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-white/5 text-slate-400 border border-border">{company.industry}</span>}
        {company.employees && <span className="text-[10px] text-slate-500 font-mono">{company.employees.toLocaleString('id-ID')} karyawan</span>}
      </div>
      {company.summary && <p className="text-sm text-slate-400 leading-relaxed text-justify">{lang === 'id' && translated ? translated : company.summary}</p>}
      <div className="mt-3 pt-3 border-t border-border">
        <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Laporan Keuangan (Invezgo, per quarter)</p>
        {financialStatement ? (
          <FinancialStatementTable data={financialStatement} />
        ) : (
          <p className="text-xs text-slate-500">Memuat...</p>
        )}
      </div>
    </div>
  );
}

function fmtRupiah(v) {
  return v == null ? '—' : `Rp${v.toLocaleString('id-ID')}`;
}
function fmtPct(v) {
  return v == null ? '—' : `${v >= 0 ? '+' : ''}${v}%`;
}

function MentorCallCard({ call }) {
  return (
    <div className="glow-border rounded-2xl bg-card border border-cyan/30 p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-bold text-white tracking-tight">Mentor Call</h3>
        <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-cyan/10 text-cyan border border-cyan/30">{call.status || '—'}</span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4 text-xs">
        <div><p className="text-slate-500 mb-1">Recom Date</p><p className="font-mono text-slate-200">{call.recom_date || '—'}</p></div>
        <div><p className="text-slate-500 mb-1">Buy Price</p><p className="font-mono text-slate-200">{fmtRupiah(call.buy_price)}</p></div>
        <div><p className="text-slate-500 mb-1">Current Price</p><p className="font-mono text-slate-200">{fmtRupiah(call.current_price)}</p></div>
        <div><p className="text-slate-500 mb-1">TP1 / TP2</p><p className="font-mono text-slate-200">{fmtRupiah(call.tp1)} / {fmtRupiah(call.tp2)}</p></div>
        <div><p className="text-slate-500 mb-1">Cut Loss</p><p className="font-mono text-slate-200">{fmtRupiah(call.cl)}</p></div>
        <div><p className="text-slate-500 mb-1">Floating PnL</p><p className={`font-mono font-semibold ${call.floating_pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{fmtPct(call.floating_pnl_pct)}</p></div>
      </div>
    </div>
  );
}

const GAYA_TRADING = [
  { key: 'invest', label: 'Investasi', hint: 'Big cap + dividen konsisten', cls: 'bg-risklow/10 text-risklow border-risklow/30' },
  { key: 'swing', label: 'Swing', hint: 'Breakout + volume, RR bagus, dipegang mingguan', cls: 'bg-accent/10 text-accent border-accent/30' },
  { key: 'bsjp', label: 'BSJP', hint: 'Lolos proxy screener + terkonfirmasi "terbang" di sesi 2', cls: 'bg-moderate/10 text-moderate border-moderate/30' },
  { key: 'bpjs', label: 'BPJS', hint: 'Ada call Day Trade aktif (masih waiting_entry/open)', cls: 'bg-cyan/10 text-cyan border-cyan/30' },
];

function GayaTradingCard({ data }) {
  const cocok = {
    invest: !!data.cocok_invest,
    swing: data.signal === 'Strong' || data.signal === 'Moderate',
    bsjp: !!data.cocok_bsjp,
    bpjs: !!data.bpjs_last_alert && ['waiting_entry', 'open'].includes(data.bpjs_last_alert.status),
  };
  return (
    <div className="glow-border rounded-2xl bg-card border border-border p-5">
      <h3 className="text-sm font-bold text-white tracking-tight mb-3">Gaya Trading</h3>
      <div className="flex items-center gap-2 flex-wrap">
        {GAYA_TRADING.map((g) => (
          <span
            key={g.key} title={g.hint}
            className={`text-[11px] font-sans font-semibold px-2.5 py-1 rounded-full border ${cocok[g.key] ? g.cls : 'bg-white/[0.02] text-slate-600 border-border'}`}
          >
            {cocok[g.key] ? '✓' : '—'} {g.label}
          </span>
        ))}
      </div>
      {cocok.bpjs && (
        <p className="text-xs text-slate-400 mt-3">
          Call BPJS aktif: entry Rp{data.bpjs_last_alert.entry_price?.toLocaleString('id-ID')},
          target Rp{data.bpjs_last_alert.target?.toLocaleString('id-ID')},
          SL Rp{data.bpjs_last_alert.stop_loss?.toLocaleString('id-ID')} ({data.bpjs_last_alert.status})
        </p>
      )}
      {data.conviction && (
        <div className="mt-4 pt-4 border-t border-border">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-xs font-bold text-white">Conviction Score</h4>
            <span className="text-xs font-mono text-slate-400">{data.conviction.score}/{data.conviction.max} sinyal sepakat</span>
          </div>
          {data.conviction.factors.length > 0 ? (
            <ul className="space-y-1 text-xs text-slate-400">
              {data.conviction.factors.map((f, i) => <li key={i}>✓ {f}</li>)}
            </ul>
          ) : (
            <p className="text-xs text-slate-500">Belum ada sinyal yang sepakat buat saham ini saat ini.</p>
          )}
          <p className="text-[10px] text-slate-600 mt-2">Jumlah sinyal independen yang searah — bukan skor berbobot, pertimbangan tambahan bukan jaminan.</p>
        </div>
      )}
    </div>
  );
}

function ScoreCard({ label, value, max, barClass }) {
  return (
    <div className="glow-border rounded-2xl bg-card border border-border p-4">
      <p className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold mb-2">{label}</p>
      <p className="text-2xl font-extrabold text-white font-mono">{value ?? '--'}<span className="text-xs text-slate-500 font-medium">/{max}</span></p>
      <div className="w-full h-1.5 rounded-full bg-white/5 mt-2 overflow-hidden">
        <div className={`h-full rounded-full ${barClass}`} style={{ width: value != null ? `${(value / max) * 100}%` : '0%' }}></div>
      </div>
    </div>
  );
}

// Batas histori MAKSIMAL yang Invezgo simpen itu 2 tahun (dikonfirmasi owner,
// lihat CLAUDE.md) — dari IPO cuma kepake beneran kalau IPO-nya <= 2 tahun lalu.
const BROKER_FLOW_MIN_DATE = new Date(Date.now() - 730 * 86400000).toISOString().slice(0, 10);
const BROKER_FLOW_MAX_DATE = new Date().toISOString().slice(0, 10);

// Dulu bar chart net_value — user minta angka+badge doang (contoh: NeoBDM),
// gak usah chart. Sekarang juga tampilin avg harga (buy_avg buat yang net
// buy, sell_avg buat yang net sell) — user minta "avg buy avg sell selama
// range" (gantiin rencana Inventory Chart candlestick+overlay yang user
// bilang "ribet, kebanyakan API" — versi ini cukup 1 endpoint, get_broker_summary,
// range-nya dari `days` di StockDetail). net_value/buy_avg/sell_avg SEMUA
// balik STRING dari API, WAJIB Number() dulu.
const AKUM_PERIODS = [
  { key: 7, label: '1 Minggu' },
  { key: 14, label: '2 Minggu' },
  { key: 30, label: '1 Bulan' },
  { key: 365, label: '1 Tahun' },
];

function BrokerSummaryTable({ brokers, topAkumulator, akumPeriod, onAkumPeriodChange }) {
  const withNet = brokers.map((b) => ({ ...b, netNum: Number(b.net_value), lotNum: Number(b.net_volume) / 100 }));
  const buyers = withNet.filter((b) => b.netNum > 0).sort((a, b) => b.netNum - a.netNum).slice(0, 8);
  const sellers = withNet.filter((b) => b.netNum < 0).sort((a, b) => a.netNum - b.netNum).slice(0, 8);

  const Table = ({ rows, positive }) => (
    <table className="w-full text-[11px]">
      <thead>
        <tr className="text-slate-500 text-[9px] uppercase tracking-wider">
          <th className="text-left font-semibold pb-1">Broker</th>
          <th className="text-right font-semibold pb-1">Nlot</th>
          <th className="text-right font-semibold pb-1">Nval</th>
          <th className="text-right font-semibold pb-1">Bavg</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((b) => {
          const rawAvg = positive ? b.buy_avg : b.sell_avg;
          const avg = rawAvg != null ? Number(rawAvg) : null;
          return (
            <tr key={b.code} className="border-b border-border/30 last:border-0">
              <td title={b.name} className={`py-1 font-mono font-bold cursor-help ${positive ? 'text-emerald-400' : 'text-red-400'}`}>{b.code}</td>
              <td className="text-right text-slate-400 font-mono">{Math.abs(b.lotNum).toLocaleString('id-ID')}</td>
              <td className={`text-right font-mono font-semibold ${positive ? 'text-emerald-400' : 'text-red-400'}`}>{fmtRupiahCompact(Math.abs(b.netNum))}</td>
              <td className="text-right text-slate-500 font-mono">{avg != null && !isNaN(avg) ? Math.round(avg).toLocaleString('id-ID') : '—'}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );

  return (
    <div>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <p className="text-[10px] text-emerald-400 uppercase tracking-wider mb-1 font-semibold">Net Buy</p>
          {buyers.length ? <Table rows={buyers} positive /> : <p className="text-slate-500 text-xs">Gak ada.</p>}
        </div>
        <div>
          <p className="text-[10px] text-red-400 uppercase tracking-wider mb-1 font-semibold">Net Sell</p>
          {sellers.length ? <Table rows={sellers} positive={false} /> : <p className="text-slate-500 text-xs">Gak ada.</p>}
        </div>
      </div>
      <div className="mt-3 pt-3 border-t border-border/30">
        <div className="flex items-center justify-between flex-wrap gap-2 mb-1.5">
          <p className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold">Top Akumulator</p>
          <div className="flex items-center gap-1">
            {AKUM_PERIODS.map((p) => (
              <button key={p.key} onClick={() => onAkumPeriodChange(p.key)}
                className={`text-[10px] font-semibold px-2 py-1 rounded-lg border transition ${akumPeriod === p.key ? 'bg-accent/10 text-accent border-accent/30' : 'bg-white/5 text-slate-500 border-border hover:text-white'}`}>
                {p.label}
              </button>
            ))}
          </div>
        </div>
        {topAkumulator ? (
          <p className="text-[11px] text-slate-400">
            <span className="font-mono font-bold text-white">{topAkumulator.broker}</span> — {topAkumulator.active} hari aktif, total {fmtRupiahCompact(topAkumulator.total)}
          </p>
        ) : <p className="text-[11px] text-slate-500">Belum ada data histori broker.</p>}
      </div>
    </div>
  );
}

export default function StockDetail() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const ticker = (params.get('t') || 'ANTM').toUpperCase();

  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [searchInput, setSearchInput] = useState('');
  const [annotation, setAnnotation] = useState(null);
  const [annotating, setAnnotating] = useState(false);

  async function runAnnotate() {
    setAnnotating(true);
    try {
      const res = await fetch(`${API_BASE}/scanner/${ticker}/annotate`, { method: 'POST' });
      if (!res.ok) throw new Error((await res.json()).detail || `HTTP ${res.status}`);
      const { penjelasan } = await res.json();
      setAnnotation(penjelasan);
    } catch (err) {
      alert('Gagal generate penjelasan: ' + err.message);
    } finally {
      setAnnotating(false);
    }
  }

  function goToTicker(e) {
    e.preventDefault();
    const t = searchInput.trim().toUpperCase();
    if (!t) return;
    navigate(`/stock-detail?t=${t}`);
    setSearchInput('');
  }

  useEffect(() => { setData(null); setAnnotation(null); }, [ticker]); // reset pas ganti ticker, tapi gak pas cuma ganti timeframe (biar gak kedip kosong)

  useEffect(() => {
    let cancelled = false;
    setError(null);
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/scanner/${ticker}`);
        if (!res.ok) throw new Error((await res.json()).detail || `HTTP ${res.status}`);
        const json = await res.json();
        if (!cancelled) setData(json);
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    })();
    return () => { cancelled = true; };
  }, [ticker]);

  const candles = data ? data.candles.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume })) : null;
  const candleRef = useCandlestickChart(candles, data?.levels, data?.ai_zones);

  const [brokerFlow, setBrokerFlow] = useState(null);
  const [brokerFlowFrom, setBrokerFlowFrom] = useState(new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10));
  const [brokerFlowTo, setBrokerFlowTo] = useState(BROKER_FLOW_MAX_DATE);
  const [akumDays, setAkumDays] = useState(7);
  useEffect(() => {
    let cancelled = false;
    setBrokerFlow(null);
    fetch(`${API_BASE}/scanner/${ticker}/broker-flow?from_date=${brokerFlowFrom}&to_date=${brokerFlowTo}&akum_days=${akumDays}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((json) => { if (!cancelled) setBrokerFlow(json); })
      .catch(() => { if (!cancelled) setBrokerFlow(null); });
    return () => { cancelled = true; };
  }, [ticker, brokerFlowFrom, brokerFlowTo, akumDays]);

  const m = signalMeta(data?.signal);
  const needleDeg = data ? (data.total_score / 100) * 180 - 90 : -90;

  return (
    <>
      <header className="sticky top-0 z-10 bg-[#0B0F1A]/90 backdrop-blur border-b border-border px-8 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight">Stock Detail</h1>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">{ticker}</p>
        </div>
        <div className="flex items-center gap-4">
          <form onSubmit={goToTicker} className="relative">
            <svg className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" width="14" height="14" viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" /><path d="M21 21L16.65 16.65" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
            <input
              type="text" placeholder="Cari ticker (e.g. BBCA)" value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              className="w-48 bg-card border border-border rounded-lg pl-9 pr-3 py-1.5 text-xs text-white font-mono uppercase placeholder:text-slate-500 placeholder:normal-case focus:outline-none focus:border-accent/60"
            />
          </form>
          <span className="flex items-center gap-2 text-xs font-semibold px-3 py-1.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span> MARKET OPEN
          </span>
          <button className="relative w-9 h-9 rounded-lg bg-card border border-border flex items-center justify-center hover:border-accent/50 transition">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M18 8A6 6 0 0 0 6 8C6 15 3 17 3 17H21S18 15 18 8Z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /><path d="M13.73 21A2 2 0 0 1 10.27 21" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
            <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-strong"></span>
          </button>
        </div>
      </header>

      <div className="p-8 space-y-6">
        {error && (
          <div className="p-4 rounded-xl bg-strong/10 border border-strong/30 text-sm text-strong">
            Gak bisa muat data {ticker}: {error}. Pastikan backend jalan di {API_BASE}.
          </div>
        )}

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-accent/20 to-cyan/20 border border-accent/30 flex items-center justify-center font-mono font-bold text-cyan">{data ? data.ticker.slice(0, 2) : '--'}</div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-2xl font-extrabold text-white font-mono">{data?.ticker || '—'}</h2>
                <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${m.cls}`}>{data ? `${m.dot} ${data.signal === 'None' ? 'No Signal' : data.signal}` : '—'}</span>
              </div>
              <p className="text-sm text-slate-500">{data?.sector || '—'}</p>
            </div>
            <a
              href={`https://www.tradingview.com/chart/?symbol=IDX%3A${ticker}`} target="_blank" rel="noreferrer"
              className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg bg-white/5 text-slate-300 border border-border hover:border-accent/50 hover:text-white transition"
            >
              Buka di TradingView
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none"><path d="M7 17L17 7M17 7H9M17 7V15" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </a>
          </div>
          <div className="text-right">
            <p className="text-2xl font-bold text-white font-mono">{data ? `Rp ${data.price.toLocaleString('id-ID')}` : 'Rp —'}</p>
            <p className={`text-sm font-semibold font-mono ${data && data.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{data ? `${data.change_pct >= 0 ? '+' : ''}${data.change_pct}% today` : '—'}</p>
          </div>
        </div>

        <div className="glow-border rounded-2xl bg-card border border-border p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-bold text-white tracking-tight">Price Chart</h3>
            <span className="text-[10px] text-slate-500 font-mono">Daily · histori penuh</span>
          </div>
          <div ref={candleRef} style={{ height: 360 }}></div>
            {data?.levels && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4 text-center">
                <div>
                  <p className="text-[10px] text-slate-500 uppercase tracking-wider">Support</p>
                  <p className="text-sm font-mono font-semibold text-emerald-400">Rp{data.levels.support.toLocaleString('id-ID')}</p>
                </div>
                <div>
                  <p className="text-[10px] text-slate-500 uppercase tracking-wider">Resistance</p>
                  <p className="text-sm font-mono font-semibold text-red-400">Rp{data.levels.resistance.toLocaleString('id-ID')}</p>
                </div>
                <div>
                  <p className="text-[10px] text-slate-500 uppercase tracking-wider">Entry Zone</p>
                  <p className="text-sm font-mono font-semibold text-white">{data.levels.entry_low.toLocaleString('id-ID')}–{data.levels.entry_high.toLocaleString('id-ID')}</p>
                </div>
                <div>
                  <p className="text-[10px] text-slate-500 uppercase tracking-wider">Risk</p>
                  <p className="text-sm font-mono font-semibold text-moderate">{data.levels.risk_pct}%</p>
                </div>
              </div>
            )}
            {data?.levels && (
              <div className="mt-4 pt-4 border-t border-border">
                {!annotation && (
                  <button onClick={runAnnotate} disabled={annotating} className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg bg-cyan/10 text-cyan border border-cyan/30 hover:bg-cyan/20 transition disabled:opacity-50">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M12 2L14.5 8.5L21 9.3L16.3 13.9L17.6 20.5L12 17.1L6.4 20.5L7.7 13.9L3 9.3L9.5 8.5L12 2Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" /></svg>
                    {annotating ? 'Nge-generate...' : 'Jelasin level ini (AI)'}
                  </button>
                )}
                {annotation && <p className="text-sm text-slate-300 leading-relaxed text-justify">{annotation}</p>}
              </div>
            )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-4 gap-5">
          <div className="glow-border rounded-2xl bg-card border border-border p-5 flex flex-col items-center justify-center">
            <h3 className="text-sm font-bold text-white tracking-tight self-start mb-2">Accumulation Score</h3>
            <svg viewBox="-18 -5 236 145" className="w-full max-w-[240px]">
              <path d="M 20.00 100.00 A 80 80 0 0 1 35.28 52.98" fill="none" stroke="#22C55E" strokeWidth="16" strokeLinecap="round" />
              <path d="M 35.28 52.98 A 80 80 0 0 1 75.28 23.92" fill="none" stroke="#84CC16" strokeWidth="16" strokeLinecap="round" />
              <path d="M 75.28 23.92 A 80 80 0 0 1 124.72 23.92" fill="none" stroke="#EAB308" strokeWidth="16" strokeLinecap="round" />
              <path d="M 124.72 23.92 A 80 80 0 0 1 164.72 52.98" fill="none" stroke="#F97316" strokeWidth="16" strokeLinecap="round" />
              <path d="M 164.72 52.98 A 80 80 0 0 1 180.00 100.00" fill="none" stroke="#EF4444" strokeWidth="16" strokeLinecap="round" />

              <text x="4" y="72" textAnchor="middle" fill="#64748B" fontSize="7.5" fontWeight="600" fontFamily="Manrope" letterSpacing="0.3">VERY LOW</text>
              <text x="40" y="20" textAnchor="middle" fill="#64748B" fontSize="7.5" fontWeight="600" fontFamily="Manrope" letterSpacing="0.3">LOW</text>
              <text x="100" y="6" textAnchor="middle" fill="#64748B" fontSize="7.5" fontWeight="600" fontFamily="Manrope" letterSpacing="0.3">MODERATE</text>
              <text x="160" y="20" textAnchor="middle" fill="#64748B" fontSize="7.5" fontWeight="600" fontFamily="Manrope" letterSpacing="0.3">HIGH</text>
              <text x="196" y="72" textAnchor="middle" fill="#64748B" fontSize="7.5" fontWeight="600" fontFamily="Manrope" letterSpacing="0.3">VERY HIGH</text>

              <g className="needle" style={{ transform: `rotate(${needleDeg}deg)`, transformOrigin: '100px 100px', transition: 'transform 1.2s cubic-bezier(.34,1.56,.64,1)' }}>
                <line x1="100" y1="100" x2="100" y2="34" stroke="#FFFFFF" strokeWidth="3" strokeLinecap="round" />
                <circle cx="100" cy="100" r="6" fill="#FFFFFF" />
                <circle cx="100" cy="100" r="2.5" fill="#0B0F1A" />
              </g>
            </svg>
            <div className="text-center -mt-6">
              <p className="text-3xl font-extrabold text-white font-mono leading-none">{data?.total_score ?? '--'}<span className="text-sm text-slate-500 font-medium">/100</span></p>
            </div>
            <p className="text-xs text-slate-500 mt-2">Zone: <span className={`font-semibold ${zoneColorClass(data?.signal)}`}>{data ? zoneLabel(data.signal) : '—'}</span></p>
          </div>
          <ScoreCard label="Volume Score" value={data?.volume_score} max={25} barClass="bg-cyan" />
          <ScoreCard label="Price Score" value={data?.price_score} max={25} barClass="bg-accent" />
          <ScoreCard label="Technical Score" value={data?.technical_score} max={20} barClass="bg-moderate" />
        </div>

        {data && <GayaTradingCard data={data} />}

        {data?.company && <CompanyInfo company={data.company} ticker={data.ticker} financialStatement={brokerFlow?.financial_statement} notation={brokerFlow?.notation} />}
        {data?.mentor_call && <MentorCallCard call={data.mentor_call} />}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          <div className="glow-border rounded-2xl bg-gradient-to-br from-card to-card2 border border-accent/30 p-5">
            <div className="flex items-center gap-2 mb-3">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M12 2L14.5 8.5L21 9.3L16.3 13.9L17.6 20.5L12 17.1L6.4 20.5L7.7 13.9L3 9.3L9.5 8.5L12 2Z" stroke="#06B6D4" strokeWidth="1.8" strokeLinejoin="round" /></svg>
              <h3 className="text-sm font-bold text-white tracking-tight">AI Prediction</h3>
            </div>
            {!data?.ai_prediction && (
              <>
                <span className="inline-block text-xs font-bold px-3 py-1.5 rounded-full bg-white/5 text-slate-400 border border-border mb-3">Belum tersedia</span>
                <p className="text-sm text-slate-500">Model XGBoost buat prediksi arah harga 5 hari belum di-training buat ticker ini (atau belum ada modelnya sama sekali).</p>
              </>
            )}
            {data?.ai_prediction && (
              <>
                <span className={`inline-flex items-center gap-1.5 text-xs font-bold px-3 py-1.5 rounded-full border mb-3 ${data.ai_prediction.direction === 'up' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' : 'bg-strong/10 text-strong border-strong/30'}`}>
                  {data.ai_prediction.direction === 'up' ? '▲ Naik' : '▼ Turun'} · {(data.ai_prediction.probability * 100).toFixed(0)}%
                </span>
                <p className="text-sm text-slate-400 mb-3">Prediksi arah harga 5 hari ke depan, dari model XGBoost (fitur teknikal).</p>
                {data.ai_prediction.model_accuracy != null && (
                  <p className="text-[11px] text-slate-500 leading-relaxed">
                    Akurasi historis model: <span className="text-slate-300 font-semibold">{(data.ai_prediction.model_accuracy * 100).toFixed(0)}%</span>
                    {data.ai_prediction.baseline_accuracy != null && ` (baseline tebak asal: ${(data.ai_prediction.baseline_accuracy * 100).toFixed(0)}%)`}
                    {' '}— pertimbangan tambahan, bukan jaminan.
                  </p>
                )}
              </>
            )}
          </div>

          {!brokerFlow?.configured && (
            <div className="glow-border rounded-2xl bg-card border border-border p-5">
              <h3 className="text-sm font-bold text-white tracking-tight mb-3">Broker Flow (Invezgo)</h3>
              <p className="text-sm text-slate-500 py-8 text-center">
                Gagal ambil data broker flow (Invezgo) — coba lagi nanti.
              </p>
            </div>
          )}

          {brokerFlow?.configured && (
            <>
            <div className="glow-border rounded-2xl bg-card border border-border p-5">
              <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
                <h3 className="text-sm font-bold text-white tracking-tight">Broker Summary (Net Value)</h3>
                <div className="flex items-center gap-1.5">
                  <input type="date" value={brokerFlowFrom} min={BROKER_FLOW_MIN_DATE} max={brokerFlowTo}
                    onChange={(e) => setBrokerFlowFrom(e.target.value)}
                    className="text-[10px] font-semibold px-2 py-1 rounded-lg bg-white/5 text-slate-300 border border-border" />
                  <span className="text-slate-600 text-[10px]">—</span>
                  <input type="date" value={brokerFlowTo} min={brokerFlowFrom} max={BROKER_FLOW_MAX_DATE}
                    onChange={(e) => setBrokerFlowTo(e.target.value)}
                    className="text-[10px] font-semibold px-2 py-1 rounded-lg bg-white/5 text-slate-300 border border-border" />
                </div>
              </div>
              {brokerFlow.broker_summary?.length ? (
                <BrokerSummaryTable
                  brokers={brokerFlow.broker_summary}
                  topAkumulator={brokerFlow.top_broker_stalker?.summary ? {
                    broker: brokerFlow.top_broker_stalker.brokers,
                    active: brokerFlow.top_broker_stalker.summary.active,
                    total: brokerFlow.top_broker_stalker.summary.total,
                  } : null}
                  akumPeriod={akumDays}
                  onAkumPeriodChange={setAkumDays}
                />
              ) : <p className="text-sm text-slate-500 py-4 text-center">Gagal ambil data broker summary buat rentang ini — coba lagi nanti.</p>}
            </div>

            <div className="glow-border rounded-2xl bg-card border border-border p-5">
              <h3 className="text-sm font-bold text-white tracking-tight mb-3">Insider Activity (2 tahun terakhir)</h3>
              {brokerFlow.insider_activity?.data?.length ? (
                <ul className="space-y-2 text-sm text-slate-300">
                  {brokerFlow.insider_activity.data.slice(0, 8).map((row, i) => {
                    const isNew = row.date && (Date.now() - new Date(row.date).getTime()) < 30 * 86400000;
                    const role = (row.badge || '').replace(/[{}]/g, '');
                    const latest = row.subrow?.[0];
                    return (
                      <li key={i} className="flex items-start justify-between gap-2 border-b border-border/30 last:border-0 pb-2 last:pb-0">
                        <div>
                          <span className="font-semibold text-white">{row.name}</span>
                          {role && <span className="text-[10px] text-slate-500 ml-1">({role})</span>}
                          {latest && (
                            <p className="text-[11px] text-slate-400 mt-0.5">
                              <span className={latest.status === 'Buy' ? 'text-emerald-400' : 'text-red-400'}>{latest.status}</span>
                              {' '}{Number(latest.value ?? 0).toLocaleString('id-ID')} lembar @Rp{Number(latest.price ?? 0).toLocaleString('id-ID')}
                            </p>
                          )}
                        </div>
                        {isNew && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-cyan/20 text-cyan shrink-0">BARU</span>}
                      </li>
                    );
                  })}
                </ul>
              ) : <p className="text-sm text-slate-500">Gak ada aktivitas insider tercatat.</p>}
            </div>

            <div className="glow-border rounded-2xl bg-card border border-border p-5">
              <h3 className="text-sm font-bold text-white tracking-tight mb-3">Kepemilikan &gt;5% (90 hari)</h3>
              {brokerFlow.shareholder_above?.data?.length ? (
                <ul className="space-y-1.5 text-sm text-slate-300">
                  {brokerFlow.shareholder_above.data.slice(0, 5).map((row, i) => (
                    <li key={i}>{row.name}: {row.prev_pct?.toFixed(1)}% → {row.next_pct?.toFixed(1)}%</li>
                  ))}
                </ul>
              ) : <p className="text-sm text-slate-500">Gak ada perubahan &gt;5% tercatat.</p>}
            </div>

            </>
          )}
        </div>
      </div>
    </>
  );
}
