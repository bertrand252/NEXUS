import { useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { supabase } from '../lib/supabaseClient';
import { useAuth } from '../hooks/useAuth';
import logo from '../assets/NEXUS.png';

export default function Login() {
  const { session, loading: sessionLoading } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  if (!sessionLoading && session) return <Navigate to="/dashboard" replace />;

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    setLoading(false);
    if (error) { setError(error.message); return; }
    navigate('/dashboard', { replace: true });
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-base px-4">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center mb-8">
          <img src={logo} alt="NEXUS" className="w-14 h-14 rounded-xl mb-3" />
          <p className="text-lg font-bold text-white tracking-tight">NEXUS</p>
          <p className="text-[11px] text-slate-500 tracking-wide">IDX INTELLIGENCE</p>
        </div>

        <form onSubmit={handleSubmit} className="glow-border rounded-2xl bg-card border border-border p-6 space-y-3">
          <input
            type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required
            className="w-full bg-card2 border border-border rounded-lg px-3 py-2.5 text-sm text-white focus:outline-none focus:border-accent/60"
          />
          <input
            type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} required
            className="w-full bg-card2 border border-border rounded-lg px-3 py-2.5 text-sm text-white focus:outline-none focus:border-accent/60"
          />
          {error && <p className="text-xs text-strong">{error}</p>}
          <button
            type="submit" disabled={loading}
            className="w-full text-sm font-semibold px-4 py-2.5 rounded-lg bg-accent hover:bg-accent/90 text-white transition disabled:opacity-50"
          >
            {loading ? 'Masuk...' : 'Masuk'}
          </button>
        </form>
      </div>
    </div>
  );
}
