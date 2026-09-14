import { useEffect, useRef, useState } from 'react';
import { API_BASE } from '../lib/api';

const SOURCE_LABEL = { bpjs: 'BPJS', sekuritas: 'Sekuritas' };
const STATUS_META = {
  observing: { label: 'Diamati', cls: 'bg-cyan/10 text-cyan border-cyan/30' },
  promoted: { label: 'Confirmed', cls: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' },
  dropped: { label: 'Drop', cls: 'bg-white/5 text-slate-500 border-border' },
};
const TREND_LABEL = {
  akumulasi_meningkat: 'Akumulasi Meningkat', akumulasi_melambat: 'Akumulasi Melambat',
  distribusi_meningkat: 'Distribusi Meningkat', netral: 'Netral',
};

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('id-ID', { day: '2-digit', month: 'short', year: 'numeric' });
}

export default function BrokerWatchlist() {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [statusFilter, setStatusFilter] = useState('observing');
  const reqId = useRef(0);

  useEffect(() => {
    function load() {
      const id = ++reqId.current;
      fetch(`${API_BASE}/signal-track/broker-watchlist`)
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then(({ data, warning }) => {
          if (id !== reqId.current) return;
          if (warning) setError(warning); else { setError(null); setRows(data); }
        })
        .catch(() => { if (id === reqId.current) setError('Gak bisa konek ke backend.'); });
    }
    load();
    // nightly job jalan 1x/hari (19:00 WIB) — polling 5 menit cukup, bukan real-time
    const id = setInterval(load, 5 * 60_000);
    return () => clearInterval(id);
  }, []);

  const filtered = rows ? rows.filter((r) => statusFilter === 'all' || r.status === statusFilter) : null;

  return (
    <>
      <header className="sticky top-0 z-10 bg-[#0B0F1A]/90 backdrop-blur border-b border-border px-8 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight">Observasi Broker</h1>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">Kandidat BPJS/sekuritas yang gagal guard SL/RR, dipantau jangka panjang</p>
        </div>
      </header>

      <div className="p-8 space-y-6">
        {error && (
          <div className="p-4 rounded-xl bg-strong/10 border border-strong/30 text-sm text-strong">{error}</div>
        )}
        <p className="text-[11px] text-slate-500">
          Dicek ulang 1x/hari (19:00 WIB) pake data broker beneran (Invezgo, window 90 hari) — "Confirmed" berarti
          akumulasi masih konsisten &amp; sideways terjaga, bukan call TP/SL siap pakai, cuma heads-up buat dicek manual.
          "Drop" ditutup diam-diam kalau ternyata distribusi atau lewat 120 hari tanpa kepastian.
        </p>

        <div className="glow-border rounded-2xl bg-card border border-border overflow-hidden">
          <div className="flex items-center gap-1 p-4 border-b border-border">
            {[['observing', 'Diamati'], ['promoted', 'Confirmed'], ['dropped', 'Drop'], ['all', 'Semua']].map(([key, label]) => (
              <button key={key} onClick={() => setStatusFilter(key)}
                className={`text-[11px] font-semibold px-3 py-1.5 rounded-lg border transition ${statusFilter === key ? 'bg-accent/10 text-accent border-accent/30' : 'bg-white/5 text-slate-500 border-border hover:text-white'}`}>
                {label}
              </button>
            ))}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider text-slate-500 border-b border-border bg-white/[0.02]">
                  <th className="px-5 py-3 font-medium">Sejak</th>
                  <th className="px-5 py-3 font-medium">Ticker</th>
                  <th className="px-5 py-3 font-medium">Sumber</th>
                  <th className="px-5 py-3 font-medium">Alasan Awal</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="px-5 py-3 font-medium">Broker Top</th>
                  <th className="px-5 py-3 font-medium text-right">Konsisten</th>
                  <th className="px-5 py-3 font-medium">Trend</th>
                  <th className="px-5 py-3 font-medium">Cek Terakhir</th>
                </tr>
              </thead>
              <tbody>
                {!filtered && !error && (
                  <tr><td colSpan={9} className="px-5 py-8 text-center text-slate-500">Memuat...</td></tr>
                )}
                {filtered && filtered.length === 0 && (
                  <tr><td colSpan={9} className="px-5 py-8 text-center text-slate-500">Belum ada kandidat di kategori ini.</td></tr>
                )}
                {filtered?.map((r) => {
                  const meta = STATUS_META[r.status] || { label: r.status, cls: 'bg-white/5 text-slate-500 border-border' };
                  const bandar = r.last_bandar;
                  return (
                    <tr key={r.ticker} className="hover:bg-white/[0.03] transition border-t border-border/50">
                      <td className="px-5 py-3 text-slate-400 font-mono whitespace-nowrap">{fmtDate(r.added_at)}</td>
                      <td className="px-5 py-3 text-white font-mono font-semibold">{r.ticker}</td>
                      <td className="px-5 py-3 text-slate-300">{SOURCE_LABEL[r.source] || r.source}</td>
                      <td className="px-5 py-3 text-slate-400 max-w-xs">{r.reason}</td>
                      <td className="px-5 py-3"><span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${meta.cls}`}>{meta.label}</span></td>
                      <td className="px-5 py-3 text-slate-300 font-mono">{bandar?.broker || '—'}</td>
                      <td className="px-5 py-3 text-right font-mono text-slate-300">{bandar?.consistency_pct != null ? `${bandar.consistency_pct}%` : '—'}</td>
                      <td className="px-5 py-3 text-slate-400">{bandar?.trend ? (TREND_LABEL[bandar.trend] || bandar.trend) : '—'}</td>
                      <td className="px-5 py-3 text-slate-500 font-mono whitespace-nowrap">{fmtDate(r.last_checked_at)}</td>
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
