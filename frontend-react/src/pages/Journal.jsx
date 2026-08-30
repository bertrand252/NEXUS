import { useEffect, useRef, useState } from 'react';
import { API_BASE } from '../lib/api';

const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

function pad(n) { return String(n).padStart(2, '0'); }

function cellClasses(net) {
  if (net > 0) return 'bg-emerald-500/20 border-emerald-500/40 text-emerald-300';
  if (net < 0) return 'bg-red-500/20 border-red-500/40 text-red-300';
  return 'bg-white/[0.02] border-border text-slate-500';
}

export default function Journal() {
  const [viewYear, setViewYear] = useState(new Date().getFullYear());
  const [viewMonth, setViewMonth] = useState(new Date().getMonth() + 1);
  const [monthData, setMonthData] = useState({});
  const [suggestions, setSuggestions] = useState([]);

  const [modalOpen, setModalOpen] = useState(false);
  const [modalDay, setModalDay] = useState(null);
  const [groups, setGroups] = useState([]);
  const [saving, setSaving] = useState(false);
  const nextId = useRef(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/journal?year=${viewYear}&month=${viewMonth}`);
        const { data } = await res.json();
        if (cancelled) return;
        const byDate = {};
        data.forEach((d) => { byDate[d.tanggal] = d; });
        setMonthData(byDate);
      } catch {
        if (!cancelled) setMonthData({});
      }
      try {
        const res = await fetch(`${API_BASE}/journal/suggestions?year=${viewYear}&month=${viewMonth}`);
        const { data } = await res.json();
        if (!cancelled) setSuggestions(data || []);
      } catch {
        if (!cancelled) setSuggestions([]);
      }
    })();
    return () => { cancelled = true; };
  }, [viewYear, viewMonth]);

  function prevMonth() {
    if (viewMonth === 1) { setViewMonth(12); setViewYear((y) => y - 1); }
    else setViewMonth((m) => m - 1);
  }
  function nextMonth() {
    if (viewMonth === 12) { setViewMonth(1); setViewYear((y) => y + 1); }
    else setViewMonth((m) => m + 1);
  }

  function newGroup(emiten = '', pl = 'profit', amount = '') {
    return { id: nextId.current++, emiten, pl, amount };
  }

  function openModal(day) {
    const dateStr = `${viewYear}-${pad(viewMonth)}-${pad(day)}`;
    const existing = monthData[dateStr];
    setGroups(
      existing?.entries?.length
        ? existing.entries.map((e) => newGroup(e.emiten, e.profit_loss, e.amount))
        : [newGroup()]
    );
    setModalDay(day);
    setModalOpen(true);
  }
  function closeModal() { setModalOpen(false); }

  function updateGroup(id, patch) {
    setGroups((gs) => gs.map((g) => (g.id === id ? { ...g, ...patch } : g)));
  }
  function removeGroup(id) {
    if (groups.length <= 1) { alert('Minimal harus ada 1 emiten'); return; }
    setGroups((gs) => gs.filter((g) => g.id !== id));
  }

  async function confirmTrade() {
    const dateStr = `${viewYear}-${pad(viewMonth)}-${pad(modalDay)}`;
    const entries = groups
      .map((g) => ({ emiten: g.emiten.trim().toUpperCase(), profit_loss: g.pl, amount: parseFloat(g.amount) || 0 }))
      .filter((e) => e.emiten && e.amount > 0);

    if (!entries.length) { alert('Isi minimal 1 emiten dengan amount valid'); return; }

    setSaving(true);
    try {
      const res = await fetch(`${API_BASE}/journal`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tanggal: dateStr, entries }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || 'Gagal simpan');
      const refreshed = await fetch(`${API_BASE}/journal?year=${viewYear}&month=${viewMonth}`);
      const { data } = await refreshed.json();
      const byDate = {};
      data.forEach((d) => { byDate[d.tanggal] = d; });
      setMonthData(byDate);
      closeModal();
    } catch (err) {
      alert('Gagal simpan journal: ' + err.message);
    } finally {
      setSaving(false);
    }
  }

  const modalDateStr = modalDay ? `${viewYear}-${pad(viewMonth)}-${pad(modalDay)}` : null;
  const usedEmiten = new Set(groups.map((g) => g.emiten.trim().toUpperCase()).filter(Boolean));
  const daySuggestions = modalDateStr
    ? suggestions.filter((s) => s.tanggal === modalDateStr && !usedEmiten.has(s.emiten))
    : [];

  function addSuggestion(s) {
    setGroups((gs) => {
      const empty = gs.find((g) => !g.emiten.trim());
      const filled = { emiten: s.emiten, pl: s.profit_loss, amount: '' };
      return empty ? gs.map((g) => (g.id === empty.id ? { ...g, ...filled } : g)) : [...gs, newGroup(filled.emiten, filled.pl)];
    });
  }

  const startDow = new Date(viewYear, viewMonth - 1, 1).getDay();
  const daysInMonth = new Date(viewYear, viewMonth, 0).getDate();
  const cells = [];
  for (let i = 0; i < startDow; i++) cells.push(<div key={`pad-${i}`}></div>);
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${viewYear}-${pad(viewMonth)}-${pad(d)}`;
    const day = monthData[dateStr];
    const net = day ? day.net : 0;
    cells.push(
      <div key={d} onClick={() => openModal(d)} className={`day-cell rounded-lg border flex flex-col items-center justify-center ${cellClasses(net)}`}>
        <span className="text-sm font-mono font-semibold">{d}</span>
        {day && <span className="text-[9px] font-mono mt-0.5">{net >= 0 ? '+' : '−'}Rp{Math.abs(net).toLocaleString('id-ID')}</span>}
      </div>
    );
  }

  return (
    <>
      <header className="sticky top-0 z-10 bg-[#0B0F1A]/90 backdrop-blur border-b border-border px-8 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight">Trading Journal</h1>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">{MONTH_NAMES[viewMonth - 1]} {viewYear}</p>
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

      <div className="p-8 space-y-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4 text-xs">
            <span className="flex items-center gap-1.5 text-slate-400"><span className="w-3 h-3 rounded bg-emerald-500/60"></span> Profit</span>
            <span className="flex items-center gap-1.5 text-slate-400"><span className="w-3 h-3 rounded bg-red-500/60"></span> Loss</span>
            <span className="flex items-center gap-1.5 text-slate-400"><span className="w-3 h-3 rounded bg-white/5 border border-border"></span> No Trade</span>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={prevMonth} className="w-8 h-8 rounded-lg bg-card border border-border flex items-center justify-center text-slate-400 hover:text-white transition">‹</button>
            <span className="text-sm font-semibold text-white font-mono px-2">{MONTH_NAMES[viewMonth - 1]} {viewYear}</span>
            <button onClick={nextMonth} className="w-8 h-8 rounded-lg bg-card border border-border flex items-center justify-center text-slate-400 hover:text-white transition">›</button>
          </div>
        </div>

        <div className="glow-border rounded-2xl bg-card border border-border p-5">
          <div className="grid grid-cols-7 gap-2 mb-2">
            {['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'].map((d) => (
              <div key={d} className="text-center text-[11px] font-semibold text-slate-500 py-1">{d}</div>
            ))}
          </div>
          <div className="grid grid-cols-7 gap-2">{cells}</div>
        </div>
      </div>

      <div className={`fixed inset-0 z-50 flex items-center justify-center transition-opacity duration-200 ${modalOpen ? '' : 'hidden-modal'}`}>
        <div className="absolute inset-0 bg-black/70" onClick={closeModal}></div>
        <div className="relative w-full max-w-md bg-card border border-border rounded-2xl p-6 shadow-2xl transition-transform duration-200">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-base font-bold text-white">Log Trade — <span className="text-cyan font-mono">{modalDay ? `${modalDay} ${MONTH_NAMES[viewMonth - 1]} ${viewYear}` : ''}</span></h3>
            <button onClick={closeModal} className="text-slate-500 hover:text-white transition">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M18 6L6 18M6 6L18 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
            </button>
          </div>

          {daySuggestions.length > 0 && (
            <div className="mb-4 p-2.5 rounded-lg bg-cyan/5 border border-cyan/20">
              <p className="text-[10px] text-cyan font-semibold mb-1.5">📡 Dari sinyal NEXUS (klik buat isi otomatis):</p>
              <div className="flex flex-wrap gap-1.5">
                {daySuggestions.map((s) => (
                  <button
                    key={s.emiten} onClick={() => addSuggestion(s)}
                    className={`text-[11px] font-mono font-semibold px-2.5 py-1 rounded-lg border transition ${
                      s.profit_loss === 'profit'
                        ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30 hover:bg-emerald-500/20'
                        : 'bg-red-500/10 text-red-300 border-red-500/30 hover:bg-red-500/20'
                    }`}
                  >
                    {s.emiten} ({s.outcome_pct >= 0 ? '+' : ''}{s.outcome_pct}%)
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="space-y-4 max-h-72 overflow-y-auto pr-1">
            {groups.map((g) => (
              <div key={g.id} className="emiten-group border border-border rounded-xl p-3 space-y-2.5">
                <div className="flex items-center gap-2">
                  <input
                    type="text" placeholder="Emiten (e.g. BBRI)" value={g.emiten}
                    onChange={(e) => updateGroup(g.id, { emiten: e.target.value })}
                    className="flex-1 bg-card2 border border-border rounded-lg px-3 py-2 text-sm text-white font-mono uppercase focus:outline-none focus:border-accent/60"
                  />
                  <button onClick={() => removeGroup(g.id)} title="Hapus emiten ini" className="shrink-0 text-slate-500 hover:text-strong transition p-1">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M18 6L6 18M6 6L18 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
                  </button>
                </div>
                <div className="flex items-center gap-4">
                  <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
                    <input type="radio" name={`pl-${g.id}`} value="profit" checked={g.pl === 'profit'} onChange={() => updateGroup(g.id, { pl: 'profit' })} className="accent-emerald-500" /> Profit
                  </label>
                  <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
                    <input type="radio" name={`pl-${g.id}`} value="loss" checked={g.pl === 'loss'} onChange={() => updateGroup(g.id, { pl: 'loss' })} className="accent-red-500" /> Loss
                  </label>
                </div>
                <input
                  type="number" placeholder="Amount (IDR)" value={g.amount}
                  onChange={(e) => updateGroup(g.id, { amount: e.target.value })}
                  className="w-full bg-card2 border border-border rounded-lg px-3 py-2 text-sm text-white font-mono focus:outline-none focus:border-accent/60"
                />
              </div>
            ))}
          </div>

          <button onClick={() => setGroups((gs) => [...gs, newGroup()])} className="w-full mt-3 flex items-center justify-center gap-2 text-xs font-semibold px-4 py-2.5 rounded-lg bg-white/5 text-slate-300 border border-border hover:border-accent/50 hover:text-white transition">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M12 5V19M5 12H19" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
            + Add Emiten
          </button>

          <button onClick={confirmTrade} disabled={saving} className="w-full mt-4 text-sm font-semibold px-4 py-2.5 rounded-lg bg-accent hover:bg-accent/90 text-white transition">
            {saving ? 'Menyimpan...' : 'Confirm & Save'}
          </button>
        </div>
      </div>
    </>
  );
}
