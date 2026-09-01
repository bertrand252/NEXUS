import { useEffect, useState } from 'react';
import { API_BASE } from '../lib/api';

const SOURCE_LABEL = { swing: 'Swing', bpjs: 'BPJS', bsjp: 'BSJP' };
const STATUS_META = {
  waiting_entry: { label: 'Nunggu Entry', cls: 'bg-white/5 text-slate-400 border-border' },
  open: { label: 'Jalan', cls: 'bg-cyan/10 text-cyan border-cyan/30' },
  tp_hit: { label: 'TP Kena', cls: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' },
  sl_hit: { label: 'SL Kena', cls: 'bg-strong/10 text-strong border-strong/30' },
  timeout: { label: 'Timeout', cls: 'bg-moderate/10 text-moderate border-moderate/30' },
  missed: { label: 'Kelewat', cls: 'bg-white/5 text-slate-500 border-border' },
};

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('id-ID', { day: '2-digit', month: 'short', year: 'numeric' });
}
function fmtRp(v) {
  return v == null ? '—' : `Rp${Number(v).toLocaleString('id-ID')}`;
}

export default function HistoryNexus() {
  const [rows, setRows] = useState(null);
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);
  const [sourceFilter, setSourceFilter] = useState('all');

  useEffect(() => {
    function load() {
      fetch(`${API_BASE}/signal-track/history`)
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then(({ data, warning }) => { if (warning) setError(warning); else { setError(null); setRows(data); } })
        .catch(() => setError('Gak bisa konek ke backend.'));
      fetch(`${API_BASE}/signal-track/stats`).then((r) => (r.ok ? r.json() : Promise.reject())).then(setStats).catch(() => setStats(null));
    }
    load();
    // polling 60 detik — call baru (Swing/BPJS/BSJP) langsung ke-insert signal_alerts
    // bareng pas Telegram kekirim, halaman ini tinggal narik ulang biar user gak
    // perlu reload manual tiap kali ada call baru masuk
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  const filtered = rows ? rows.filter((r) => sourceFilter === 'all' || r.source === sourceFilter) : null;

  return (
    <>
      <header className="sticky top-0 z-10 bg-[#0B0F1A]/90 backdrop-blur border-b border-border px-8 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight">History NEXUS</h1>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">Semua call Telegram — akurasi & realized PnL</p>
        </div>
      </header>

      <div className="p-8 space-y-6">
        {error && (
          <div className="p-4 rounded-xl bg-strong/10 border border-strong/30 text-sm text-strong">{error}</div>
        )}

        <div className="grid grid-cols-2 md:grid-cols-4 gap-5">
          <div className="glow-border rounded-2xl bg-card border border-border p-4">
            <p className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold mb-2">Win Rate</p>
            <p className="text-2xl font-extrabold font-mono text-white">{stats?.win_rate_pct != null ? `${stats.win_rate_pct}%` : '—'}</p>
          </div>
          <div className="glow-border rounded-2xl bg-card border border-border p-4">
            <p className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold mb-2">TP / SL</p>
            <p className="text-2xl font-extrabold font-mono text-white">{stats ? `${stats.tp_hit} / ${stats.sl_hit}` : '—'}</p>
          </div>
          <div className="glow-border rounded-2xl bg-card border border-border p-4">
            <p className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold mb-2">Masih Jalan</p>
            <p className="text-2xl font-extrabold font-mono text-cyan">{stats ? stats.open : '—'}</p>
          </div>
          <div className="glow-border rounded-2xl bg-card border border-border p-4">
            <p className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold mb-2">Total Call</p>
            <p className="text-2xl font-extrabold font-mono text-white">{stats ? stats.total : '—'}</p>
          </div>
        </div>
        <p className="text-[11px] text-slate-500 -mt-3">
          Status dicek 1x/hari abis market tutup (harga closing kemarin), bukan real-time — win rate cuma ngitung
          call yang beneran kejalanin (TP/SL/timeout), bukan yang masih nunggu entry/kelewat.
        </p>

        <div className="glow-border rounded-2xl bg-card border border-border overflow-hidden">
          <div className="flex items-center gap-1 p-4 border-b border-border">
            {[['all', 'Semua'], ['swing', 'Swing'], ['bpjs', 'BPJS'], ['bsjp', 'BSJP']].map(([key, label]) => (
              <button key={key} onClick={() => setSourceFilter(key)}
                className={`text-[11px] font-semibold px-3 py-1.5 rounded-lg border transition ${sourceFilter === key ? 'bg-accent/10 text-accent border-accent/30' : 'bg-white/5 text-slate-500 border-border hover:text-white'}`}>
                {label}
              </button>
            ))}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider text-slate-500 border-b border-border bg-white/[0.02]">
                  <th className="px-5 py-3 font-medium">Tanggal</th>
                  <th className="px-5 py-3 font-medium">Ticker</th>
                  <th className="px-5 py-3 font-medium">Jenis</th>
                  <th className="px-5 py-3 font-medium text-right">Harga Beli</th>
                  <th className="px-5 py-3 font-medium text-right">Target</th>
                  <th className="px-5 py-3 font-medium text-right">Stop Loss</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="px-5 py-3 font-medium text-right">Harga Close</th>
                  <th className="px-5 py-3 font-medium text-right">Realized PnL</th>
                </tr>
              </thead>
              <tbody>
                {!filtered && !error && (
                  <tr><td colSpan={9} className="px-5 py-8 text-center text-slate-500">Memuat...</td></tr>
                )}
                {filtered && filtered.length === 0 && (
                  <tr><td colSpan={9} className="px-5 py-8 text-center text-slate-500">Belum ada call tercatat.</td></tr>
                )}
                {filtered?.map((r, i) => {
                  const meta = STATUS_META[r.status] || { label: r.status, cls: 'bg-white/5 text-slate-500 border-border' };
                  return (
                    <tr key={i} className="hover:bg-white/[0.03] transition border-t border-border/50">
                      <td className="px-5 py-3 text-slate-400 font-mono whitespace-nowrap">{fmtDate(r.alerted_at)}</td>
                      <td className="px-5 py-3 text-white font-mono font-semibold">{r.ticker}</td>
                      <td className="px-5 py-3 text-slate-300">{SOURCE_LABEL[r.source] || r.source}</td>
                      <td className="px-5 py-3 text-right font-mono text-slate-300">{fmtRp(r.entry_price)}</td>
                      <td className="px-5 py-3 text-right font-mono text-emerald-400">{fmtRp(r.target)}</td>
                      <td className="px-5 py-3 text-right font-mono text-red-400">{fmtRp(r.stop_loss)}</td>
                      <td className="px-5 py-3"><span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${meta.cls}`}>{meta.label}</span></td>
                      <td className="px-5 py-3 text-right font-mono text-slate-300">{fmtRp(r.close_price)}</td>
                      <td className={`px-5 py-3 text-right font-mono font-semibold ${r.outcome_pct == null ? 'text-slate-500' : r.outcome_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {r.outcome_pct == null ? '—' : `${r.outcome_pct >= 0 ? '+' : ''}${r.outcome_pct}%`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}
