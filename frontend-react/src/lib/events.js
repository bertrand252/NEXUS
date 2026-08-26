// Shared impact styling for Forex Factory events (GET /market-events), impact is
// one of High/Medium/Low/Holiday as returned by backend/forex_factory.py.
export const IMPACT_BADGE_CLASS = {
  High: 'bg-strong/10 text-strong border-strong/30',
  Medium: 'bg-moderate/10 text-moderate border-moderate/30',
  Low: 'bg-weak/10 text-weak border-weak/30',
  Holiday: 'bg-none/10 text-slate-400 border-none/30',
};

export const IMPACT_DOT_CLASS = {
  High: 'bg-strong',
  Medium: 'bg-moderate',
  Low: 'bg-weak',
  Holiday: 'bg-none',
};

// "2026-08-24" -> "24 Aug"
export function formatShortDate(isoDate) {
  return new Date(`${isoDate}T00:00:00`).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
}
