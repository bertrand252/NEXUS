// Self-check pure functions lib.js — assert-based, gak butuh framework, gak
// nyentuh koneksi WA sama sekali (safe dijalanin kapan aja: node lib.test.js).
const assert = require('assert');
const { extractText, createSeenTracker, backoffDelayMs } = require('./lib');

// extractText
assert.strictEqual(extractText({ conversation: 'halo' }), 'halo');
assert.strictEqual(extractText({ extendedTextMessage: { text: 'halo2' } }), 'halo2');
assert.strictEqual(extractText({ imageMessage: { caption: 'caption gambar' } }), 'caption gambar');
assert.strictEqual(extractText({}), '');

// createSeenTracker: dedup + cap ukuran
{
  const seen = createSeenTracker(3);
  assert.strictEqual(seen('a'), false); // pertama kali, belum pernah
  assert.strictEqual(seen('a'), true);  // udah pernah -> kedeteksi dobel
  assert.strictEqual(seen(undefined), false); // id kosong (msg.key.id null) jangan nge-block
  seen('b'); seen('c'); seen('d'); // cap=3, dorong 'a' keluar
  assert.strictEqual(seen('a'), false); // 'a' udah ke-evict, dianggep baru lagi (trade-off cap memory)
}

// backoffDelayMs: 0 pas streak kosong, eksponensial abis itu, clamp ke max
assert.strictEqual(backoffDelayMs(0), 0);
assert.strictEqual(backoffDelayMs(1, 5000, 300000), 5000);
assert.strictEqual(backoffDelayMs(2, 5000, 300000), 10000);
assert.strictEqual(backoffDelayMs(3, 5000, 300000), 20000);
assert.strictEqual(backoffDelayMs(20, 5000, 300000), 300000); // clamp ke max, gak infinite

console.log('lib.test.js: semua pass');
