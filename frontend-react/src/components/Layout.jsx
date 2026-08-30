import { useState } from 'react';
import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';

export default function Layout() {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex min-h-screen">
      <Sidebar open={open} onClose={() => setOpen(false)} />
      {open && (
        <div className="no-print fixed inset-0 bg-black/60 z-10 md:hidden" onClick={() => setOpen(false)}></div>
      )}
      {!open && (
        <button
          onClick={() => setOpen(true)}
          className="no-print md:hidden fixed top-4 left-4 z-30 w-9 h-9 rounded-lg bg-card border border-border flex items-center justify-center text-white"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M3 6H21M3 12H21M3 18H21" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
        </button>
      )}
      <main className="md:ml-64 flex-1 flex flex-col min-h-screen">
        <Outlet />
      </main>
    </div>
  );
}
