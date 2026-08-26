const META = {
  Strong: { dot: '🔴', cls: 'bg-strong/10 text-strong border-strong/30', label: 'Strong' },
  Moderate: { dot: '🟠', cls: 'bg-moderate/10 text-moderate border-moderate/30', label: 'Moderate' },
  Weak: { dot: '🟡', cls: 'bg-weak/10 text-weak border-weak/30', label: 'Weak' },
  None: { dot: '⚪', cls: 'bg-none/10 text-slate-400 border-none/30', label: 'No Signal' },
};

// signal string ("Strong"/"Moderate"/"Weak"/"None") -> {dot, cls, label}, as returned by the backend.
export function signalMeta(signal) {
  return META[signal] || META.None;
}

const ZONE_LABEL = { Strong: 'Very High Accumulation', Moderate: 'Moderate Accumulation', Weak: 'Weak Accumulation', None: 'No Signal' };
const ZONE_COLOR = { Strong: 'text-strong', Moderate: 'text-moderate', Weak: 'text-weak', None: 'text-slate-400' };

export function zoneLabel(signal) { return ZONE_LABEL[signal] || 'No Signal'; }
export function zoneColorClass(signal) { return ZONE_COLOR[signal] || 'text-slate-400'; }
