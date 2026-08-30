import { NavLink } from 'react-router-dom';
import logo from '../assets/NEXUS.png';
import { useProfile } from '../hooks/useProfile';
import { initials } from '../lib/profile';

const NAV_ITEMS = [
  {
    to: '/dashboard',
    label: 'Dashboard',
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none"><rect x="3" y="3" width="7" height="9" rx="1.5" stroke="currentColor" strokeWidth="2" /><rect x="14" y="3" width="7" height="5" rx="1.5" stroke="currentColor" strokeWidth="2" /><rect x="14" y="12" width="7" height="9" rx="1.5" stroke="currentColor" strokeWidth="2" /><rect x="3" y="16" width="7" height="5" rx="1.5" stroke="currentColor" strokeWidth="2" /></svg>
    ),
  },
  {
    to: '/scanner',
    label: 'Scanner',
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" /><path d="M21 21L16.65 16.65" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
    ),
  },
  {
    to: '/stock-detail',
    label: 'Stock Detail',
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M4 19V13M10 19V9M16 19V5M22 19V11" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
    ),
  },
  {
    to: '/market-events',
    label: 'Market Events',
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none"><rect x="3" y="4" width="18" height="17" rx="2" stroke="currentColor" strokeWidth="2" /><path d="M3 9H21" stroke="currentColor" strokeWidth="2" /><path d="M8 2V6M16 2V6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
    ),
  },
  {
    to: '/portfolio-simulation',
    label: 'Portfolio Simulation',
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M13 2L3 14H12L11 22L21 10H12L13 2Z" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" /></svg>
    ),
  },
  {
    to: '/journal',
    label: 'Journal',
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none"><rect x="3" y="4" width="18" height="17" rx="2" stroke="currentColor" strokeWidth="2" /><path d="M3 10H21" stroke="currentColor" strokeWidth="2" /></svg>
    ),
  },
  {
    to: '/analytics',
    label: 'Analytics',
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M3 3V21H21" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /><path d="M7 15L11 10L14 13L19 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
    ),
  },
  {
    to: '/settings',
    label: 'Settings',
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="2" /><path d="M19.4 15A1.65 1.65 0 0 0 21 13.35V10.65A1.65 1.65 0 0 0 19.4 9L18.9 8.9A1.65 1.65 0 0 1 17.6 8L17.4 7.5A1.65 1.65 0 0 1 18 5.6L18.1 5.5A1.65 1.65 0 0 0 15.65 3L15.5 3.1A1.65 1.65 0 0 1 13.6 3.7L13.1 3.5A1.65 1.65 0 0 0 11.4 3H8.6A1.65 1.65 0 0 0 6.9 3.5L6.4 3.7A1.65 1.65 0 0 1 4.5 3.1L4.4 3A1.65 1.65 0 0 0 1.95 5.5L2.05 5.6A1.65 1.65 0 0 1 2.65 7.5L2.45 8A1.65 1.65 0 0 1 1.15 8.9L1.05 9A1.65 1.65 0 0 0 0 10.65V13.35A1.65 1.65 0 0 0 1.05 15L1.15 15.1A1.65 1.65 0 0 1 2.45 16L2.65 16.5A1.65 1.65 0 0 1 2.05 18.4L1.95 18.5A1.65 1.65 0 0 0 4.4 21L4.5 20.9A1.65 1.65 0 0 1 6.4 20.3L6.9 20.5A1.65 1.65 0 0 0 8.6 21H11.4A1.65 1.65 0 0 0 13.1 20.5L13.6 20.3A1.65 1.65 0 0 1 15.5 20.9L15.65 21A1.65 1.65 0 0 0 18.1 18.5L18 18.4A1.65 1.65 0 0 1 17.6 16.5L17.4 16A1.65 1.65 0 0 1 18.9 15.1Z" stroke="currentColor" strokeWidth="1.6" /></svg>
    ),
  },
];

export default function Sidebar({ open = false, onClose }) {
  const profile = useProfile();
  return (
    <aside
      className={`no-print w-64 shrink-0 bg-[#0D1220] border-r border-border flex flex-col justify-between fixed h-screen z-20
        transition-transform duration-200 md:translate-x-0 ${open ? 'translate-x-0' : '-translate-x-full'}`}
    >
      <div>
        <div className="flex items-center gap-3 px-6 py-6 border-b border-border">
          <div className="w-10 h-10 shrink-0"><img src={logo} alt="NEXUS" className="w-full h-full object-cover" /></div>
          <div className="flex-1 min-w-0">
            <p className="text-[15px] font-bold text-white leading-none tracking-tight">NEXUS</p>
            <p className="text-[10px] text-slate-500 mt-1 tracking-wide">IDX INTELLIGENCE</p>
          </div>
          <button onClick={onClose} className="md:hidden w-7 h-7 rounded-lg flex items-center justify-center text-slate-400 hover:text-white transition shrink-0">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M18 6L6 18M6 6L18 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
          </button>
        </div>
        <nav className="px-3 py-4 space-y-1 text-sm">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              onClick={onClose}
              className={({ isActive }) =>
                isActive
                  ? 'nav-active flex items-center gap-3 px-3 py-2.5 rounded-r-md text-white font-medium'
                  : 'flex items-center gap-3 px-3 py-2.5 rounded-md text-slate-400 hover:text-white hover:bg-white/5 transition'
              }
            >
              {item.icon}
              {item.label}
            </NavLink>
          ))}
        </nav>
      </div>
      <div className="px-4 py-4 border-t border-border">
        <button className="w-full flex items-center gap-2 justify-center text-xs text-slate-500 hover:text-slate-300 py-2 mb-2 transition">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M15 18L9 12L15 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
          Collapse
        </button>
        <NavLink to="/settings" className="flex items-center gap-3 px-2 py-2 rounded-lg hover:bg-white/5 transition cursor-pointer">
          <div className="w-9 h-9 rounded-full bg-gradient-to-br from-cyan to-accent flex items-center justify-center text-xs font-bold text-white shrink-0">{initials(profile.name)}</div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-white truncate">{profile.name}</p>
            <p className="text-[11px] text-slate-500 truncate">{profile.role}</p>
          </div>
        </NavLink>
      </div>
    </aside>
  );
}
