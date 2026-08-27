import { useEffect, useState } from 'react';
import { API_BASE } from '../lib/api';
import { IMPACT_BADGE_CLASS } from '../lib/events';

function EventRow({ e }) {
  return (
    <tr className="hover:bg-white/[0.03] transition">
      <td className="px-5 py-3 text-slate-300 font-mono whitespace-nowrap">{e.date} {e.time_wib} WIB</td>
      <td className="px-5 py-3"><span className="inline-flex items-center gap-1.5 text-slate-300 font-mono">{e.flag} {e.currency}</span></td>
      <td className="px-5 py-3 text-slate-200 font-medium">{e.event}</td>
      <td className="px-5 py-3"><span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${IMPACT_BADGE_CLASS[e.impact] || IMPACT_BADGE_CLASS.Low}`}>{e.impact}</span></td>
      <td className="px-5 py-3 text-slate-400 text-[12px]">{e.idx_sector_impact}</td>
      <td className="px-5 py-3 text-right font-mono text-[12px] text-slate-400">{e.forecast || '—'} <span className="text-slate-600">vs</span> {e.previous || '—'}</td>
    </tr>
  );
}

export default function MarketEvents() {
  const [events, setEvents] = useState(null);
  const [warning, setWarning] = useState(null);
  const [error, setError] = useState(false);
  const [impactFilter, setImpactFilter] = useState('');
  const [currencyFilter, setCurrencyFilter] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/market-events`);
        const { data, warning } = await res.json();
        if (cancelled) return;
        if (warning) {
          setWarning(warning);
          return;
        }
        setEvents(data);
      } catch {
        if (!cancelled) setError(true);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const today = new Date().toISOString().slice(0, 10);
  const upcomingEvents = (events || []).filter((e) => e.date >= today);
  const currencyOptions = [...new Map(upcomingEvents.map((e) => [e.currency, e.flag])).entries()].sort(([a], [b]) => a.localeCompare(b));
  const filteredEvents = upcomingEvents.filter((e) =>
    (!impactFilter || e.impact === impactFilter) && (!currencyFilter || e.currency === currencyFilter)
  );

  return (
    <>
      <header className="sticky top-0 z-10 bg-[#0B0F1A]/90 backdrop-blur border-b border-border px-8 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight">Market Events</h1>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">Economic Calendar</p>
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
          <select value={impactFilter} onChange={(e) => setImpactFilter(e.target.value)} className="bg-card border border-border rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none focus:border-accent/60">
            <option value="">All Impact</option>
            <option value="High">High</option>
            <option value="Medium">Medium</option>
            <option value="Low">Low</option>
          </select>
          <select value={currencyFilter} onChange={(e) => setCurrencyFilter(e.target.value)} className="bg-card border border-border rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none focus:border-accent/60">
            <option value="">All Currencies</option>
            {currencyOptions.map(([code, flag]) => <option key={code} value={code}>{flag} {code}</option>)}
          </select>
          <span className="ml-auto text-xs text-slate-500 font-mono">This week</span>
        </div>

        <div className="glow-border rounded-2xl bg-card border border-border overflow-hidden">
          <div className="overflow-x-auto scrollbar-thin">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider text-slate-500 border-b border-border bg-white/[0.02]">
                  <th className="px-5 py-3 font-medium">Date</th>
                  <th className="px-5 py-3 font-medium">Currency</th>
                  <th className="px-5 py-3 font-medium">Event</th>
                  <th className="px-5 py-3 font-medium">Impact</th>
                  <th className="px-5 py-3 font-medium">IDX Sector Impact</th>
                  <th className="px-5 py-3 font-medium text-right">Forecast vs Previous</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60 text-[13px]">
                {error && (
                  <tr><td colSpan={6} className="px-5 py-8 text-center text-sm text-slate-500">
                    Gak bisa konek ke backend ({API_BASE}). Pastikan <code className="text-cyan">uvicorn main:app --reload --port 8000</code> lagi jalan.
                  </td></tr>
                )}
                {!error && warning && (
                  <tr><td colSpan={6} className="px-5 py-8 text-center text-sm text-slate-500">{warning}</td></tr>
                )}
                {!error && !warning && events && filteredEvents.length === 0 && (
                  <tr><td colSpan={6} className="px-5 py-8 text-center text-sm text-slate-500">Gak ada event yang cocok filter.</td></tr>
                )}
                {!error && !warning && filteredEvents.map((e, i) => <EventRow key={i} e={e} />)}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}
