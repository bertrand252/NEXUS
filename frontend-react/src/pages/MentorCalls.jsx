import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { API_BASE } from '../lib/api';
import { signalMeta } from '../lib/signal';

function fmtRupiah(n) {
  return n == null ? '—' : `Rp${n.toLocaleString('id-ID')}`;
}
function fmtPct(n) {
  return n == null ? '—' : `${n >= 0 ? '+' : ''}${n}%`;
}

const STATUS_CLASS = {
  Running: 'bg-accent/10 text-accent border-accent/30',
  'WAIT CORRECTION': 'bg-moderate/10 text-moderate border-moderate/30',
};

function NexusBadge({ nexusSignal }) {
  if (!nexusSignal) {
    return <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full border bg-none/10 text-slate-500 border-none/30">Belum di-scan</span>;
  }
  const m = signalMeta(nexusSignal.signal);
  return (
    <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${m.cls}`}>
      {m.dot} {nexusSignal.signal === 'None' ? 'No Signal' : nexusSignal.signal} ({nexusSignal.total_score})
    </span>
  );
}

export default function MentorCalls() {
  const [calls, setCalls] = useState(null);
  const [warning, setWarning] = useState(null);
  const [loadError, setLoadError] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    try {
      const res = await fetch(`${API_BASE}/mentor-calls`);
      const { data, warning } = await res.json();
      setCalls(data);
      setWarning(warning || null);
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
  }

  useEffect(() => { load(); }, []);

  async function runRefresh() {
    setRefreshing(true);
    try {
      const res = await fetch(`${API_BASE}/mentor-calls/refresh`, { method: 'POST' });
      if (!res.ok) throw new Error(`Refresh gagal (${res.status})`);
      await load();
    } catch (err) {
      alert('Gagal refresh mentor calls: ' + err.message);
    } finally {
      setRefreshing(false);
    }
  }

  const matchCount = calls?.filter((c) => c.nexus_signal && (c.nexus_signal.signal === 'Strong' || c.nexus_signal.signal === 'Moderate')).length ?? 0;

  return (
    <>
      <header className="sticky top-0 z-10 bg-[#0B0F1A]/90 backdrop-blur border-b border-border px-8 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight">Mentor Calls</h1>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">Call saham dari mentor, dibandingin sama sinyal NEXUS sendiri</p>
        </div>
        <button
          onClick={runRefresh} disabled={refreshing}
          className="flex items-center gap-2 text-xs font-semibold px-3 py-1.5 rounded-full bg-cyan/10 text-cyan border border-cyan/30 hover:bg-cyan/20 transition disabled:opacity-50"
        >
          {refreshing ? 'Nge-refresh...' : '↻ Refresh dari Sheet'}
        </button>
      </header>

      <div className="p-8 space-y-5">
        {loadError && (
          <div className="p-4 rounded-xl bg-strong/10 border border-strong/30 text-sm text-strong">
            Gak bisa konek ke backend ({API_BASE}).
          </div>
        )}

        {!loadError && warning && (
          <div className="p-4 rounded-xl bg-moderate/10 border border-moderate/30 text-sm text-moderate flex items-center justify-between">
            {warning}
            <button onClick={runRefresh} disabled={refreshing} className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-moderate/20 hover:bg-moderate/30 transition shrink-0 ml-4">
              {refreshing ? 'Memuat...' : 'Refresh sekarang'}
            </button>
          </div>
        )}

        {!loadError && !warning && calls && (
          <p className="text-xs text-slate-500">
            {calls.length} call aktif · {matchCount} di antaranya juga di-flag Strong/Moderate sama NEXUS
          </p>
        )}

        <div className="glow-border rounded-2xl bg-card border border-border overflow-hidden">
          <div className="overflow-x-auto scrollbar-thin">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider text-slate-500 border-b border-border bg-white/[0.02]">
                  <th className="px-5 py-3 font-medium">Ticker</th>
                  <th className="px-5 py-3 font-medium">Recom Date</th>
                  <th className="px-5 py-3 font-medium">Buy Price</th>
                  <th className="px-5 py-3 font-medium">TP1 / TP2 / CL</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="px-5 py-3 font-medium">Current Price</th>
                  <th className="px-5 py-3 font-medium">Floating PnL</th>
                  <th className="px-5 py-3 font-medium">Sinyal NEXUS</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60 font-mono text-[13px]">
                {calls?.length === 0 && (
                  <tr><td colSpan={8} className="px-5 py-8 text-center text-sm text-slate-500">Belum ada data. Klik "Refresh dari Sheet".</td></tr>
                )}
                {calls?.map((c) => (
                  <tr key={c.id} className="hover:bg-white/[0.03] transition">
                    <td className="px-5 py-3">
                      <Link to={`/stock-detail?t=${c.ticker}`} className="font-sans font-semibold text-white hover:text-accent transition">{c.ticker}</Link>
                    </td>
                    <td className="px-5 py-3 text-slate-400">{c.recom_date || '—'}</td>
                    <td className="px-5 py-3 text-slate-300">{fmtRupiah(c.buy_price)}</td>
                    <td className="px-5 py-3 text-slate-400 text-[12px]">
                      <span className="text-emerald-400">{c.tp1 ?? '—'}</span> / <span className="text-emerald-400">{c.tp2 ?? '—'}</span> / <span className="text-red-400">{c.cl ?? '—'}</span>
                    </td>
                    <td className="px-5 py-3">
                      <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${STATUS_CLASS[c.status] || 'bg-none/10 text-slate-400 border-none/30'}`}>{c.status || '—'}</span>
                    </td>
                    <td className="px-5 py-3 text-slate-300">{fmtRupiah(c.current_price)}</td>
                    <td className={`px-5 py-3 font-semibold ${c.floating_pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{fmtPct(c.floating_pnl_pct)}</td>
                    <td className="px-5 py-3"><NexusBadge nexusSignal={c.nexus_signal} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}
