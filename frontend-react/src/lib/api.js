// || (bukan ??) sengaja — VITE_API_BASE kosong di .env itu tetep string "",
// bukan undefined, jadi ?? gak bakal fallback ke default-nya.
export const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

export async function apiFetch(path, options) {
  const res = await fetch(`${API_BASE}${path}`, options);
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail || `Request gagal (${res.status})`);
  }
  return res.json();
}
