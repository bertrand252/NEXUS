import { useEffect, useRef, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { supabase } from '../lib/supabaseClient';
import { useAuth } from '../hooks/useAuth';
import logo from '../assets/NEXUS.png';

const inputCls = 'w-full bg-card2 border border-border rounded-lg px-3 py-2.5 text-sm text-white focus:outline-none focus:border-accent/60';
const btnCls = 'w-full text-sm font-semibold px-4 py-2.5 rounded-lg bg-accent hover:bg-accent/90 text-white transition disabled:opacity-50';

export default function Login() {
  const { session, loading: sessionLoading, mfaPassed } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [mfa, setMfa] = useState(null); // { factorId, qr?, secret? } — qr ada = pendaftaran pertama kali
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const mfaStarted = useRef(false); // StrictMode jalanin effect 2x, jangan enroll dobel

  const needMfa = !sessionLoading && session && !mfaPassed;

  // Password udah bener (aal1) → siapin langkah MFA: verifikasi kalau udah
  // punya authenticator, daftar (QR) kalau belum pernah.
  useEffect(() => {
    if (!needMfa || mfaStarted.current) return;
    mfaStarted.current = true;
    (async () => {
      const { data, error } = await supabase.auth.mfa.listFactors();
      if (error) { setError(error.message); return; }
      if (data.totp.length) { setMfa({ factorId: data.totp[0].id }); return; }
      // Sisa pendaftaran yang dulu gak diselesaiin — buang dulu biar gak numpuk.
      for (const f of data.all.filter((f) => f.status === 'unverified')) {
        await supabase.auth.mfa.unenroll({ factorId: f.id });
      }
      const enroll = await supabase.auth.mfa.enroll({ factorType: 'totp', issuer: 'NEXUS' });
      if (enroll.error) { setError(enroll.error.message); return; }
      setMfa({ factorId: enroll.data.id, qr: enroll.data.totp.qr_code, secret: enroll.data.totp.secret });
    })();
  }, [needMfa]);

  if (!sessionLoading && mfaPassed) return <Navigate to="/dashboard" replace />;

  async function handleLogin(e) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    setLoading(false);
    if (error) setError(error.message);
  }

  async function handleVerify(e) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const { error } = await supabase.auth.mfa.challengeAndVerify({ factorId: mfa.factorId, code: code.trim() });
    setLoading(false);
    if (error) { setError(error.message); setCode(''); return; }
    navigate('/dashboard', { replace: true });
  }

  async function handleCancel() {
    mfaStarted.current = false;
    setMfa(null);
    setCode('');
    setError(null);
    await supabase.auth.signOut();
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-base px-4">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center mb-8">
          <img src={logo} alt="NEXUS" className="w-14 h-14 rounded-xl mb-3" />
          <p className="text-lg font-bold text-white tracking-tight">NEXUS</p>
          <p className="text-[11px] text-slate-500 tracking-wide">IDX INTELLIGENCE</p>
        </div>

        {needMfa ? (
          <form onSubmit={handleVerify} className="glow-border rounded-2xl bg-card border border-border p-6 space-y-3">
            {!mfa && !error && <p className="text-xs text-slate-400">Menyiapkan verifikasi...</p>}
            {mfa?.qr && (
              <div className="space-y-2">
                <p className="text-xs text-slate-300">
                  Pertama kali: scan QR ini pake Google Authenticator / Authy, lalu masukin kode 6 digitnya.
                </p>
                <img src={mfa.qr} alt="QR authenticator" className="w-44 h-44 mx-auto bg-white rounded-lg p-2" />
                <p className="text-[11px] text-slate-500 break-all">Gak bisa scan? Kode manual: {mfa.secret}</p>
              </div>
            )}
            {mfa && !mfa.qr && <p className="text-xs text-slate-300">Masukin kode 6 digit dari aplikasi authenticator.</p>}
            {mfa && (
              <input
                inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="123456" autoFocus
                value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} required
                className={`${inputCls} tracking-[0.4em] text-center`}
              />
            )}
            {error && <p className="text-xs text-strong">{error}</p>}
            <button type="submit" disabled={loading || !mfa || code.length !== 6} className={btnCls}>
              {loading ? 'Memverifikasi...' : 'Verifikasi'}
            </button>
            <button type="button" onClick={handleCancel} className="w-full text-xs text-slate-500 hover:text-slate-300">
              Batal / ganti akun
            </button>
          </form>
        ) : (
          <form onSubmit={handleLogin} className="glow-border rounded-2xl bg-card border border-border p-6 space-y-3">
            <input type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required className={inputCls} />
            <input type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} required className={inputCls} />
            {error && <p className="text-xs text-strong">{error}</p>}
            <button type="submit" disabled={loading} className={btnCls}>
              {loading ? 'Masuk...' : 'Masuk'}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
