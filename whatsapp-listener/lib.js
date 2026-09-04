// Fungsi murni whatsapp-listener — dipisah dari index.js biar bisa di-require
// buat testing TANPA nyalain koneksi WA beneran (index.js manggil start()
// begitu di-require, langsung connect).

function extractText(message) {
  return (
    message.conversation ||
    message.extendedTextMessage?.text ||
    message.imageMessage?.caption ||
    message.videoMessage?.caption ||
    ''
  );
}

// Message ID yang udah diforward — Baileys ngirim ulang pesan yang numpuk pas
// listener OFFLINE begitu reconnect (type:'append', lihat baileys
// messages-recv.js: upsertMessage(msg, node.attrs.offline ? 'append' : 'notify')).
// Railway sering redeploy, jadi proses baru abis restart WAJAR nerima ulang
// pesan yang overlap sama window sebelum restart — tanpa dedup ini keforward
// DUA KALI ke /intel. Cap ukuran biar gak leak memory (in-memory doang, gak
// perlu persist ke disk — cuma buat nyegah dobel SEKALI reconnect).
function createSeenTracker(maxSize = 500) {
  const seen = new Set();
  return function alreadySeen(id) {
    if (!id) return false;
    if (seen.has(id)) return true;
    seen.add(id);
    if (seen.size > maxSize) {
      seen.delete(seen.values().next().value);
    }
    return false;
  };
}

// Backoff eksponensial buat disconnect reason yang nandain ADA YANG SALAH
// terus-terusan (bukan hiccup jaringan) — reconnect instan tanpa jeda bikin
// spam ke server WA (insiden temp ban nyata, lihat CLAUDE.md).
function backoffDelayMs(streak, baseMs = 5000, maxMs = 5 * 60 * 1000) {
  if (streak <= 0) return 0;
  return Math.min(baseMs * 2 ** (streak - 1), maxMs);
}

module.exports = { extractText, createSeenTracker, backoffDelayMs };
