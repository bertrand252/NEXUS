// Nempelin token Supabase ke tiap request yang nuju backend NEXUS (API_BASE),
// sekali doang di sini — daripada edit satu-satu tiap fetch() yang tersebar
// di semua halaman. Di-import sekali di main.jsx sebelum App di-render.
import { supabase } from './supabaseClient';
import { API_BASE } from './api';

const nativeFetch = window.fetch.bind(window);

window.fetch = async (input, init = {}) => {
  const url = typeof input === 'string' ? input : input.url;
  if (url.startsWith(API_BASE)) {
    const { data } = await supabase.auth.getSession();
    const token = data?.session?.access_token;
    if (token) {
      init = { ...init, headers: { ...(init.headers || {}), Authorization: `Bearer ${token}` } };
    }
  }
  return nativeFetch(input, init);
};
