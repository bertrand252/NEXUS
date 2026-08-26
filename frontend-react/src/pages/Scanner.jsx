import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { API_BASE } from '../lib/api';
import { signalMeta } from '../lib/signal';

// backend doesn't know sector names — keep a small client-side lookup for display only
const SECTOR = {
  ANTM: 'Basic Materials', BBRI: 'Banking', ADRO: 'Energy', ASII: 'Consumer',
  BMRI: 'Banking', ICBP: 'Consumer', TLKM: 'Technology', GOTO: 'Technology', UNVR: 'Consumer', MDKA: 'Basic Materials',
};

function ScanRow({ s }) {
  const m = signalMeta(s.signal);
  return (
    <tr className="hover:bg-white/[0.03] transition">
      <td className="px-5 py-3">
        <p className="font-sans font-semibold text-white">{s.ticker}</p>
        <p className="text-[10px] text-slate-500 font-sans">{SECTOR[s.ticker] || '—'}</p>
      </td>
      <td className="px-5 py-3 text-slate-300">Rp {s.price.toLocaleString('id-ID')}</td>
      <td className={`px-5 py-3 ${s.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{s.change_pct >= 0 ? '+' : ''}{s.change_pct}%</td>
      <td className="px-5 py-3 text-slate-300">{s.volume_ratio}x</td>
      <td className="px-5 py-3">
        <div className="flex items-center gap-2">
          <span className="text-white font-semibold">{s.total_score}</span>
          <div className="w-16 h-1.5 rounded-full bg-white/5 overflow-hidden">
            <div className="h-full rounded-full bg-gradient-to-r from-accent to-cyan" style={{ width: `${s.total_score}%` }}></div>
          </div>
        </div>
      </td>
      <td className="px-5 py-3"><span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${m.cls}`}>{m.dot} {s.signal === 'None' ? 'No Signal' : s.signal}</span></td>
      <td className="px-5 py-3 text-right">
        <Link to={`/stock-detail?t=${s.ticker}`} className="text-[11px] font-sans font-semibold px-3 py-1.5 rounded-lg bg-accent/10 text-accent border border-accent/30 hover:bg-accent/20 transition">View</Link>
      </td>
    </tr>
  );
}

export default function Scanner() {
  const [allData, setAllData] = useState([]);
  const [errors, setErrors] = useState([]);
  const [loadError, setLoadError] = useState(false);
  const [refreshedAt, setRefreshedAt] = useState(null);

  const [search, setSearch] = useState('');
  const [signal, setSignal] = useState('');
  const [sector, setSector] = useState('');
  const [minScoreOnly, setMinScoreOnly] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/scanner`);
        if (!res.ok) throw new Error(`Backend error ${res.status}`);
        const { data, errors } = await res.json();
        setAllData(data);
        setErrors(errors);
        setRefreshedAt(new Date().toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' }));
      } catch {
        setLoadError(true);
      }
    })();
  }, []);

  const filtered = useMemo(() => {
    const q = search.trim().toUpperCase();
    return allData.filter((s) => {
      if (q && !s.ticker.includes(q)) return false;
      if (signal && s.signal !== signal) return false;
      if (sector && SECTOR[s.ticker] !== sector) return false;
      if (minScoreOnly && s.total_score < 50) return false;
      return true;
    });
  }, [allData, search, signal, sector, minScoreOnly]);

  const statusLine = loadError
    ? 'Gagal memuat data'
    : `Showing ${filtered.length} of ${allData.length} IDX tickers` +
      (refreshedAt ? ` · Refreshed ${refreshedAt} WIB` : '') +
      (errors.length ? ` · ${errors.length} gagal dimuat (${errors.map((e) => e.ticker).join(', ')})` : '');

  return (
    <>
      <header className="sticky top-0 z-10 bg-[#0B0F1A]/90 backdrop-blur border-b border-border px-8 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight">Scanner</h1>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">IDX Watchlist</p>
        </div>
        <div className="flex items-center gap-4">
          <span className="flex items-center gap-2 text-xs font-semibold px-3 py-1.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span> MARKET OPEN
          </span>
          <button className="relative w-9 h-9 rounded-lg bg-card border border-border flex items-center justify-center hover:border-accent/50 transition">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M18 8A6 6 0 0 0 6 8C6 15 3 17 3 17H21S18 15 18 8Z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /><path d="M13.73 21A2 2 0 0 1 10.27 21" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
            <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-strong"></span>
          </button>
        </div>
      </header>

      <div className="p-8 space-y-5">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="relative flex-1 min-w-[220px] max-w-xs">
            <svg className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" width="15" height="15" viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" /><path d="M21 21L16.65 16.65" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
            <input
              type="text" placeholder="Search ticker..." value={search} onChange={(e) => setSearch(e.target.value)}
              className="w-full bg-card border border-border rounded-lg pl-9 pr-3 py-2 text-sm text-white placeholder:text-slate-500 focus:outline-none focus:border-accent/60"
            />
          </div>
          <select value={signal} onChange={(e) => setSignal(e.target.value)} className="bg-card border border-border rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none focus:border-accent/60">
            <option value="">All Signals</option>
            <option value="Strong">🔴 Strong</option>
            <option value="Moderate">🟠 Moderate</option>
            <option value="Weak">🟡 Weak</option>
            <option value="None">⚪ No Signal</option>
          </select>
          <select value={sector} onChange={(e) => setSector(e.target.value)} className="bg-card border border-border rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none focus:border-accent/60">
            <option value="">All Sectors</option>
            <option>Banking</option>
            <option>Energy</option>
            <option>Consumer</option>
            <option>Technology</option>
            <option>Basic Materials</option>
          </select>
          <button
            onClick={() => setMinScoreOnly((v) => !v)}
            className={minScoreOnly
              ? 'ml-auto flex items-center gap-2 text-xs font-semibold px-4 py-2 rounded-lg bg-accent text-white border border-accent transition'
              : 'ml-auto flex items-center gap-2 text-xs font-semibold px-4 py-2 rounded-lg bg-accent/10 text-accent border border-accent/30 hover:bg-accent/20 transition'}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M4 4V20M4 4H14L20 10V20H4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
            Score ≥ 50 filter
          </button>
        </div>

        <div className="glow-border rounded-2xl bg-card border border-border overflow-hidden">
          <div className="overflow-x-auto scrollbar-thin">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider text-slate-500 border-b border-border bg-white/[0.02]">
                  <th className="px-5 py-3 font-medium">Ticker</th>
                  <th className="px-5 py-3 font-medium">Price</th>
                  <th className="px-5 py-3 font-medium">Change</th>
                  <th className="px-5 py-3 font-medium">Volume Ratio</th>
                  <th className="px-5 py-3 font-medium">Score (0–100)</th>
                  <th className="px-5 py-3 font-medium">Signal Status</th>
                  <th className="px-5 py-3 font-medium text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60 font-mono text-[13px]">
                {loadError && (
                  <tr><td colSpan={7} className="px-5 py-8 text-center text-sm text-slate-500">
                    Gak bisa konek ke backend ({API_BASE}). Pastikan <code className="text-cyan">uvicorn main:app --reload --port 8000</code> lagi jalan.
                  </td></tr>
                )}
                {!loadError && filtered.length === 0 && (
                  <tr><td colSpan={7} className="px-5 py-8 text-center text-sm text-slate-500">Gak ada saham yang cocok dengan filter ini.</td></tr>
                )}
                {!loadError && filtered.map((s) => <ScanRow key={s.ticker} s={s} />)}
              </tbody>
            </table>
          </div>
        </div>

        <p className="text-xs text-slate-500 text-center pt-1">{statusLine}</p>
      </div>
    </>
  );
}
