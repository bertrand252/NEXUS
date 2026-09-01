import { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, LineSeries, CrosshairMode } from 'lightweight-charts';

const DARK = { bg: 'transparent', grid: '#1F2937', text: '#64748B' };

// Candlestick harga (scale kanan) + cumulative net-flow broker (scale kiri,
// SKALA BEDA — flow-nya miliaran/triliunan Rupiah, harga cuma ribuan, numpuk
// kalau 1 scale) — biar keliatan broker akumulasi PAS HARGA lagi ngapain
// (breakout/sideways/turun), bukan cuma garis polos tanpa konteks harga.
// `priceCandles`: [{time,open,high,low,close}]. `brokerLines`: [{broker,
// data:[{time,value}]}] (cumulative, udah dihitung di caller).
export function useInventoryChart(priceCandles, brokerLines) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef({ candle: null, lines: [] });

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 280,
      layout: { background: { color: DARK.bg }, textColor: DARK.text, fontFamily: 'JetBrains Mono' },
      grid: { vertLines: { color: DARK.grid }, horzLines: { color: DARK.grid } },
      timeScale: { borderColor: DARK.grid },
      rightPriceScale: { borderColor: DARK.grid },
      leftPriceScale: { visible: true, borderColor: DARK.grid },
      crosshair: { mode: CrosshairMode.Normal },
    });
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: '#10B981', downColor: '#EF4444', borderVisible: false,
      wickUpColor: '#10B981', wickDownColor: '#EF4444', priceScaleId: 'right',
    });
    seriesRef.current = { candle, lines: [] };
    chartRef.current = chart;

    const onResize = () => chart.applyOptions({ width: containerRef.current.clientWidth });
    window.addEventListener('resize', onResize);

    return () => {
      window.removeEventListener('resize', onResize);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = { candle: null, lines: [] };
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    const s = seriesRef.current;
    if (!chart || !s.candle) return;

    if (priceCandles?.length) s.candle.setData(priceCandles);

    s.lines.forEach((l) => chart.removeSeries(l));
    s.lines = (brokerLines || []).map((b) => {
      const line = chart.addSeries(LineSeries, {
        color: b.color, lineWidth: 1.5, priceScaleId: 'left', title: b.broker,
      });
      line.setData(b.data);
      return line;
    });

    chart.timeScale().fitContent();
  }, [priceCandles, brokerLines]);

  return containerRef;
}
