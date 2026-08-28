import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { API_BASE } from '../lib/api';
import { IMPACT_DOT_CLASS, formatShortDate } from '../lib/events';

const SENTIMENT_CLASS = {
  bullish: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
  bearish: 'bg-strong/10 text-strong border-strong/30',
  neutral: 'bg-slate-500/10 text-slate-400 border-slate-500/30',
};

const RISK = {
  low: { badge: 'bg-risklow/10 text-risklow border-risklow/30', dot: 'bg-risklow', label: 'Low Risk' },
  medium: { badge: 'bg-riskmed/10 text-riskmed border-riskmed/30', dot: 'bg-riskmed', label: 'Medium Risk' },
  high: { badge: 'bg-riskhigh/10 text-riskhigh border-riskhigh/30', dot: 'bg-riskhigh', label: 'High Risk' },
};

export default function PortfolioSimulation() {
  const [timeline, setTimeline] = useState([]);

  const IMPACT_RANK = { High: 3, Medium: 2, Low: 1, Holiday: 0 };

  useEffect(() => {
    const today = new Date().toISOString().slice(0, 10);
    fetch(`${API_BASE}/market-events`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(({ data }) => {
        const byDate = new Map();
        for (const e of (data || [])) {
          if (e.date < today) continue;
          if (!byDate.has(e.date)) byDate.set(e.date, []);
          byDate.get(e.date).push(e);
        }
        const days = [...byDate.entries()]
          .sort(([a], [b]) => a.localeCompare(b))
          .slice(0, 7)
          .map(([date, events]) => ({
            date: formatShortDate(date),
            events: [...events].sort((a, b) => (IMPACT_RANK[b.impact] ?? 0) - (IMPACT_RANK[a.impact] ?? 0)),
          }));
        setTimeline(days);
      })
      .catch(() => setTimeline([]));
  }, []);

  const [holdings, setHoldings] = useState([
    { kode: 'BBRI', lot: 80, avg_price: 4650 },
    { kode: 'ASII', lot: 40, avg_price: 5300 },
  ]);
  const [kode, setKode] = useState('');
  const [lot, setLot] = useState('');
  const [avg, setAvg] = useState('');

  const [intel, setIntel] = useState(null);
  const [intelError, setIntelError] = useState(false);

  const [result, setResult] = useState(null);
  const [simRunning, setSimRunning] = useState(false);
  const [simError, setSimError] = useState(null);

  async function loadIntelHistory() {
    try {
      const res = await fetch(`${API_BASE}/intel?days=3`);
      const { data } = await res.json();
      setIntel(data);
      setIntelError(false);
    } catch {
      setIntelError(true);
    }
  }

  async function runSimulation() {
    setSimRunning(true);
    setSimError(null);
    try {
      const res = await fetch(`${API_BASE}/portfolio/simulate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ holdings }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || 'Simulasi gagal');
      setResult(await res.json());
    } catch (err) {
      setSimError(err.message);
    } finally {
      setSimRunning(false);
    }
  }

  useEffect(() => {
    loadIntelHistory();
    runSimulation();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function addHolding() {
    const lotNum = parseFloat(lot);
    const avgNum = parseFloat(avg);
    if (!kode.trim() || !lotNum || !avgNum) { alert('Isi kode, lot, dan avg price dulu'); return; }
    setHoldings((hs) => [...hs, { kode: kode.trim().toUpperCase(), lot: lotNum, avg_price: avgNum }]);
    setKode(''); setLot(''); setAvg('');
  }
  function removeHolding(i) {
    setHoldings((hs) => hs.filter((_, idx) => idx !== i));
  }

  const totalValue = holdings.reduce((sum, h) => sum + h.lot * h.avg_price, 0) || 1;
  const risk = RISK[result?.overall_risk || 'medium'];

  return (
    <>
      <header className="sticky top-0 z-10 bg-[#0B0F1A]/90 backdrop-blur border-b border-border px-8 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight">Portfolio Simulation</h1>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">AI risk analysis · powered by Groq</p>
        </div>
        <div className="flex items-center gap-4">
          <span className={`flex items-center gap-2 text-xs font-semibold px-3 py-1.5 rounded-full ${risk.badge}`}>
            <span className={`w-1.5 h-1.5 rounded-full pulse-dot ${risk.dot}`}></span> {(result?.overall_risk || 'medium').toUpperCase()} RISK
          </span>
          <button className="relative w-9 h-9 rounded-lg bg-card border border-border flex items-center justify-center hover:border-accent/50 transition">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M18 8A6 6 0 0 0 6 8C6 15 3 17 3 17H21S18 15 18 8Z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /><path d="M13.73 21A2 2 0 0 1 10.27 21" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
            <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-strong"></span>
          </button>
        </div>
      </header>

      <div className="p-8 space-y-6">
        <div className="grid grid-cols-3 gap-6">
          <div className="space-y-6">
            <div className="glow-border rounded-2xl bg-card border border-border p-5">
              <h3 className="text-sm font-bold text-white tracking-tight mb-4">Input Portofolio</h3>
              <div className="space-y-2 mb-3">
                <input
                  type="text" placeholder="Kode saham (e.g. BBCA)" value={kode} onChange={(e) => setKode(e.target.value)}
                  className="w-full bg-card2 border border-border rounded-lg px-3 py-2 text-sm text-white font-mono uppercase focus:outline-none focus:border-accent/60"
                />
                <div className="grid grid-cols-2 gap-2">
                  <input type="number" placeholder="Jumlah lot" value={lot} onChange={(e) => setLot(e.target.value)} className="bg-card2 border border-border rounded-lg px-3 py-2 text-sm text-white font-mono focus:outline-none focus:border-accent/60" />
                  <input type="number" placeholder="Avg price" value={avg} onChange={(e) => setAvg(e.target.value)} className="bg-card2 border border-border rounded-lg px-3 py-2 text-sm text-white font-mono focus:outline-none focus:border-accent/60" />
                </div>
                <button onClick={addHolding} className="w-full flex items-center justify-center gap-2 text-xs font-semibold px-4 py-2 rounded-lg bg-white/5 text-slate-300 border border-border hover:border-accent/50 hover:text-white transition">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M12 5V19M5 12H19" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
                  Tambah Saham
                </button>
              </div>

              <div className="overflow-x-auto scrollbar-thin -mx-1">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-[10px] uppercase tracking-wider text-slate-500 border-b border-border">
                      <th className="px-1 py-2 font-medium">Kode</th>
                      <th className="px-1 py-2 font-medium">Lot</th>
                      <th className="px-1 py-2 font-medium">Avg</th>
                      <th className="px-1 py-2 font-medium text-right">Exp %</th>
                      <th className="px-1 py-2 font-medium w-6"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/60 font-mono">
                    {holdings.map((h, i) => (
                      <tr key={i}>
                        <td className="px-1 py-2 font-sans font-semibold text-white">{h.kode}</td>
                        <td className="px-1 py-2 text-slate-300">{h.lot}</td>
                        <td className="px-1 py-2 text-slate-300">{h.avg_price.toLocaleString('id-ID')}</td>
                        <td className="px-1 py-2 text-right text-white font-semibold">{(h.lot * h.avg_price / totalValue * 100).toFixed(1)}%</td>
                        <td className="px-1 py-2 text-right">
                          <button onClick={() => removeHolding(i)} title={`Hapus ${h.kode}`} className="text-slate-500 hover:text-strong transition">
                            <svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M18 6L6 18M6 6L18 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <button onClick={runSimulation} disabled={simRunning} className="w-full mt-4 flex items-center justify-center gap-2 text-sm font-semibold px-4 py-2.5 rounded-lg bg-accent hover:bg-accent/90 text-white transition">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="M13 2L3 14H12L11 22L21 10H12L13 2Z" stroke="white" strokeWidth="2" strokeLinejoin="round" /></svg>
                {simRunning ? 'Menjalankan AI...' : 'Jalankan Simulasi'}
              </button>
            </div>

            <details className="glow-border rounded-2xl bg-card border border-border p-5" open>
              <summary className="flex items-center justify-between">
                <h3 className="text-sm font-bold text-white tracking-tight">Berita Terkini</h3>
                <svg className="chev text-slate-500 transition-transform" width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M9 18L15 12L9 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
              </summary>
              <p className="text-[11px] text-slate-500 mt-2">Otomatis dari WhatsApp/Telegram channel yang di-pantau, dipakai AI buat simulasi di samping.</p>

              <div className="mt-4 space-y-2">
                {intelError && <p className="text-xs text-slate-500 text-center py-2">Gak bisa konek ke backend.</p>}
                {!intelError && intel && intel.length === 0 && <p className="text-xs text-slate-500 text-center py-2">Belum ada berita masuk beberapa hari terakhir.</p>}
                {!intelError && intel?.map((i, idx) => (
                  <details key={idx} className="text-xs">
                    <summary className="flex items-center justify-between py-1.5">
                      <span className="flex items-center gap-2">
                        <span className="text-slate-300 font-medium">{i.sumber}</span>
                        <span className="text-[10px] text-slate-500 font-mono">{i.tanggal}</span>
                      </span>
                      <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${SENTIMENT_CLASS[i.sentiment] || SENTIMENT_CLASS.neutral}`}>{i.sentiment || 'pending'}</span>
                    </summary>
                    <ul className="pl-4 pb-2 space-y-1 list-disc marker:text-slate-600">
                      {(i.summary_ai?.poin_penting || []).length
                        ? i.summary_ai.poin_penting.map((p, pi) => <li key={pi} className="text-slate-400 text-justify">{p}</li>)
                        : <li className="text-slate-500">Belum diringkas</li>}
                    </ul>
                  </details>
                ))}
              </div>
            </details>
          </div>

          <div className="col-span-2 space-y-6">
            <div className="glow-border rounded-2xl bg-card border border-border p-6">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-bold text-white tracking-tight">Hasil Simulasi</h3>
                <span className="text-[10px] text-slate-500 font-mono">via Groq</span>
              </div>

              {simError && <div className="mb-4 p-3 rounded-lg bg-strong/10 border border-strong/30 text-sm text-strong">{simError}</div>}

              <div className="rounded-xl bg-riskmed/5 border border-riskmed/20 p-4 mb-5">
                <p className="text-[15px] text-slate-100 font-medium leading-relaxed text-justify">{result?.portfolio_impact_summary || '—'}</p>
              </div>

              <div className="grid grid-cols-3 gap-4 mb-6">
                {(result?.per_saham || []).map((s, i) => {
                  const r = RISK[s.risk_level] || RISK.medium;
                  return (
                    <div key={i} className="rounded-xl border border-border bg-card2 p-3.5">
                      <div className="flex items-center justify-between mb-2">
                        <span className="font-mono font-bold text-white text-sm">{s.kode}</span>
                        <span className={`text-[9px] font-semibold px-2 py-0.5 rounded-full border ${r.badge}`}>{r.label}</span>
                      </div>
                      <p className="text-[10px] text-slate-500 mb-2 font-mono">Exposure {s.exposure_pct}%</p>
                      <p className="text-xs text-slate-400 leading-snug text-justify">{s.alasan}</p>
                    </div>
                  );
                })}
              </div>

              <div>
                <p className="text-xs font-semibold text-slate-400 mb-3 uppercase tracking-wider">Rekomendasi Aksi</p>
                <div className="space-y-2.5">
                  {(result?.rekomendasi_aksi || []).map((r, i) => (
                    <div key={i} className="flex items-start gap-2.5">
                      <svg className="mt-0.5 shrink-0" width="15" height="15" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="#06B6D4" strokeWidth="1.8" /><path d="M8.5 12.5L11 15L16 9.5" stroke="#06B6D4" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
                      <p className="text-sm text-slate-300 text-justify">{r}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="glow-border rounded-2xl bg-card border border-border p-6">
              <h3 className="text-sm font-bold text-white tracking-tight mb-6">Event Timeline — 7 Hari Ke Depan</h3>
              <div className="timeline-track relative flex justify-between px-2 pt-1">
                {timeline.length === 0 && <p className="text-xs text-slate-500">Gak ada event minggu ini.</p>}
                {timeline.map((d, i) => {
                  const top = d.events[0];
                  return (
                    <div key={i} className="flex flex-col items-center text-center w-24 group relative">
                      <div className={`w-4 h-4 rounded-full ${IMPACT_DOT_CLASS[top.impact] || IMPACT_DOT_CLASS.Low} border-2 border-card z-10 cursor-pointer`}></div>
                      <p className="text-[10px] text-slate-500 font-mono mt-2">{d.date}</p>
                      <p className="text-lg leading-none mt-1">{top.flag}</p>
                      <p className="text-[11px] text-slate-300 font-medium mt-1 leading-tight">
                        {top.event}
                        {d.events.length > 1 && <span className="text-slate-500"> +{d.events.length - 1} lainnya</span>}
                      </p>
                      <div className="absolute bottom-full mb-2 hidden group-hover:block w-56 bg-card2 border border-border rounded-lg p-2.5 text-[10px] text-slate-300 shadow-xl z-20 text-left space-y-2">
                        {d.events.slice(0, 3).map((ev, ei) => (
                          <div key={ei}>
                            <span className="font-semibold text-white">{ev.flag} {ev.event}</span>
                            <p className="text-slate-400 mt-0.5">{ev.impact} Impact — affects <span className="text-cyan">{ev.idx_sector_impact}</span> sector</p>
                          </div>
                        ))}
                        {d.events.length > 3 && (
                          <Link to="/market-events" className="block pt-1 text-cyan hover:underline">
                            +{d.events.length - 3} event lain — lihat semua →
                          </Link>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
