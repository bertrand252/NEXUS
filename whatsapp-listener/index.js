require('dotenv').config();
const { makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const qrcodeTerminal = require('qrcode-terminal');
const qrcode = require('qrcode');
const pino = require('pino');
const { extractText, createSeenTracker, backoffDelayMs } = require('./lib');

const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8000';
const SERVICE_API_KEY = process.env.SERVICE_API_KEY || '';
// Bisa lebih dari 1 channel — pisahin pake koma di .env, contoh:
// TARGET_JIDS=123@newsletter,456@newsletter,789@newsletter
const TARGET_JIDS = (process.env.TARGET_JIDS || process.env.TARGET_JID || '')
  .split(',')
  .map((j) => j.trim())
  .filter(Boolean);
const SOURCE_NAME = process.env.SOURCE_NAME || 'WhatsApp Channel';

const alreadySeen = createSeenTracker(500);

const FORWARD_MAX_ATTEMPTS = 3;
const FORWARD_RETRY_DELAY_MS = 5000; // flat 5s antar-percobaan, cukup buat nutup downtime redeploy singkat

async function forwardToIntel(text, attempt = 1) {
  try {
    const res = await fetch(`${BACKEND_URL}/intel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Service-Key': SERVICE_API_KEY },
      body: JSON.stringify({ sumber: SOURCE_NAME, isi_teks: text }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
    console.log('✅ Terkirim ke /intel (NEXUS bakal ringkas otomatis pake Groq).');
  } catch (err) {
    console.error(`Gagal forward ke /intel (percobaan ${attempt}/${FORWARD_MAX_ATTEMPTS}):`, err.message);
    if (attempt < FORWARD_MAX_ATTEMPTS) {
      await new Promise((resolve) => setTimeout(resolve, FORWARD_RETRY_DELAY_MS));
      return forwardToIntel(text, attempt + 1);
    }
    console.error(`❌ Nyerah forward ke /intel abis ${FORWARD_MAX_ATTEMPTS}x — pesan ini HILANG:`, text.slice(0, 100));
  }
}

// Alasan disconnect yang nandain ADA YANG SALAH terus-terusan (bukan hiccup
// jaringan biasa) — connectionReplaced: device/proses lain rebutan sesi yang
// sama (PERSIS skenario CLAUDE.md warning soal resolve-channel.js jalan bareng
// index.js), badSession: sesi corrupt, forbidden: akun kena restrict WA. Kalau
// ini kejadian BERULANG dan langsung reconnect tanpa jeda kayak dulu, jadinya
// spam reconnect ke server WA — exact pattern yang bikin kena temporary ban
// (insiden nyata, lihat CLAUDE.md). Alasan lain (connectionClosed/timedOut/dst,
// hiccup jaringan wajar) TETEP instant reconnect, itu emang perilaku normal.
const BACKOFF_REASONS = new Set([
  DisconnectReason.connectionReplaced,
  DisconnectReason.badSession,
  DisconnectReason.forbidden,
]);
let backoffStreak = 0;

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState('auth_session');

  const sock = makeWASocket({
    auth: state,
    logger: pino({ level: 'silent' }),
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr) {
      console.log('\nScan QR ini pake WhatsApp di NOMOR TUMBAL kamu (Linked Devices > Link a Device):\n');
      qrcodeTerminal.generate(qr, { small: true }); // buat lokal — kalau lewat log viewer web (Railway dll), block character-nya sering rusak
      qrcode.toDataURL(qr, { width: 300 }, (err, dataUrl) => {
        if (err) return;
        console.log('\nKalau QR di atas gak kebaca (misal lewat Railway logs), copy baris di bawah ini SELURUHNYA, paste ke address bar browser, Enter — bakal kebuka jadi gambar QR yang bisa di-scan:\n');
        console.log(dataUrl);
        console.log('');
      });
    }
    if (connection === 'close') {
      const statusCode = new Boom(lastDisconnect?.error)?.output?.statusCode;
      if (statusCode === DisconnectReason.loggedOut) {
        console.log('Logged out — hapus folder auth_session/ terus jalanin ulang buat pairing baru.');
        return;
      }
      if (BACKOFF_REASONS.has(statusCode)) backoffStreak += 1;
      const delay = backoffDelayMs(backoffStreak);
      console.log(`Koneksi putus (kode ${statusCode}). Reconnect dalam ${delay / 1000}s...`);
      setTimeout(start, delay);
    } else if (connection === 'open') {
      backoffStreak = 0; // konek lagi sukses, reset — biar disconnect berikutnya mulai dari delay terkecil lagi
      console.log('✅ Terhubung ke WhatsApp.');
      console.log(TARGET_JIDS.length
        ? `Nunggu pesan baru dari ${TARGET_JIDS.length} channel target:\n  - ${TARGET_JIDS.join('\n  - ')}`
        : 'TARGET_JIDS belum di-set di .env — mode logging doang, lihat [CHANNEL] di bawah buat nemuin JID-nya.');
    }
  });

  sock.ev.on('messages.upsert', async ({ messages }) => {
    for (const msg of messages) {
      if (!msg.message) continue;
      const jid = msg.key.remoteJid;
      const text = extractText(msg.message);

      if (jid.endsWith('@newsletter')) {
        console.log(`\n[CHANNEL] jid=${jid}`);
        console.log(`  preview: ${text.slice(0, 100)}`);
      }

      if (!TARGET_JIDS.includes(jid) || !text.trim()) continue;
      if (alreadySeen(msg.key.id)) {
        console.log(`\n[DEDUP] Pesan ${msg.key.id} dari ${jid} udah pernah diforward, skip.`);
        continue;
      }

      console.log(`\n[MATCH] Pesan baru dari channel target (${jid}) — forward ke NEXUS...`);
      await forwardToIntel(text);
    }
  });
}

start();
