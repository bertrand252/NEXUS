import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { API_BASE } from '../lib/api';
import { signalMeta } from '../lib/signal';
import { IMPACT_BADGE_CLASS, formatShortDate } from '../lib/events';
import { useChart } from '../hooks/useChart';

function ihsgChartConfig(spark) {
  if (!spark) return null;
  return {
    type: 'line',
    data: {
      labels: spark.map((_, i) => i),
      datasets: [{
        data: spark, borderColor: '#06B6D4', borderWidth: 2, pointRadius: 0, tension: 0.35, fill: true,
        backgroundColor: (ctx) => { const g = ctx.chart.ctx.createLinearGradient(0, 0, 0, 64); g.addColorStop(0, 'rgba(6,182,212,0.25)'); g.addColorStop(1, 'rgba(6,182,212,0)'); return g; },
      }],
    },
    options: { responsive: false, plugins: { legend: { display: false }, tooltip: { enabled: false } }, scales: { x: { display: false }, y: { display: false } }, elements: { line: { borderJoinStyle: 'round' } } },
  };
}

const SENTIMENT_CLASS = {
  bullish: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
  bearish: 'bg-strong/10 text-strong border-strong/30',
  neutral: 'bg-slate-500/10 text-slate-400 border-slate-500/30',
  mixed: 'bg-moderate/10 text-moderate border-moderate/30',
};

const HEATMAP_CLASS = (avgScore) => {
  if (avgScore >= 60) return 'bg-strong/20 border-strong/40 text-strong';
  if (avgScore >= 45) return 'bg-moderate/20 border-moderate/40 text-moderate';
  if (avgScore >= 30) return 'bg-cyan/10 border-cyan/30 text-cyan';
  return 'bg-white/5 border-border text-slate-500';
};

export default function Dashboard() {
  const [scanner, setScanner] = useState(null);
  const [scannerError, setScannerError] = useState(false);
  const [events, setEvents] = useState(null);
  const [ihsg, setIhsg] = useState(null);
  const [briefing, setBriefing] = useState(null);
  const [briefingWarning, setBriefingWarning] = useState(null);
  const [refreshingBriefing, setRefreshingBriefing] = useState(false);
  const [watchlist, setWatchlist] = useState(null);
  const [sectorHeatmap, setSectorHeatmap] = useState(null);

  function loadBriefing() {
    fetch(`${API_BASE}/daily-briefing`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(({ data, warning }) => { setBriefing(data); setBriefingWarning(warning); })
      .catch(() => setBriefingWarning('Gak bisa konek ke backend.'));
  }

  async function runRefreshBriefing() {
    setRefreshingBriefing(true);
    try {
      await fetch(`${API_BASE}/daily-briefing/refresh`, { method: 'POST' });
      loadBriefing();
    } catch (err) {
      alert('Gagal generate briefing: ' + err.message);
    } finally {
      setRefreshingBriefing(false);
    }
  }

  useEffect(() => {
    fetch(`${API_BASE}/scanner`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(({ data }) => setScanner(data))
      .catch(() => setScannerError(true));

    fetch(`${API_BASE}/market-events`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(({ data }) => setEvents((data || []).slice(0, 4)))
      .catch(() => setEvents([]));

    fetch(`${API_BASE}/scanner/index/ihsg`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setIhsg)
      .catch(() => setIhsg(null));

    fetch(`${API_BASE}/watchlist`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(({ data }) => setWatchlist((data || []).map((w) => w.ticker)))
      .catch(() => setWatchlist([]));

    fetch(`${API_BASE}/scanner/sectors`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(({ data }) => setSectorHeatmap(data || []))
      .catch(() => setSectorHeatmap([]));

    loadBriefing();
  }, []);

  const ihsgRef = useChart(ihsgChartConfig(ihsg?.spark));
  const top5 = scanner ? [...scanner].sort((a, b) => b.total_score - a.total_score).slice(0, 5) : [];
  const watchlistRows = scanner && watchlist
    ? scanner.filter((s) => watchlist.includes(s.ticker)).sort((a, b) => b.total_score - a.total_score)
    : [];

  return (
    <>
      <header className="sticky top-0 z-10 bg-[#0B0F1A]/90 backdrop-blur border-b border-border px-8 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight">Dashboard</h1>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">Overview</p>
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

      <div className="p-8 space-y-6">
        <div className="glow-border rounded-2xl bg-gradient-to-r from-card via-card to-card2 border border-border p-6 flex items-center justify-between overflow-hidden relative">
          <div className="absolute -right-10 -top-10 w-56 h-56 rounded-full bg-accent/10 blur-3xl"></div>
          <div className="relative">
            <p className="text-xs uppercase tracking-widest text-cyan font-semibold mb-1">IHSG</p>
            <div className="flex items-center gap-3">
              <h2 className="text-3xl font-extrabold text-white">{ihsg?.price != null ? ihsg.price.toLocaleString('id-ID') : '—'}</h2>
              {ihsg && <span className={`text-sm font-semibold font-mono ${ihsg.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{ihsg.change_pct >= 0 ? '+' : ''}{ihsg.change_pct}%</span>}
            </div>
            {ihsg?.as_of && <p className="text-[11px] text-slate-500 font-mono mt-1">Closing per {formatShortDate(ihsg.as_of)} — yfinance kadang telat update</p>}
          </div>
          <div className="relative flex items-center gap-8 pr-4">
            <canvas ref={ihsgRef} width="160" height="64"></canvas>
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-bold text-white tracking-tight">Top Signals Today</h3>
            <Link to="/scanner" className="text-xs text-cyan hover:text-accent font-medium">View all in Scanner →</Link>
          </div>
          {scannerError && <p className="text-sm text-slate-500">Gak bisa konek ke backend ({API_BASE}).</p>}
          <div className="grid grid-cols-5 gap-4">
            {top5.map((s) => {
              const m = signalMeta(s.signal);
              return (
                <Link key={s.ticker} to={`/stock-detail?t=${s.ticker}`} className="glow-border rounded-2xl bg-card border border-border p-4 hover:-translate-y-0.5 transition duration-200 cursor-pointer block">
                  <div className="flex items-center justify-between mb-3">
                    <span className="font-mono font-bold text-white text-[15px]">{s.ticker}</span>
                    <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${m.cls}`}>{m.dot} {m.label}</span>
                  </div>
                  <p className="text-xl font-extrabold text-white font-mono">{s.total_score}<span className="text-xs text-slate-500 font-medium">/100</span></p>
                  <div className="w-full h-1.5 rounded-full bg-white/5 mt-2 overflow-hidden">
                    <div className="h-full rounded-full bg-gradient-to-r from-accent to-cyan" style={{ width: `${s.total_score}%` }}></div>
                  </div>
                  <p className="text-[11px] text-slate-500 mt-2 font-mono">Rp {s.price.toLocaleString('id-ID')} <span className={s.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}>{s.change_pct >= 0 ? '+' : ''}{s.change_pct}%</span></p>
                </Link>
              );
            })}
          </div>
        </div>

        <div className="glow-border rounded-2xl bg-card border border-border p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-bold text-white tracking-tight">Sector Rotation</h3>
            <span className="text-[11px] text-slate-500 font-mono">Sektor mana lagi rame Strong signal</span>
          </div>
          {sectorHeatmap?.length === 0 && <p className="text-sm text-slate-500 py-2">Cache scanner kosong — refresh dulu di Scanner.</p>}
          {sectorHeatmap?.length > 0 && (
            <div className="grid grid-cols-4 gap-3">
              {sectorHeatmap.map((s) => (
                <div key={s.sector} className={`rounded-xl border p-3 ${HEATMAP_CLASS(s.avg_score)}`}>
                  <p className="text-xs font-semibold leading-tight">{s.sector}</p>
                  <p className="text-lg font-extrabold font-mono mt-1">{s.strong_count}</p>
                  <p className="text-[10px] font-mono opacity-70">Strong · avg {s.avg_score}</p>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="glow-border rounded-2xl bg-card border border-border p-5">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2.5">
              <h3 className="text-sm font-bold text-white tracking-tight">AI Daily Briefing</h3>
              {briefing && (
                <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${SENTIMENT_CLASS[briefing.market_sentiment] || SENTIMENT_CLASS.neutral}`}>
                  {briefing.market_sentiment}
                </span>
              )}
            </div>
            <button onClick={runRefreshBriefing} disabled={refreshingBriefing} className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-cyan/10 text-cyan border border-cyan/30 hover:bg-cyan/20 transition disabled:opacity-50">
              {refreshingBriefing ? 'Nge-generate...' : '↻ Generate ulang'}
            </button>
          </div>

          {briefingWarning && !briefing && <p className="text-sm text-slate-500">{briefingWarning}</p>}

          {briefing && (
            <div className="grid grid-cols-3 gap-6 mt-2">
              <div>
                <p className="text-sm text-slate-300 leading-relaxed mb-3">{briefing.ringkasan}</p>
                {['positive', 'negative', 'netral'].map((key) => {
                  const items = briefing.berita?.[key];
                  if (!items || items.length === 0) return null;
                  const cfg = {
                    positive: { label: '🟢 Positive', cls: 'text-emerald-400' },
                    negative: { label: '🔴 Negative', cls: 'text-strong' },
                    netral: { label: '⚪ Netral', cls: 'text-slate-400' },
                  }[key];
                  return (
                    <div key={key} className="mb-3">
                      <p className={`text-[11px] uppercase tracking-wider font-semibold mb-1.5 ${cfg.cls}`}>{cfg.label}</p>
                      <div className="space-y-1">
                        {items.map((it, i) => (
                          <p key={i} className="text-xs text-slate-400">
                            <span className="font-mono font-semibold text-white">{it.saham}</span>
                            {': '}{it.berita}
                          </p>
                        ))}
                      </div>
                    </div>
                  );
                })}
                {!briefing.berita && <p className="text-xs text-slate-500">Belum ada rincian berita per saham.</p>}
              </div>

              <div>
                <p className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold mb-2">Tanggal Penting</p>
                {(!briefing.tanggal_penting || briefing.tanggal_penting.length === 0) && <p className="text-xs text-slate-500">Gak ada event penting kesebut di berita terakhir.</p>}
                <div className="space-y-2">
                  {briefing.tanggal_penting?.map((e, i) => (
                    <div key={i} className="text-xs">
                      <span className="font-mono font-semibold text-white">{e.saham}</span>
                      <span className="text-slate-500"> — {e.jenis} · {e.tanggal}</span>
                      {e.detail && <p className="text-slate-500 mt-0.5 text-justify">{e.detail}</p>}
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <p className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold mb-2">Rekomendasi</p>
                {(!briefing.rekomendasi || briefing.rekomendasi.length === 0) && <p className="text-xs text-slate-500">Belum cukup data buat rekomendasi solid.</p>}
                <div className="space-y-2">
                  {briefing.rekomendasi?.map((r, i) => (
                    <div key={i} className="text-xs">
                      <span className="font-mono font-semibold text-white">{r.saham}</span>
                      <p className="text-slate-500 mt-0.5 text-justify">{r.alasan}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="grid grid-cols-3 gap-6">
          <div className="col-span-2 glow-border rounded-2xl bg-card border border-border p-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-bold text-white tracking-tight">Watchlist Summary</h3>
              <Link to="/settings" className="text-[11px] text-cyan hover:text-accent font-mono">Kelola watchlist →</Link>
            </div>
            {watchlist?.length === 0 && <p className="text-sm text-slate-500 py-4 text-center">Belum ada ticker di watchlist — tambahin di Settings.</p>}
            {watchlist?.length > 0 && (
            <div className="overflow-x-auto scrollbar-thin">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wider text-slate-500 border-b border-border">
                    <th className="pb-2 font-medium">Ticker</th>
                    <th className="pb-2 font-medium">Price</th>
                    <th className="pb-2 font-medium">Change</th>
                    <th className="pb-2 font-medium">Vol Ratio</th>
                    <th className="pb-2 font-medium">Score</th>
                    <th className="pb-2 font-medium">Signal</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/60 font-mono text-[13px]">
                  {watchlistRows.map((s) => {
                    const m = signalMeta(s.signal);
                    return (
                      <tr key={s.ticker} className="hover:bg-white/[0.03] transition">
                        <td className="py-2.5 font-semibold text-white">{s.ticker}</td>
                        <td className="py-2.5 text-slate-300">{s.price.toLocaleString('id-ID')}</td>
                        <td className={`py-2.5 ${s.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{s.change_pct >= 0 ? '+' : ''}{s.change_pct}%</td>
                        <td className="py-2.5 text-slate-300">{s.volume_ratio}x</td>
                        <td className="py-2.5 text-white font-semibold">{s.total_score}</td>
                        <td className="py-2.5"><span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${m.cls}`}>{m.dot} {s.signal === 'None' ? 'No Signal' : s.signal}</span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            )}
          </div>

          <div className="glow-border rounded-2xl bg-card border border-border p-5 flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-bold text-white tracking-tight">Upcoming Events</h3>
              <Link to="/market-events" className="text-xs text-cyan hover:text-accent font-medium">Calendar →</Link>
            </div>
            <div className="space-y-3 flex-1">
              {events == null && <p className="text-xs text-slate-500">Memuat...</p>}
              {events?.length === 0 && <p className="text-xs text-slate-500">Gak ada event minggu ini.</p>}
              {events?.map((e, i) => (
                <div key={i} className="flex items-center justify-between py-2 border-b border-border/60 last:border-0">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <span className="text-lg">{e.flag}</span>
                    <div className="min-w-0">
                      <p className="text-[13px] text-slate-200 font-medium truncate">{e.event}</p>
                      <p className="text-[11px] text-slate-500 font-mono">{e.time_wib} WIB</p>
                    </div>
                  </div>
                  <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border shrink-0 ${IMPACT_BADGE_CLASS[e.impact] || IMPACT_BADGE_CLASS.Low}`}>{e.impact}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
