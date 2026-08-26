// SMA dihitung client-side dari candle yang udah di-fetch — gak perlu round-trip
// ke backend, datanya (close price) udah ada di tangan.

export function sma(values, period) {
  const out = [];
  for (let i = 0; i < values.length; i++) {
    if (i < period - 1) continue;
    const slice = values.slice(i - period + 1, i + 1);
    out.push(slice.reduce((a, b) => a + b, 0) / period);
  }
  return out; // panjangnya values.length - period + 1
}

export function smaLine(candles, period) {
  const closes = candles.map((c) => c.close);
  const times = candles.map((c) => c.time);
  return sma(closes, period).map((v, i) => ({ time: times[i + period - 1], value: v }));
}
