// Profil user disimpen di localStorage — belum ada sistem auth/user di backend
// (single-user assumption, lihat CLAUDE.md), jadi ini cukup buat sekarang.
const KEY = 'nexus_profile';
const EVENT = 'nexus-profile-updated';
const DEFAULT_PROFILE = { name: 'Bertrand Leonard', role: 'Pro Trader', email: 'bertrand.leonard@student.ac.id' };

export function getProfile() {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? { ...DEFAULT_PROFILE, ...JSON.parse(raw) } : DEFAULT_PROFILE;
  } catch {
    return DEFAULT_PROFILE;
  }
}

export function saveProfile(profile) {
  try {
    localStorage.setItem(KEY, JSON.stringify(profile));
  } catch {
    // localStorage gak kebuka (private window, dll) — edit gak ke-persist tapi gak crash
  }
  window.dispatchEvent(new Event(EVENT));
}

export function initials(name) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || '?';
}

export const PROFILE_UPDATED_EVENT = EVENT;
