import { Link } from 'react-router-dom';
import { signalMetaFromScore } from '../lib/signal';
import { useChart } from '../hooks/useChart';

const STOCKS = [
  { t: 'ANTM', price: 1620, chg: 2.14, vol: 3.8, score: 91 },
  { t: 'BBRI', price: 4870, chg: 0.62, vol: 2.1, score: 84 },
  { t: 'ADRO', price: 2740, chg: 1.35, vol: 2.9, score: 73 },
  { t: 'ASII', price: 5125, chg: -0.39, vol: 1.6, score: 60 },
  { t: 'BMRI', price: 6350, chg: 0.79, vol: 1.4, score: 58 },
  { t: 'ICBP', price: 11025, chg: -0.22, vol: 1.1, score: 40 },
  { t: 'TLKM', price: 2980, chg: 0.34, vol: 0.9, score: 31 },
  { t: 'GOTO', price: 68, chg: -1.45, vol: 0.7, score: 20 },
  { t: 'UNVR', price: 3560, chg: -0.11, vol: 0.5, score: 11 },
  { t: 'MDKA', price: 1780, chg: 1.02, vol: 1.8, score: 49 },
];

const EVENTS = [
  { time: '14:30', flag: '🇺🇸', name: 'US Non-Farm Payrolls', impact: 'High' },
  { time: '16:00', flag: '🇨🇳', name: 'China PMI Manufacturing', impact: 'Med' },
  { time: '19:00', flag: '🇮🇩', name: 'BI 7-Day Reverse Repo Rate', impact: 'High' },
  { time: '20:30', flag: '🇪🇺', name: 'ECB Rate Decision', impact: 'Med' },
];
const IMPACT_CLASS = { High: 'bg-strong/10 text-strong border-strong/30', Med: 'bg-moderate/10 text-moderate border-moderate/30', Low: 'bg-weak/10 text-weak border-weak/30' };

const IHSG_MINI_CONFIG = {
  type: 'line',
  data: {
    labels: Array.from({ length: 20 }, (_, i) => i),
    datasets: [{
      data: [7340, 7352, 7338, 7360, 7375, 7368, 7390, 7402, 7385, 7398, 7410, 7395, 7405, 7418, 7400, 7412, 7420, 7408, 7415, 7413],
      borderColor: '#06B6D4', borderWidth: 2, pointRadius: 0, tension: 0.35, fill: true,
      backgroundColor: (ctx) => { const g = ctx.chart.ctx.createLinearGradient(0, 0, 0, 64); g.addColorStop(0, 'rgba(6,182,212,0.25)'); g.addColorStop(1, 'rgba(6,182,212,0)'); return g; },
    }],
  },
  options: { responsive: false, plugins: { legend: { display: false }, tooltip: { enabled: false } }, scales: { x: { display: false }, y: { display: false } }, elements: { line: { borderJoinStyle: 'round' } } },
};

export default function Dashboard() {
  const ihsgRef = useChart(IHSG_MINI_CONFIG);
  const top5 = [...STOCKS].sort((a, b) => b.score - a.score).slice(0, 5);

  return (
    <>
      <header className="sticky top-0 z-10 bg-[#0B0F1A]/90 backdrop-blur border-b border-border px-8 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight">Dashboard</h1>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">Dummy data — belum disambungin ke backend</p>
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
            <p className="text-xs uppercase tracking-widest text-cyan font-semibold mb-1">Market Mood</p>
            <div className="flex items-center gap-3">
              <h2 className="text-3xl font-extrabold text-white">Cautiously Bullish</h2>
              <span className="text-2xl">📈</span>
            </div>
            <p className="text-sm text-slate-400 mt-1.5 max-w-md">Accumulation breadth improving across banking & energy sectors. Foreign flow turned net buy for 3rd session.</p>
          </div>
          <div className="relative flex items-center gap-8 pr-4">
            <div className="text-right">
              <p className="text-[11px] text-slate-500 font-mono">IHSG</p>
              <p className="text-2xl font-bold text-white font-mono">7,412.85</p>
              <p className="text-sm font-semibold text-emerald-400 font-mono">+0.86%</p>
            </div>
            <canvas ref={ihsgRef} width="160" height="64"></canvas>
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-bold text-white tracking-tight">Top Signals Today</h3>
            <Link to="/scanner" className="text-xs text-cyan hover:text-accent font-medium">View all in Scanner →</Link>
          </div>
          <div className="grid grid-cols-5 gap-4">
            {top5.map((s) => {
              const m = signalMetaFromScore(s.score);
              return (
                <div key={s.t} className="glow-border rounded-2xl bg-card border border-border p-4 hover:-translate-y-0.5 transition duration-200 cursor-pointer">
                  <div className="flex items-center justify-between mb-3">
                    <span className="font-mono font-bold text-white text-[15px]">{s.t}</span>
                    <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${m.cls}`}>{m.dot} {m.label}</span>
                  </div>
                  <p className="text-xl font-extrabold text-white font-mono">{s.score}<span className="text-xs text-slate-500 font-medium">/100</span></p>
                  <div className="w-full h-1.5 rounded-full bg-white/5 mt-2 overflow-hidden">
                    <div className="h-full rounded-full bg-gradient-to-r from-accent to-cyan" style={{ width: `${s.score}%` }}></div>
                  </div>
                  <p className="text-[11px] text-slate-500 mt-2 font-mono">Rp {s.price.toLocaleString('id-ID')} <span className={s.chg >= 0 ? 'text-emerald-400' : 'text-red-400'}>{s.chg >= 0 ? '+' : ''}{s.chg}%</span></p>
                </div>
              );
            })}
          </div>
        </div>

        <div className="grid grid-cols-3 gap-6">
          <div className="col-span-2 glow-border rounded-2xl bg-card border border-border p-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-bold text-white tracking-tight">Watchlist Summary</h3>
              <span className="text-[11px] text-slate-500 font-mono">10 tickers</span>
            </div>
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
                  {STOCKS.map((s) => {
                    const m = signalMetaFromScore(s.score);
                    return (
                      <tr key={s.t} className="hover:bg-white/[0.03] transition">
                        <td className="py-2.5 font-semibold text-white">{s.t}</td>
                        <td className="py-2.5 text-slate-300">{s.price.toLocaleString('id-ID')}</td>
                        <td className={`py-2.5 ${s.chg >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{s.chg >= 0 ? '+' : ''}{s.chg}%</td>
                        <td className="py-2.5 text-slate-300">{s.vol}x</td>
                        <td className="py-2.5 text-white font-semibold">{s.score}</td>
                        <td className="py-2.5"><span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${m.cls}`}>{m.dot} {m.label}</span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <div className="glow-border rounded-2xl bg-card border border-border p-5 flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-bold text-white tracking-tight">Upcoming Events</h3>
              <Link to="/market-events" className="text-xs text-cyan hover:text-accent font-medium">Calendar →</Link>
            </div>
            <div className="space-y-3 flex-1">
              {EVENTS.map((e, i) => (
                <div key={i} className="flex items-center justify-between py-2 border-b border-border/60 last:border-0">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <span className="text-lg">{e.flag}</span>
                    <div className="min-w-0">
                      <p className="text-[13px] text-slate-200 font-medium truncate">{e.name}</p>
                      <p className="text-[11px] text-slate-500 font-mono">{e.time} WIB</p>
                    </div>
                  </div>
                  <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border shrink-0 ${IMPACT_CLASS[e.impact]}`}>{e.impact}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
