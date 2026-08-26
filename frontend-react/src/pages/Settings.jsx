import { useEffect, useState } from 'react';
import { API_BASE } from '../lib/api';

const TICKERS = ['BBRI', 'ASII', 'GOTO', 'TLKM', 'ANTM', 'MDKA', 'ADRO', 'ICBP', 'BMRI', 'UNVR'];

function Toggle({ defaultOn }) {
  const [on, setOn] = useState(defaultOn);
  return (
    <div className={`toggle ${on ? 'on' : ''}`} onClick={() => setOn((v) => !v)}>
      <div className="toggle-dot"></div>
    </div>
  );
}

export default function Settings() {
  const [tgStatus, setTgStatus] = useState({ text: 'Checking...', cls: 'text-slate-500' });
  const [botHandle, setBotHandle] = useState('—');
  const [botLink, setBotLink] = useState('#');
  const [connected, setConnected] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [testing, setTesting] = useState(false);

  async function refreshTelegramStatus() {
    try {
      const res = await fetch(`${API_BASE}/telegram/status`);
      const data = await res.json();

      if (data.bot_username) {
        setBotHandle(`Bot: @${data.bot_username}`);
        setBotLink(`https://t.me/${data.bot_username}`);
      } else {
        setBotHandle(data.error || 'Bot belum bisa dijangkau');
      }

      if (data.connected) {
        setTgStatus({ text: 'Connected', cls: 'text-emerald-400' });
        setConnected(true);
      } else {
        setTgStatus({ text: 'Not connected', cls: 'text-slate-500' });
        setConnected(false);
      }
    } catch {
      setTgStatus({ text: 'Gak bisa konek ke backend', cls: 'text-slate-500' });
    }
  }

  useEffect(() => { refreshTelegramStatus(); }, []);

  async function handleDetect() {
    setDetecting(true);
    try {
      const detectRes = await fetch(`${API_BASE}/telegram/detect`);
      if (!detectRes.ok) throw new Error((await detectRes.json()).detail);
      const { chat_id } = await detectRes.json();

      const connectRes = await fetch(`${API_BASE}/telegram/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id }),
      });
      if (!connectRes.ok) throw new Error('Gagal simpan koneksi');

      await refreshTelegramStatus();
    } catch (err) {
      alert(err.message || 'Gagal hubungkan Telegram');
    } finally {
      setDetecting(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    try {
      const res = await fetch(`${API_BASE}/telegram/test`, { method: 'POST' });
      if (!res.ok) throw new Error((await res.json()).detail);
      alert('Test alert terkirim — cek Telegram kamu!');
    } catch (err) {
      alert('Gagal kirim: ' + err.message);
    } finally {
      setTesting(false);
    }
  }

  return (
    <>
      <header className="sticky top-0 z-10 bg-[#0B0F1A]/90 backdrop-blur border-b border-border px-8 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white tracking-tight">Settings</h1>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">Preferences & Integrations</p>
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

      <div className="p-8 grid grid-cols-3 gap-6">
        <div className="glow-border rounded-2xl bg-gradient-to-br from-card to-card2 border border-border p-5">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-10 h-10 rounded-xl bg-[#229ED9]/15 border border-[#229ED9]/30 flex items-center justify-center">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="#229ED9"><path d="M21.5 4.5L2.7 11.9c-1.2.5-1.2 1.2-.2 1.5l4.8 1.5 1.9 5.8c.2.6.4.8.9.8.4 0 .6-.2.9-.5l2.2-2.1 4.6 3.4c.8.5 1.4.2 1.6-.7l3-14.1c.3-1.2-.4-1.7-1.9-1z" /></svg>
            </div>
            <div>
              <p className="text-sm font-bold text-white">Telegram Bot</p>
              <p className={`text-[11px] ${tgStatus.cls}`}>{tgStatus.text}</p>
            </div>
          </div>
          <p className="text-xs text-slate-400 mb-4">Connect your Telegram to receive real-time accumulation alerts straight to your phone.</p>

          {!connected && (
            <div>
              <a href={botLink} target="_blank" rel="noreferrer" className="w-full text-sm font-semibold px-4 py-2.5 rounded-lg bg-[#229ED9] hover:bg-[#1c8bc0] text-white transition flex items-center justify-center gap-2">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="white"><path d="M21.5 4.5L2.7 11.9c-1.2.5-1.2 1.2-.2 1.5l4.8 1.5 1.9 5.8c.2.6.4.8.9.8.4 0 .6-.2.9-.5l2.2-2.1 4.6 3.4c.8.5 1.4.2 1.6-.7l3-14.1c.3-1.2-.4-1.7-1.9-1z" /></svg>
                1. Buka Bot & Kirim /start
              </a>
              <button onClick={handleDetect} disabled={detecting} className="w-full mt-2 text-sm font-semibold px-4 py-2.5 rounded-lg bg-white/5 text-slate-300 border border-border hover:border-accent/50 hover:text-white transition">
                {detecting ? 'Mendeteksi...' : '2. Sudah Kirim /start — Hubungkan'}
              </button>
            </div>
          )}

          {connected && (
            <button onClick={handleTest} disabled={testing} className="w-full text-sm font-semibold px-4 py-2.5 rounded-lg bg-white/5 text-slate-300 border border-border hover:border-accent/50 hover:text-white transition">
              {testing ? 'Mengirim...' : 'Kirim Test Alert'}
            </button>
          )}

          <p className="text-[11px] text-slate-500 mt-3 font-mono text-center">{botHandle}</p>
        </div>

        <div className="glow-border rounded-2xl bg-card border border-border p-5">
          <p className="text-sm font-bold text-white mb-1">Alert Threshold</p>
          <p className="text-xs text-slate-500 mb-5">Only notify when accumulation score crosses this value.</p>
          <div className="flex items-center justify-between mb-2">
            <span className="text-[11px] text-slate-500 font-mono">0</span>
            <span className="text-2xl font-extrabold text-cyan font-mono">30</span>
            <span className="text-[11px] text-slate-500 font-mono">45</span>
          </div>
          <input type="range" min="0" max="45" defaultValue="30" className="w-full accent-cyan" />
          <div className="flex justify-between text-[10px] text-slate-500 mt-2 font-mono">
            <span>None</span><span>Weak</span><span>Moderate</span><span>Strong</span>
          </div>
        </div>

        <div className="glow-border rounded-2xl bg-card border border-border p-5">
          <p className="text-sm font-bold text-white mb-4">Notification Preferences</p>
          <div className="space-y-4">
            <div className="flex items-center justify-between"><span className="text-sm text-slate-300">Strong signal alerts</span><Toggle defaultOn /></div>
            <div className="flex items-center justify-between"><span className="text-sm text-slate-300">Daily night recap</span><Toggle defaultOn /></div>
            <div className="flex items-center justify-between"><span className="text-sm text-slate-300">Economic event reminders</span><Toggle /></div>
            <div className="flex items-center justify-between"><span className="text-sm text-slate-300">Portfolio risk warnings</span><Toggle defaultOn /></div>
          </div>
        </div>

        <div className="col-span-2 glow-border rounded-2xl bg-card border border-border p-5">
          <div className="flex items-center justify-between mb-4">
            <p className="text-sm font-bold text-white">Watchlist Management</p>
            <span className="text-[11px] text-slate-500 font-mono">{TICKERS.length} tickers</span>
          </div>
          <div className="flex items-center gap-2 mb-4">
            <input type="text" placeholder="Add ticker (e.g. BBCA)" className="flex-1 bg-card2 border border-border rounded-lg px-3 py-2 text-sm text-white font-mono uppercase focus:outline-none focus:border-accent/60" />
            <button className="text-sm font-semibold px-4 py-2 rounded-lg bg-accent hover:bg-accent/90 text-white transition">Add</button>
          </div>
          <div className="flex flex-wrap gap-2">
            {TICKERS.map((t) => (
              <span key={t} className="flex items-center gap-1.5 text-xs font-mono font-semibold px-3 py-1.5 rounded-lg bg-white/5 border border-border text-slate-200">
                {t}
                <button className="text-slate-500 hover:text-strong transition">×</button>
              </span>
            ))}
          </div>
        </div>

        <div className="glow-border rounded-2xl bg-card border border-border p-5">
          <p className="text-sm font-bold text-white mb-4">Account</p>
          <div className="flex items-center gap-3 mb-4">
            <div className="w-12 h-12 rounded-full bg-gradient-to-br from-cyan to-accent flex items-center justify-center text-sm font-bold text-white">RA</div>
            <div>
              <p className="text-sm font-semibold text-white">Rian Adnan</p>
              <p className="text-[11px] text-slate-500">rian.adnan@student.ac.id</p>
            </div>
          </div>
          <button className="w-full text-xs font-semibold px-4 py-2 rounded-lg bg-white/5 text-slate-300 border border-border hover:border-accent/50 hover:text-white transition mb-2">Edit Profile</button>
          <button className="w-full text-xs font-semibold px-4 py-2 rounded-lg bg-strong/10 text-strong border border-strong/30 hover:bg-strong/20 transition">Log Out</button>
        </div>
      </div>
    </>
  );
}
