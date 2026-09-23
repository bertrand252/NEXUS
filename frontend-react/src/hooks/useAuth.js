import { useEffect, useState } from 'react';
import { supabase } from '../lib/supabaseClient';

// Baca claim `aal` dari JWT: "aal1" = cuma password, "aal2" = udah lolos MFA.
// Decode doang tanpa verifikasi — yang beneran jaga itu backend (auth_guard.py).
function aalOf(session) {
  try {
    const b64 = session.access_token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(b64)).aal;
  } catch {
    return null;
  }
}

export function useAuth() {
  const [session, setSession] = useState(undefined); // undefined = belum dicek, null = gak login

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => setSession(data.session));
    const { data: sub } = supabase.auth.onAuthStateChange((_event, newSession) => setSession(newSession));
    return () => sub.subscription.unsubscribe();
  }, []);

  return { session, loading: session === undefined, mfaPassed: !!session && aalOf(session) === 'aal2' };
}
