import { useEffect, useRef, useState } from 'react';
import { API_BASE } from '../lib/api';

const SOURCE_LABEL = { swing: 'Swing', bpjs: 'BPJS', bsjp: 'BSJP' };
const STATUS_META = {
  waiting_entry: { label: 'Nunggu Entry', cls: 'bg-white/5 text-slate-400 border-border' },
  open: { label: 'Jalan', cls: 'bg-cyan/10 text-cyan border-cyan/30' },
  tp_hit: { label: 'TP Kena', cls: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' },
  sl_hit: { label: 'SL Kena', cls: 'bg-strong/10 text-strong border-strong/30' },
  timeout: { label: 'Timeout', cls: 'bg-moderate/10 text-moderate border-moderate/30' },
  missed: { label: 'Kelewat', cls: 'bg-white/5 text-slate-500 border-border' },
  invalidated: { label: 'Dicabut', cls: 'bg-orange-500/10 text-orange-400 border-orange-500/30' },
};

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('id-ID', { day: '2-digit', month: 'short', year: 'numeric' });
}
function fmtRp(v) {
  return v == null ? '—' : `Rp${Number(v).toLocaleString('id-ID')}`;
}

// cache domain logo per ticker di luar komponen — polling 60 detik gak perlu
// nembak /logo ulang tiap kali, domain website perusahaan gak berubah-ubah
const logoDomainCache = new Map();

function TickerLogo({ ticker }) {
  const [domain, setDomain] = useState(() => logoDomainCache.get(ticker) ?? undefined);
  const [broken, setBroken] = useState(false);

  useEffect(() => {
    if (logoDomainCache.has(ticker)) return;
    fetch(`${API_BASE}/scanner/${ticker}/logo`)
      .then((r) => (r.ok ? r.json() : { domain: null }))
      .then(({ domain: d }) => { logoDomainCache.set(ticker, d); setDomain(d); })
      .catch(() => { logoDomainCache.set(ticker, null); setDomain(null); });
  }, [ticker]);

  if (domain && !broken) {
    return (
      <img src={`https://logo.clearbit.com/${domain}?size=64`} alt={ticker} onError={() => setBroken(true)}
        className="w-10 h-10 rounded-full bg-white object-contain p-1.5 shrink-0" />
    );
  }
  return (
    <div className="w-10 h-10 rounded-full bg-accent/15 text-accent flex items-center justify-center text-[11px] font-extrabold font-mono shrink-0">
      {ticker.slice(0, 4)}
    </div>
  );
}

const RUNNING_STATUSES = new Set(['waiting_entry', 'open']);
const HIDE_COMPLETED_KEY = 'nexus_history_hide_completed';

export default function HistoryNexus() {
  const [rows, setRows] = useState(null);
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);
  const [sourceFilter, setSourceFilter] = useState('all');
  // default ON — user komplain kebanjiran card call yang udah selesai
  // (TP/SL/timeout), susah nemu yang masih jalan di antara tumpukan lama
  const [hideCompleted, setHideCompleted] = useState(() => {
    try { return localStorage.getItem(HIDE_COMPLETED_KEY) !== 'false'; } catch { return true; }
  });

  function toggleHideCompleted() {
    setHideCompleted((v) => {
      const next = !v;
      try { localStorage.setItem(HIDE_COMPLETED_KEY, String(next)); } catch { /* private window dst, gak fatal */ }
      return next;
    });
  }

  const reqId = useRef(0);

  useEffect(() => {
    function load() {
      // tag tiap tick polling — kalau response tick LAMA baru resolve SETELAH
      // tick BARU (network reorder/slow response), jangan biarin nimpa data
      // yang udah lebih fresh
      const id = ++reqId.current;
      fetch(`${API_BASE}/signal-track/history`)
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then(({ data, warning }) => {
          if (id !== reqId.current) return;
          if (warning) setError(warning); else { setError(null); setRows(data); }
        })
        .catch(() => { if (id === reqId.current) setError('Gak bisa konek ke backend.'); });
      fetch(`${API_BASE}/signal-track/stats`).then((r) => (r.ok ? r.json() : Promise.reject()))
        .then((json) => { if (id === reqId.current) setStats(json); })
        .catch(() => { if (id === reqId.current) setStats(null); });
    }
    load();
    // polling 60 detik — call baru (Swing/BPJS/BSJP) langsung ke-insert signal_alerts
    // bareng pas Telegram kekirim, halaman ini tinggal narik ulang biar user gak
    // perlu reload manual tiap kali ada call baru masuk
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  const filtered = rows
    ? rows
        .filter((r) => sourceFilter === 'all' || r.source === sourceFilter)
        .filter((r) => !hideCompleted || RUNNING_STATUSES.has(r.status))
    : null;

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

        <div>
          <div className="flex items-center justify-between gap-3 mb-4 flex-wrap">
            <div className="flex items-center gap-1">
              {[['all', 'Semua'], ['swing', 'Swing'], ['bpjs', 'BPJS'], ['bsjp', 'BSJP']].map(([key, label]) => (
                <button key={key} onClick={() => setSourceFilter(key)}
                  className={`text-[11px] font-semibold px-3 py-1.5 rounded-lg border transition ${sourceFilter === key ? 'bg-accent/10 text-accent border-accent/30' : 'bg-white/5 text-slate-500 border-border hover:text-white'}`}>
                  {label}
                </button>
              ))}
            </div>
            <button onClick={toggleHideCompleted}
              className="flex items-center gap-2 text-[11px] font-semibold text-slate-400 hover:text-white transition">
              Sembunyikan yang Selesai (TP/SL/Timeout)
              <span className={`relative inline-flex h-5 w-9 items-center rounded-full transition ${hideCompleted ? 'bg-accent' : 'bg-white/10'}`}>
                <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition ${hideCompleted ? 'translate-x-4' : 'translate-x-0.5'}`} />
              </span>
            </button>
          </div>

          {!filtered && !error && <p className="text-center text-slate-500 py-8">Memuat...</p>}
          {filtered && filtered.length === 0 && (
            <p className="text-center text-slate-500 py-8">
              {hideCompleted ? 'Gak ada call yang lagi jalan/nunggu entry saat ini.' : 'Belum ada call tercatat.'}
            </p>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {filtered?.map((r, i) => {
              const meta = STATUS_META[r.status] || { label: r.status, cls: 'bg-white/5 text-slate-500 border-border' };
              return (
                <div key={i} className="glow-border rounded-2xl bg-card border border-border p-4 flex flex-col gap-3">
                  <div className="flex items-center gap-3">
                    <TickerLogo ticker={r.ticker} />
                    <div className="min-w-0">
                      <p className="text-white font-mono font-bold truncate">{r.ticker}</p>
                      <p className="text-[11px] text-slate-500">{SOURCE_LABEL[r.source] || r.source}</p>
                    </div>
                    <span className={`ml-auto text-[10px] font-semibold px-2 py-0.5 rounded-full border whitespace-nowrap ${meta.cls}`}>{meta.label}</span>
                  </div>

                  <div className="grid grid-cols-3 gap-2 text-center">
                    <div>
                      <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-0.5">Entry</p>
                      <p className="text-xs font-mono text-slate-300">{fmtRp(r.entry_price)}</p>
                    </div>
                    <div>
                      <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-0.5">Target</p>
                      <p className="text-xs font-mono text-emerald-400">{fmtRp(r.target)}</p>
                    </div>
                    <div>
                      <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-0.5">SL</p>
                      <p className="text-xs font-mono text-red-400">{fmtRp(r.stop_loss)}</p>
                    </div>
                  </div>

                  <div className="flex items-center justify-between pt-2 border-t border-border/50 text-xs">
                    <span className="text-slate-500 font-mono">{fmtDate(r.alerted_at)}</span>
                    <span className={`font-mono font-semibold ${r.outcome_pct == null ? 'text-slate-500' : r.outcome_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {r.outcome_pct == null ? '—' : `${r.outcome_pct >= 0 ? '+' : ''}${r.outcome_pct}%`}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </>
  );
}
