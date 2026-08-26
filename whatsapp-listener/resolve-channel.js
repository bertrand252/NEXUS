// Resolve JID channel WhatsApp dari link invite-nya, tanpa perlu nunggu post baru.
// Pakai: node resolve-channel.js https://whatsapp.com/channel/0029XXXXXXXXXXXXXXXX
require('dotenv').config();
const { makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const pino = require('pino');

const inviteArg = process.argv[2];
if (!inviteArg) {
  console.error('Pakai: node resolve-channel.js <link-invite-channel>');
  process.exit(1);
}
const inviteCode = inviteArg.split('/channel/')[1]?.split(/[?#]/)[0] || inviteArg;

async function main() {
  const { state } = await useMultiFileAuthState('auth_session'); // reuse sesi yang udah login, gak perlu scan QR lagi
  const sock = makeWASocket({ auth: state, logger: pino({ level: 'silent' }) });

  const timeout = setTimeout(() => {
    console.error('\n⏱ Timeout — 15 detik gak connect-connect. Kemungkinan besar `npm start` MASIH JALAN di terminal lain (stop dulu pake Ctrl+C, baru coba lagi).');
    process.exit(1);
  }, 15000);

  sock.ev.on('connection.update', async ({ connection, lastDisconnect }) => {
    if (connection === 'close') {
      clearTimeout(timeout);
      const statusCode = new Boom(lastDisconnect?.error)?.output?.statusCode;
      console.error(`\n❌ Koneksi ke WhatsApp ketutup (kode ${statusCode}).`);
      if (statusCode === DisconnectReason.loggedOut) {
        console.error('Sesi logged out — hapus folder auth_session/ terus pairing ulang lewat `npm start`.');
      } else {
        console.error('Kemungkinan besar `npm start` masih jalan di terminal lain (rebutan sesi yang sama). Stop itu dulu (Ctrl+C), baru coba lagi.');
      }
      process.exit(1);
    }
    if (connection !== 'open') return;
    clearTimeout(timeout);
    try {
      const meta = await sock.newsletterMetadata('invite', inviteCode);
      const name = meta.name || meta.thread_metadata?.name?.text || meta.threadMetadata?.name?.text;
      console.log('\n✅ Ketemu:');
      console.log('  Nama :', name || '(gak ke-baca, cek raw di bawah)');
      console.log('  JID  :', meta.id);
      console.log('\nCopy JID di atas ke .env -> TARGET_JID=' + meta.id);
      console.log('\n--- raw metadata (buat debug kalau nama masih kosong) ---');
      console.log(JSON.stringify(meta, null, 2));
    } catch (err) {
      console.error('Gagal resolve:', err.message);
    } finally {
      process.exit(0);
    }
  });
}

main();
