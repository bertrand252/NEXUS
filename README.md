# NEXUS

**NEXUS** adalah platform analisis saham IDX (Bursa Efek Indonesia) berbasis AI — menggabungkan data teknikal, data broker/institusi, berita, dan kalender ekonomi jadi sinyal trading yang bisa langsung dipakai, dikirim otomatis lewat Telegram.

🔗 **Live app**: [nexus-idx.vercel.app](https://nexus-idx.vercel.app)

## Apa yang bisa dilakukan NEXUS

- **Scanner 951 saham IDX** — scoring 0–100 (Volume, Price, Accumulation, Technical) dengan badge Mentor Call & filter breakout+volume.
- **Stock Detail** — candlestick asli, support/resistance, AI Prediction (XGBoost), broker flow lengkap (broker summary, insider activity, foreign flow, laporan keuangan, seasonality) dari data Invezgo.
- **Alert Telegram otomatis** untuk 4 gaya trading:
  - **Swing** — breakout + volume + VCP compression setup, TP/SL multi-timeframe.
  - **BSJP** (Beli Sore Jual Pagi) — deteksi saham "terbang" di sesi 2, limit-buy 3 tick di atas estimasi closing, konfirmasi fill lawan IEP closing asli.
  - **BPJS** (day trade) — momentum intraday dengan guard risk/reward.
  - Rotasi portofolio 5-slot dengan approval via tombol Telegram.
- **Whale/Block Trade Alert** — deteksi transaksi institusi besar & split order, otomatis memantau ticker dengan posisi aktif.
- **AI Daily Briefing** — sintesis berita otomatis (WhatsApp + Telegram channel listener) jadi ringkasan harian + rekomendasi berbasis sinyal real, bukan tebakan LLM.
- **Portfolio Simulation, Trading Journal, Analytics** — money management, jurnal trading dengan auto-suggestion, rekap win-rate.
- **Market Events** — kalender ekonomi (Forex Factory) + aksi korporasi (RUPS/dividen) dari Invezgo.
- **History NEXUS** — riwayat lengkap semua call otomatis (entry, target, SL, status, PnL).

## Tech Stack

| Layer | Teknologi |
|---|---|
| Backend | FastAPI (Python), scheduler `asyncio` (bukan cron terpisah) |
| Database | Supabase (PostgreSQL) + Row Level Security |
| Auth | Supabase Auth (JWT) + service key untuk service-to-service |
| AI/LLM | Groq (`openai/gpt-oss-120b`) |
| Data saham | yfinance + Invezgo API (broker flow, accumulation, corporate action) |
| Alert | Telegram Bot API |
| Ingest berita | WhatsApp (Baileys) + Telegram channel listener |
| Frontend | React (Vite) + Tailwind + Chart.js + lightweight-charts |
| Hosting | Railway (backend + WhatsApp listener) · Vercel (frontend) |

## Struktur Repo

```
NEXUS/
├── backend/            # FastAPI app, scheduler, routers, AI/scoring logic
├── frontend-react/     # React (Vite) dashboard
└── whatsapp-listener/  # Baileys listener, forward berita channel WA ke backend
```

## Menjalankan Secara Lokal

Backend dan WhatsApp listener sudah jalan 24/7 di Railway — yang biasanya perlu dijalankan lokal cuma frontend:

```bash
cd frontend-react
npm install
npm run dev
```

Butuh file `.env` di `frontend-react/` berisi `VITE_API_BASE`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`.

Untuk menjalankan backend secara lokal (debugging):

```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8000
```

Backend butuh environment variable (lihat `backend/.env.example`): `SUPABASE_URL`, `SUPABASE_KEY` (service role), `GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`, `SERVICE_API_KEY`, `INVEZGO_API_KEY` (opsional).

## Testing

```bash
cd backend
pytest
```

## Catatan

NEXUS adalah alat bantu analisis, **bukan rekomendasi investasi**. Semua sinyal dihasilkan dari data historis/teknikal dan tidak menjamin hasil di masa depan. Awalnya dikembangkan sebagai proyek capstone, sekarang terus dikembangkan sebagai produk pribadi jangka panjang.
