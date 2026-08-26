require('dotenv').config();
const { makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const qrcode = require('qrcode-terminal');
const pino = require('pino');

const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8000';
// Bisa lebih dari 1 channel — pisahin pake koma di .env, contoh:
// TARGET_JIDS=123@newsletter,456@newsletter,789@newsletter
const TARGET_JIDS = (process.env.TARGET_JIDS || process.env.TARGET_JID || '')
  .split(',')
  .map((j) => j.trim())
  .filter(Boolean);
const SOURCE_NAME = process.env.SOURCE_NAME || 'WhatsApp Channel';

async function forwardToIntel(text) {
  try {
    const res = await fetch(`${BACKEND_URL}/intel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sumber: SOURCE_NAME, isi_teks: text }),
    });
    if (!res.ok) console.error('Gagal forward ke /intel:', res.status, await res.text());
    else console.log('✅ Terkirim ke /intel (NEXUS bakal ringkas otomatis pake Groq).');
  } catch (err) {
    console.error('Gagal forward ke /intel:', err.message);
  }
}

function extractText(message) {
  return (
    message.conversation ||
    message.extendedTextMessage?.text ||
    message.imageMessage?.caption ||
    message.videoMessage?.caption ||
    ''
  );
}

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
      qrcode.generate(qr, { small: true });
    }
    if (connection === 'close') {
      const statusCode = new Boom(lastDisconnect?.error)?.output?.statusCode;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
      console.log('Koneksi putus. Reconnect:', shouldReconnect);
      if (shouldReconnect) start();
      else console.log('Logged out — hapus folder auth_session/ terus jalanin ulang buat pairing baru.');
    } else if (connection === 'open') {
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

      console.log(`\n[MATCH] Pesan baru dari channel target (${jid}) — forward ke NEXUS...`);
      await forwardToIntel(text);
    }
  });
}

start();
