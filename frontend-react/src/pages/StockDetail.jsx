import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { API_BASE } from '../lib/api';
import { signalMeta, zoneLabel, zoneColorClass } from '../lib/signal';
import { useChart } from '../hooks/useChart';
import { useCandlestickChart } from '../hooks/useCandlestickChart';

function ScoreCard({ label, value, max, barClass }) {
  return (
    <div className="glow-border rounded-2xl bg-card border border-border p-4">
      <p className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold mb-2">{label}</p>
      <p className="text-2xl font-extrabold text-white font-mono">{value ?? '--'}<span className="text-xs text-slate-500 font-medium">/{max}</span></p>
      <div className="w-full h-1.5 rounded-full bg-white/5 mt-2 overflow-hidden">
        <div className={`h-full rounded-full ${barClass}`} style={{ width: value != null ? `${(value / max) * 100}%` : '0%' }}></div>
      </div>
    </div>
  );
}

export default function StockDetail() {
  const [params] = useSearchParams();
  const ticker = (params.get('t') || 'ANTM').toUpperCase();

  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/scanner/${ticker}`);
        if (!res.ok) throw new Error((await res.json()).detail || `HTTP ${res.status}`);
        const json = await res.json();
        if (!cancelled) setData(json);
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    })();
    return () => { cancelled = true; };
  }, [ticker]);

  const candles = data ? data.candles.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })) : null;
  const candleRef = useCandlestickChart(candles);

  const brokerConfig = {
    type: 'bar',
    data: {
      labels: ['YP', 'MG', 'RG', 'PD', 'CC', 'KZ', 'NI'],
      datasets: [{
        data: [820, 640, -210, 450, -380, 290, -150],
        backgroundColor: (ctx) => (ctx.raw >= 0 ? '#2563EB' : '#EF4444'),
        borderRadius: 4,
      }],
    },
    options: { plugins: { legend: { display: false } }, scales: { x: { grid: { display: false }, ticks: { color: '#94A3B8', font: { size: 11 } } }, y: { grid: { color: '#1F2937' }, ticks: { color: '#64748B', font: { size: 10 } } } } },
  };
  const brokerRef = useChart(brokerConfig);

  const m = signalMeta(data?.signal);
  const needleDeg = data ? (data.total_score / 100) * 180 - 90 : -90;

  return (
    <>
      <header className="sticky top-0 z-10 bg-[#0B0F1A]/90 backdrop-blur border-b border-border px-8 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight">Stock Detail</h1>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">{ticker}</p>
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
        {error && (
          <div className="p-4 rounded-xl bg-strong/10 border border-strong/30 text-sm text-strong">
            Gak bisa muat data {ticker}: {error}. Pastikan backend jalan di {API_BASE}.
          </div>
        )}

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-accent/20 to-cyan/20 border border-accent/30 flex items-center justify-center font-mono font-bold text-cyan">{data ? data.ticker.slice(0, 2) : '--'}</div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-2xl font-extrabold text-white font-mono">{data?.ticker || '—'}</h2>
                <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${m.cls}`}>{data ? `${m.dot} ${data.signal === 'None' ? 'No Signal' : data.signal}` : '—'}</span>
              </div>
              <p className="text-sm text-slate-500">{data?.sector || '—'}</p>
            </div>
          </div>
          <div className="text-right">
            <p className="text-2xl font-bold text-white font-mono">{data ? `Rp ${data.price.toLocaleString('id-ID')}` : 'Rp —'}</p>
            <p className={`text-sm font-semibold font-mono ${data && data.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{data ? `${data.change_pct >= 0 ? '+' : ''}${data.change_pct}% today` : '—'}</p>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-6">
          <div className="col-span-2 glow-border rounded-2xl bg-card border border-border p-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-bold text-white tracking-tight">Price Chart</h3>
              <div className="flex gap-1 text-[11px] font-mono">
                <button className="px-2.5 py-1 rounded bg-accent/15 text-accent border border-accent/30">1D</button>
                <button className="px-2.5 py-1 rounded text-slate-500 hover:text-slate-300">1W</button>
                <button className="px-2.5 py-1 rounded text-slate-500 hover:text-slate-300">1M</button>
                <button className="px-2.5 py-1 rounded text-slate-500 hover:text-slate-300">1Y</button>
              </div>
            </div>
            <div ref={candleRef} style={{ height: 280 }}></div>
          </div>

          <div className="glow-border rounded-2xl bg-card border border-border p-5 flex flex-col items-center justify-center">
            <h3 className="text-sm font-bold text-white tracking-tight self-start mb-2">Accumulation Score</h3>
            <svg viewBox="-18 -5 236 145" className="w-full max-w-[240px]">
              <path d="M 20.00 100.00 A 80 80 0 0 1 35.28 52.98" fill="none" stroke="#22C55E" strokeWidth="16" strokeLinecap="round" />
              <path d="M 35.28 52.98 A 80 80 0 0 1 75.28 23.92" fill="none" stroke="#84CC16" strokeWidth="16" strokeLinecap="round" />
              <path d="M 75.28 23.92 A 80 80 0 0 1 124.72 23.92" fill="none" stroke="#EAB308" strokeWidth="16" strokeLinecap="round" />
              <path d="M 124.72 23.92 A 80 80 0 0 1 164.72 52.98" fill="none" stroke="#F97316" strokeWidth="16" strokeLinecap="round" />
              <path d="M 164.72 52.98 A 80 80 0 0 1 180.00 100.00" fill="none" stroke="#EF4444" strokeWidth="16" strokeLinecap="round" />

              <text x="4" y="72" textAnchor="middle" fill="#64748B" fontSize="7.5" fontWeight="600" fontFamily="Manrope" letterSpacing="0.3">VERY LOW</text>
              <text x="40" y="20" textAnchor="middle" fill="#64748B" fontSize="7.5" fontWeight="600" fontFamily="Manrope" letterSpacing="0.3">LOW</text>
              <text x="100" y="6" textAnchor="middle" fill="#64748B" fontSize="7.5" fontWeight="600" fontFamily="Manrope" letterSpacing="0.3">MODERATE</text>
              <text x="160" y="20" textAnchor="middle" fill="#64748B" fontSize="7.5" fontWeight="600" fontFamily="Manrope" letterSpacing="0.3">HIGH</text>
              <text x="196" y="72" textAnchor="middle" fill="#64748B" fontSize="7.5" fontWeight="600" fontFamily="Manrope" letterSpacing="0.3">VERY HIGH</text>

              <g className="needle" style={{ transform: `rotate(${needleDeg}deg)`, transformOrigin: '100px 100px', transition: 'transform 1.2s cubic-bezier(.34,1.56,.64,1)' }}>
                <line x1="100" y1="100" x2="100" y2="34" stroke="#FFFFFF" strokeWidth="3" strokeLinecap="round" />
                <circle cx="100" cy="100" r="6" fill="#FFFFFF" />
                <circle cx="100" cy="100" r="2.5" fill="#0B0F1A" />
              </g>
            </svg>
            <div className="text-center -mt-6">
              <p className="text-3xl font-extrabold text-white font-mono leading-none">{data?.total_score ?? '--'}<span className="text-sm text-slate-500 font-medium">/100</span></p>
            </div>
            <p className="text-xs text-slate-500 mt-2">Zone: <span className={`font-semibold ${zoneColorClass(data?.signal)}`}>{data ? zoneLabel(data.signal) : '—'}</span></p>
          </div>
        </div>

        <div className="grid grid-cols-4 gap-5">
          <ScoreCard label="Volume Score" value={data?.volume_score} max={25} barClass="bg-cyan" />
          <ScoreCard label="Price Score" value={data?.price_score} max={25} barClass="bg-accent" />
          <ScoreCard label="Accumulation Score" value={data?.accumulation_score} max={30} barClass="bg-strong" />
          <ScoreCard label="Technical Score" value={data?.technical_score} max={20} barClass="bg-moderate" />
        </div>

        <div className="grid grid-cols-3 gap-6">
          <div className="glow-border rounded-2xl bg-gradient-to-br from-card to-card2 border border-accent/30 p-5">
            <div className="flex items-center gap-2 mb-3">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M12 2L14.5 8.5L21 9.3L16.3 13.9L17.6 20.5L12 17.1L6.4 20.5L7.7 13.9L3 9.3L9.5 8.5L12 2Z" stroke="#06B6D4" strokeWidth="1.8" strokeLinejoin="round" /></svg>
              <h3 className="text-sm font-bold text-white tracking-tight">AI Prediction</h3>
            </div>
            <span className="inline-block text-xs font-bold px-3 py-1.5 rounded-full bg-white/5 text-slate-400 border border-border mb-3">Belum tersedia</span>
            <p className="text-sm text-slate-500">Model LSTM/XGBoost buat prediksi arah harga 1-5 hari belum diimplementasi.</p>
          </div>

          <div className="col-span-2 glow-border rounded-2xl bg-card border border-border p-5">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-bold text-white tracking-tight">Broker Summary (Net Value)</h3>
              <span className="text-[10px] text-slate-500 font-mono">Data placeholder — butuh sumber broker summary berbayar</span>
            </div>
            <canvas ref={brokerRef} height="140"></canvas>
          </div>
        </div>
      </div>
    </>
  );
}
