import { useEffect, useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { API_BASE } from '../lib/api';
import { signalMeta, zoneLabel, zoneColorClass } from '../lib/signal';
import { useChart } from '../hooks/useChart';
import { useCandlestickChart } from '../hooks/useCandlestickChart';

// Struktur field laporan keuangan Invezgo belum diverifikasi lawan API asli —
// render tabel generik dari kolom apapun yang ada di data, daripada nebak nama
// field (revenue/laba/dst) yang bisa aja salah.
function GenericTable({ raw }) {
  const rows = Array.isArray(raw) ? raw : Array.isArray(raw?.data) ? raw.data : null;
  if (rows && !rows.length) return <p className="text-slate-500 text-[11px]">Belum ada data.</p>;
  if (!rows) {
    return <pre className="text-[10px] text-slate-400 bg-black/30 rounded-lg p-3 overflow-auto max-h-40 font-mono">{JSON.stringify(raw, null, 2)}</pre>;
  }
  const cols = Object.keys(rows[0]).slice(0, 7);
  return (
    <div className="overflow-x-auto">
      <table className="text-[11px] text-slate-300 w-full">
        <thead>
          <tr className="text-slate-500 uppercase text-[9px]">
            {cols.map((c) => <th key={c} className="text-left pr-3 pb-1 font-semibold">{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 8).map((r, i) => (
            <tr key={i} className="border-t border-border/50">
              {cols.map((c) => <td key={c} className="pr-3 py-1 font-mono">{typeof r[c] === 'number' ? r[c].toLocaleString('id-ID') : String(r[c] ?? '—')}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const STATEMENT_TABS = { BS: 'Neraca', IS: 'Laba Rugi', CF: 'Arus Kas' };
// pecah 9 widget broker-flow jadi 3 tab (dulu numpuk semua di 1 grid, kepanjangan
// scroll & bikin pusing) — grouping berdasar jenis sinyal, sama pola tab kayak
// FinancialStatementTable di atas.
const FLOW_TABS = { ringkasan: 'Ringkasan', order: 'Order Flow', historis: 'Historis' };

function FinancialStatementTable({ data }) {
  const [tab, setTab] = useState('BS');
  const raw = data?.[tab];
  return (
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
  );
}

function CompanyInfo({ company, ticker, financialStatement }) {
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
        <h3 className="text-sm font-bold text-white tracking-tight">{company.name || ticker}</h3>
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
          <p className="text-xs text-slate-500">Belum tersedia — nunggu Invezgo API aktif.</p>
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

// Struktur response Invezgo belum diverifikasi lawan API asli buat endpoint ini
// (lihat invezgo_client.py::get_sankey_chart) — daripada nebak field yang salah,
// dump JSON mentahnya biar minimal keliatan APAKAH datanya berguna. Chart Sankey
// beneran nunggu struktur node/link asli begitu key aktif.
function RawJsonPreview({ raw, note }) {
  return (
    <div>
      <p className="text-[11px] text-slate-500 mb-2">{note}</p>
      <pre className="text-[10px] text-slate-400 bg-black/30 rounded-lg p-3 overflow-auto max-h-40 font-mono">{JSON.stringify(raw, null, 2)}</pre>
    </div>
  );
}

// Field CONFIRMED lawan OpenAPI spec asli Invezgo: [{month, start_price,
// end_price, percentage_change}]. percentage_change balik sebagai number
// (bukan string) di contoh spec, tapi tetep dijaga Number() jaga-jaga.
function parseSeasonality(raw) {
  const rows = Array.isArray(raw) ? raw : Array.isArray(raw?.data) ? raw.data : null;
  if (rows && !rows.length) return { empty: true };
  if (!rows) return { malformed: true };
  const labelKey = ['month', 'label', 'period', 'name'].find((k) => rows[0][k] != null);
  const valueKey = ['percentage_change', 'avg_return', 'avg_return_pct', 'return', 'return_pct', 'avg', 'value'].find((k) => rows[0][k] != null && !isNaN(Number(rows[0][k])));
  if (!labelKey || !valueKey) return { malformed: true };
  return { labels: rows.map((r) => String(r[labelKey])), values: rows.map((r) => Number(r[valueKey])) };
}

function SeasonalityChart({ raw }) {
  const parsed = parseSeasonality(raw);
  const config = parsed.labels ? {
    type: 'bar',
    data: { labels: parsed.labels, datasets: [{ data: parsed.values, backgroundColor: (ctx) => (ctx.raw >= 0 ? '#10B981' : '#EF4444'), borderRadius: 3 }] },
    options: { plugins: { legend: { display: false } }, scales: { x: { grid: { display: false }, ticks: { color: '#94A3B8', font: { size: 9 } } }, y: { grid: { color: '#1F2937' }, ticks: { color: '#64748B', font: { size: 9 } } } } },
  } : null;
  const chartRef = useChart(config);
  if (parsed.empty) return <p className="text-slate-500 text-[11px]">Belum ada data seasonality.</p>;
  if (parsed.malformed) return <RawJsonPreview raw={raw} note="Struktur field belum ketebak otomatis — data mentah di bawah, chart nyusul begitu bentuknya jelas." />;
  return <canvas ref={chartRef} height="90"></canvas>;
}

// Format CONFIRMED lawan OpenAPI spec asli Invezgo: {"nodes":[{"name"}],
// "links":[{"source","target","value"}]} — standar D3 Sankey. Belum ada lib
// diagram Sankey di stack (chart.js gak native support), jadi ditampilin
// sebagai tabel flow terurut (paling gede duluan) — jujur & berguna tanpa
// nambah dependency baru buat 1 chart. Upgrade ke diagram visual beneran
// kalau nanti worth-nya kelihatan pas data asli.
function SankeyFlowTable({ raw }) {
  const links = raw?.links;
  if (!Array.isArray(links)) return <RawJsonPreview raw={raw} note="Struktur field belum sesuai — data mentah di bawah." />;
  if (!links.length) return <p className="text-slate-500 text-[11px]">Belum ada arus tercatat.</p>;
  const sorted = [...links].sort((a, b) => Math.abs(b.value) - Math.abs(a.value)).slice(0, 8);
  return (
    <ul className="space-y-1 text-slate-300 font-mono">
      {sorted.map((l, i) => (
        <li key={i}>{String(l.source).trim()} → {String(l.target).trim()}: Rp{Number(l.value).toLocaleString('id-ID')}</li>
      ))}
    </ul>
  );
}

// Format CONFIRMED lawan OpenAPI spec asli: {"price":[{date,open,high,low,close,
// volume}], "broker":[{"broker","data":[{"date","value"}]}]} — akumulasi/
// distribusi broker dari waktu ke waktu (BEDA dari broker_summary yang cuma
// snapshot 1 rentang tanggal). Chart net value kumulatif per broker, top 5
// broker paling aktif (biar gak numpuk garis).
function InventoryChart({ raw }) {
  const brokers = raw?.broker;
  const config = Array.isArray(brokers) && brokers.length ? (() => {
    const top = [...brokers].sort((a, b) => {
      const sumA = (a.data || []).reduce((s, d) => s + Math.abs(d.value), 0);
      const sumB = (b.data || []).reduce((s, d) => s + Math.abs(d.value), 0);
      return sumB - sumA;
    }).slice(0, 5);
    const dates = [...new Set(top.flatMap((b) => (b.data || []).map((d) => d.date)))].sort();
    const colors = ['#06B6D4', '#8B5CF6', '#F59E0B', '#10B981', '#EF4444'];
    return {
      type: 'line',
      data: {
        labels: dates,
        datasets: top.map((b, i) => {
          let cum = 0;
          const byDate = Object.fromEntries((b.data || []).map((d) => [d.date, d.value]));
          return {
            label: b.broker,
            data: dates.map((d) => { cum += byDate[d] || 0; return cum; }),
            borderColor: colors[i % colors.length], borderWidth: 1.5, pointRadius: 0, tension: 0.2,
          };
        }),
      },
      options: {
        plugins: { legend: { display: true, labels: { color: '#94A3B8', boxWidth: 10, font: { size: 9 } } } },
        scales: { x: { display: false }, y: { grid: { color: '#1F2937' }, ticks: { color: '#64748B', font: { size: 9 } } } },
      },
    };
  })() : null;
  const chartRef = useChart(config);
  if (!config) return <p className="text-slate-500">Gak ada data inventory tercatat.</p>;
  return (
    <>
      <canvas ref={chartRef} height="100"></canvas>
      <p className="text-[10px] text-slate-500 mt-1">Net value kumulatif, top 5 broker paling aktif (7 hari)</p>
    </>
  );
}

function OrderQueueWidget({ ticker, defaultPrice }) {
  const [price, setPrice] = useState(defaultPrice ?? '');
  const [side, setSide] = useState('BUY');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  async function fetchQueue(e) {
    e.preventDefault();
    if (!price) return;
    setLoading(true);
    setResult(null);
    try {
      const res = await fetch(`${API_BASE}/scanner/${ticker}/order-queue?price=${price}&side=${side}`);
      setResult(await res.json());
    } catch {
      setResult({ configured: true, data: null });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-2">Order Queue</p>
      <form onSubmit={fetchQueue} className="flex items-center gap-2 mb-2">
        <input type="number" step="any" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="Harga"
          className="w-20 bg-black/30 border border-border rounded-lg px-2 py-1 text-xs text-white font-mono" />
        <select value={side} onChange={(e) => setSide(e.target.value)} className="bg-black/30 border border-border rounded-lg px-2 py-1 text-xs text-white">
          <option value="BUY">BUY</option>
          <option value="SELL">SELL</option>
        </select>
        <button type="submit" disabled={loading} className="text-xs font-semibold px-2.5 py-1 rounded-lg bg-accent/10 text-accent border border-accent/30 hover:bg-accent/20 transition disabled:opacity-50">
          {loading ? '...' : 'Cek'}
        </button>
      </form>
      {result && !result.configured && <p className="text-slate-500">Belum tersedia — nunggu Invezgo API aktif.</p>}
      {result?.configured && !result.data?.length && <p className="text-slate-500">Gak ada antrian di level harga ini.</p>}
      {result?.configured && result.data?.length > 0 && (
        <ul className="space-y-1 text-slate-300 font-mono max-h-32 overflow-auto">
          {result.data.slice(0, 8).map((q, i) => (
            <li key={i}>{q.time ?? '—'} · order {Number(q.order_volume ?? 0).toLocaleString('id-ID')} · sisa {Number(q.open_volume ?? 0).toLocaleString('id-ID')}</li>
          ))}
        </ul>
      )}
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
  const [flowTab, setFlowTab] = useState('ringkasan');

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
  useEffect(() => {
    let cancelled = false;
    setBrokerFlow(null);
    fetch(`${API_BASE}/scanner/${ticker}/broker-flow`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((json) => { if (!cancelled) setBrokerFlow(json); })
      .catch(() => { if (!cancelled) setBrokerFlow(null); });
    return () => { cancelled = true; };
  }, [ticker]);

  const topBrokers = brokerFlow?.broker_summary
    ? [...brokerFlow.broker_summary].sort((a, b) => Math.abs(b.net_value) - Math.abs(a.net_value)).slice(0, 7)
    : null;
  const brokerConfig = topBrokers ? {
    type: 'bar',
    data: {
      labels: topBrokers.map((b) => b.code),
      datasets: [{
        data: topBrokers.map((b) => b.net_value),
        backgroundColor: (ctx) => (ctx.raw >= 0 ? '#2563EB' : '#EF4444'),
        borderRadius: 4,
      }],
    },
    options: { plugins: { legend: { display: false } }, scales: { x: { grid: { display: false }, ticks: { color: '#94A3B8', font: { size: 11 } } }, y: { grid: { color: '#1F2937' }, ticks: { color: '#64748B', font: { size: 10 } } } } },
  } : null;
  const brokerRef = useChart(brokerConfig);

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

        {data?.company && <CompanyInfo company={data.company} ticker={data.ticker} financialStatement={brokerFlow?.financial_statement} />}
        {data?.mentor_call && <MentorCallCard call={data.mentor_call} />}

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
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

          <div className="md:col-span-2 glow-border rounded-2xl bg-card border border-border p-5">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-bold text-white tracking-tight">Broker Summary (Net Value)</h3>
              {!brokerFlow?.configured && (
                <span className="text-[10px] text-slate-500 font-mono">Belum tersedia — nunggu Invezgo API aktif</span>
              )}
            </div>
            {!brokerFlow?.configured && (
              <p className="text-sm text-slate-500 py-8 text-center">
                Data broker summary asli belum aktif (nunggu langganan Invezgo). Angka net buy/sell per broker bakal muncul di sini begitu API key-nya keisi — bukan data karangan.
              </p>
            )}
            {brokerFlow?.configured && !topBrokers && (
              <p className="text-sm text-slate-500 py-8 text-center">Gagal ambil data broker summary hari ini — coba lagi nanti.</p>
            )}
            {topBrokers && <canvas ref={brokerRef} height="140"></canvas>}

            {brokerFlow?.configured && (
              <div className="mt-4 pt-4 border-t border-border">
                <div className="flex items-center gap-1 mb-4">
                  {Object.entries(FLOW_TABS).map(([key, label]) => (
                    <button key={key} onClick={() => setFlowTab(key)}
                      className={`text-[10px] font-semibold px-2 py-1 rounded-lg border transition ${flowTab === key ? 'bg-accent/10 text-accent border-accent/30' : 'bg-white/5 text-slate-500 border-border hover:text-white'}`}>
                      {label}
                    </button>
                  ))}
                </div>

                {flowTab === 'ringkasan' && (
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
                    <div>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Insider Activity (90 hari)</p>
                      {brokerFlow.insider_activity?.data?.length ? (
                        <ul className="space-y-1 text-slate-300">
                          {brokerFlow.insider_activity.data.slice(0, 3).map((row, i) => (
                            <li key={i}>{row.name} {row.change >= 0 ? '+' : ''}{row.change}%</li>
                          ))}
                        </ul>
                      ) : <p className="text-slate-500">Gak ada aktivitas insider tercatat.</p>}
                    </div>
                    <div>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Notasi Khusus</p>
                      {brokerFlow.notation?.list?.length ? (
                        <p className="text-strong font-semibold">⚠ {brokerFlow.notation.list.map((n) => n.notation).join(', ')}</p>
                      ) : <p className="text-slate-500">Gak ada notasi aktif.</p>}
                    </div>
                    <div>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Top Akumulator</p>
                      {brokerFlow.top_broker_stalker?.summary ? (
                        <p className="text-slate-300">
                          {brokerFlow.top_broker_stalker.broker} — {brokerFlow.top_broker_stalker.summary.active} hari aktif,
                          total Rp{Number(brokerFlow.top_broker_stalker.summary.total).toLocaleString('id-ID')}
                        </p>
                      ) : <p className="text-slate-500">Belum ada data histori broker.</p>}
                    </div>
                  </div>
                )}

                {flowTab === 'order' && (
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
                    <div>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Tape Reading</p>
                      {brokerFlow.running_trade?.data?.length ? (
                        <ul className="space-y-1 text-slate-300 font-mono">
                          {brokerFlow.running_trade.data.slice(0, 3).map((t, i) => (
                            <li key={i} className={t.type === 'BUY' ? 'text-emerald-400' : 'text-red-400'}>
                              {t.time} {t.type} {t.volume.toLocaleString('id-ID')} @{t.price}
                            </li>
                          ))}
                        </ul>
                      ) : <p className="text-slate-500">Belum ada transaksi tercatat.</p>}
                    </div>
                    <div>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Money Flow (Sankey)</p>
                      {brokerFlow.sankey_chart ? <SankeyFlowTable raw={brokerFlow.sankey_chart} /> : <p className="text-slate-500">Belum tersedia.</p>}
                    </div>
                    <div>
                      <OrderQueueWidget ticker={data.ticker} defaultPrice={data.price} />
                    </div>
                  </div>
                )}

                {flowTab === 'historis' && (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                    <div className="md:col-span-2">
                      <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Inventory Chart (akumulasi/distribusi broker, 7 hari)</p>
                      {brokerFlow.inventory_chart ? <InventoryChart raw={brokerFlow.inventory_chart} /> : <p className="text-slate-500">Belum tersedia.</p>}
                    </div>
                    <div>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Seasonality</p>
                      {brokerFlow.price_seasonality ? <SeasonalityChart raw={brokerFlow.price_seasonality} /> : <p className="text-slate-500">Belum tersedia.</p>}
                    </div>
                    <div>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Kepemilikan &gt;5% (90 hari)</p>
                      {brokerFlow.shareholder_above?.data?.length ? (
                        <ul className="space-y-1 text-slate-300">
                          {brokerFlow.shareholder_above.data.slice(0, 3).map((row, i) => (
                            <li key={i}>{row.name}: {row.prev_pct?.toFixed(1)}% → {row.next_pct?.toFixed(1)}%</li>
                          ))}
                        </ul>
                      ) : <p className="text-slate-500">Gak ada perubahan &gt;5% tercatat.</p>}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
