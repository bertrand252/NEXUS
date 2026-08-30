import { useEffect, useState } from 'react';
import { API_BASE } from '../lib/api';
import { useChart } from '../hooks/useChart';

function fmtRupiah(n) {
  const abs = Math.abs(n);
  const val = abs >= 1_000_000 ? (abs / 1_000_000).toFixed(1) + 'jt' : abs.toLocaleString('id-ID');
  return `${n >= 0 ? '+' : '−'}Rp ${val}`;
}

function StatCard({ label, value, valueClass, badge, badgeClass }) {
  return (
    <div className="glow-border rounded-2xl bg-card border border-border p-4">
      <p className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold mb-2">{label}</p>
      <p className={`text-xl font-extrabold font-mono ${valueClass || ''}`}>{value}</p>
      <span className={`inline-block mt-2 text-[10px] font-semibold px-2 py-0.5 rounded-full border ${badgeClass || ''}`}>{badge}</span>
    </div>
  );
}

export default function Analytics() {
  const [viewYear, setViewYear] = useState(new Date().getFullYear());
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);
  const [signalStats, setSignalStats] = useState(null);
  const [mentorScoreboard, setMentorScoreboard] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setError(false);
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/journal/analytics?year=${viewYear}`);
        const json = await res.json();
        if (!cancelled) setData(json);
      } catch {
        if (!cancelled) setError(true);
      }
    })();
    return () => { cancelled = true; };
  }, [viewYear]);

  useEffect(() => {
    fetch(`${API_BASE}/signal-track/stats`).then((r) => (r.ok ? r.json() : Promise.reject())).then(setSignalStats).catch(() => setSignalStats(null));
    fetch(`${API_BASE}/mentor-calls/scoreboard`).then((r) => (r.ok ? r.json() : Promise.reject())).then(setMentorScoreboard).catch(() => setMentorScoreboard(null));
  }, []);

  const monthlyConfig = data ? {
    type: 'line',
    data: {
      labels: data.monthly_pnl.map((m) => m.month),
      datasets: [{
        data: data.monthly_pnl.map((m) => m.net / 1_000_000),
        borderColor: '#06B6D4', borderWidth: 2, pointRadius: 4, pointBackgroundColor: '#06B6D4', tension: 0.3, fill: true,
        backgroundColor: (ctx) => { const g = ctx.chart.ctx.createLinearGradient(0, 0, 0, 180); g.addColorStop(0, 'rgba(6,182,212,0.2)'); g.addColorStop(1, 'rgba(6,182,212,0)'); return g; },
      }],
    },
    options: { plugins: { legend: { display: false } }, scales: { x: { grid: { color: '#1F2937' }, ticks: { color: '#64748B', font: { size: 11 } } }, y: { grid: { color: '#1F2937' }, ticks: { color: '#64748B', font: { size: 10 }, callback: (v) => v + 'jt' } } } },
  } : null;

  const yearlyConfig = data ? (() => {
    const yc = data.yearly_comparison.length ? data.yearly_comparison : [{ year: viewYear, total: 0 }];
    return {
      type: 'bar',
      data: { labels: yc.map((y) => String(y.year)), datasets: [{ data: yc.map((y) => y.total / 1_000_000), backgroundColor: '#2563EB', borderRadius: 6 }] },
      options: { plugins: { legend: { display: false } }, scales: { x: { grid: { display: false }, ticks: { color: '#94A3B8' } }, y: { grid: { color: '#1F2937' }, ticks: { color: '#64748B', font: { size: 10 }, callback: (v) => v + 'jt' } } } },
    };
  })() : null;

  const winRateConfig = data ? {
    type: 'doughnut',
    data: { labels: ['Win', 'Loss'], datasets: [{ data: [data.wins, data.losses], backgroundColor: ['#10B981', '#EF4444'], borderWidth: 0 }] },
    options: { plugins: { legend: { position: 'bottom', labels: { color: '#94A3B8', font: { size: 11 } } } }, cutout: '70%' },
  } : null;

  const monthlyRef = useChart(monthlyConfig);
  const yearlyRef = useChart(yearlyConfig);
  const winRateRef = useChart(winRateConfig);

  return (
    <>
      <header className="sticky top-0 z-10 bg-[#0B0F1A]/90 backdrop-blur border-b border-border px-8 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight">Analytics</h1>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">Performance Recap</p>
        </div>
        <div className="no-print flex items-center gap-4">
          <span className="flex items-center gap-2 text-xs font-semibold px-3 py-1.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span> MARKET OPEN
          </span>
          <button onClick={() => window.print()} className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg bg-card border border-border hover:border-accent/50 hover:text-white text-slate-300 transition">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M6 9V2h12v7M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2M6 14h12v8H6z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
            Export PDF
          </button>
          <button className="relative w-9 h-9 rounded-lg bg-card border border-border flex items-center justify-center hover:border-accent/50 transition">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M18 8A6 6 0 0 0 6 8C6 15 3 17 3 17H21S18 15 18 8Z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /><path d="M13.73 21A2 2 0 0 1 10.27 21" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
            <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-strong"></span>
          </button>
        </div>
      </header>

      <div className="p-8 space-y-6">
        {error && (
          <div className="mb-4 p-4 rounded-xl bg-strong/10 border border-strong/30 text-sm text-strong">
            Gak bisa konek ke backend. Pastikan uvicorn jalan di {API_BASE}.
          </div>
        )}

        <div className="grid grid-cols-2 md:grid-cols-5 gap-5">
          <StatCard
            label="Total P&L"
            value={data ? fmtRupiah(data.total_pnl) : '—'}
            valueClass={data ? (data.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400') : ''}
            badge={data ? `${data.wins + data.losses} trades tahun ini` : '—'}
            badgeClass={data ? (data.total_pnl >= 0 ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' : 'bg-strong/10 text-strong border-strong/30') : ''}
          />
          <StatCard
            label="Win Rate"
            value={data ? `${data.win_rate}%` : '—'}
            valueClass="text-white"
            badge={data ? `${data.wins}W / ${data.losses}L` : '—'}
            badgeClass="bg-accent/10 text-accent border-accent/30"
          />
          <StatCard
            label="Best Month"
            value={data?.best_month ? `${data.best_month.month} '${String(viewYear).slice(2)}` : '—'}
            valueClass="text-emerald-400"
            badge={data?.best_month ? fmtRupiah(data.best_month.net) : 'Belum ada data'}
            badgeClass="bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
          />
          <StatCard
            label="Worst Month"
            value={data?.worst_month ? `${data.worst_month.month} '${String(viewYear).slice(2)}` : '—'}
            valueClass="text-red-400"
            badge={data?.worst_month ? fmtRupiah(data.worst_month.net) : 'Belum ada data'}
            badgeClass="bg-strong/10 text-strong border-strong/30"
          />
          <StatCard
            label="Most Traded"
            value={data?.most_traded ? data.most_traded.emiten : '—'}
            valueClass="text-white"
            badge={data?.most_traded ? `${data.most_traded.count} trades` : 'Belum ada data'}
            badgeClass="bg-cyan/10 text-cyan border-cyan/30"
          />
        </div>

        <div className="glow-border rounded-2xl bg-card border border-border p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-bold text-white tracking-tight">Monthly P&L — <span>{viewYear}</span></h3>
            <div className="flex items-center gap-2">
              <button onClick={() => setViewYear((y) => y - 1)} className="w-7 h-7 rounded-lg bg-card2 border border-border flex items-center justify-center text-slate-400 hover:text-white transition text-xs">‹</button>
              <button onClick={() => setViewYear((y) => y + 1)} className="w-7 h-7 rounded-lg bg-card2 border border-border flex items-center justify-center text-slate-400 hover:text-white transition text-xs">›</button>
            </div>
          </div>
          <canvas ref={monthlyRef} height="90"></canvas>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="glow-border rounded-2xl bg-card border border-border p-5">
            <h3 className="text-sm font-bold text-white tracking-tight mb-4">Yearly Comparison</h3>
            <canvas ref={yearlyRef} height="140"></canvas>
          </div>
          <div className="glow-border rounded-2xl bg-card border border-border p-5 flex flex-col items-center justify-center">
            <h3 className="text-sm font-bold text-white tracking-tight self-start mb-2">Win / Loss Ratio</h3>
            <canvas ref={winRateRef} height="160"></canvas>
          </div>
        </div>

        <div className="glow-border rounded-2xl bg-card border border-border p-5">
          <h3 className="text-sm font-bold text-white tracking-tight mb-1">Mentor vs NEXUS — Track Record</h3>
          <p className="text-[11px] text-slate-500 mb-4">Win rate real dari alert Telegram (TP vs SL) dan call mentor (floating PnL), bukan asumsi</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            <div className="rounded-xl border border-border bg-card2 p-4">
              <p className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold mb-2">NEXUS Win Rate</p>
              {signalStats?.warning || signalStats?.win_rate_pct == null ? (
                <p className="text-sm text-slate-500">Belum cukup data — nunggu alert Telegram ngumpul.</p>
              ) : (
                <>
                  <p className="text-2xl font-extrabold font-mono text-white">{signalStats.win_rate_pct}%</p>
                  <p className="text-xs text-slate-500 mt-1 font-mono">{signalStats.tp_hit} TP · {signalStats.sl_hit} SL · {signalStats.open} masih jalan</p>
                </>
              )}
            </div>
            <div className="rounded-xl border border-border bg-card2 p-4">
              <p className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold mb-2">Mentor Win Rate</p>
              {mentorScoreboard?.warning || mentorScoreboard?.win_rate_pct == null ? (
                <p className="text-sm text-slate-500">Belum cukup data — refresh Mentor Calls dulu.</p>
              ) : (
                <>
                  <p className="text-2xl font-extrabold font-mono text-white">{mentorScoreboard.win_rate_pct}%</p>
                  <p className="text-xs text-slate-500 mt-1 font-mono">dari {mentorScoreboard.total} call aktif</p>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
