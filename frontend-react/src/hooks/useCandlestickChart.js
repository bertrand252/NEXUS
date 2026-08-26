import { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, LineSeries, CrosshairMode } from 'lightweight-charts';
import { smaLine } from '../lib/indicators';

const DARK = { bg: 'transparent', grid: '#1F2937', text: '#64748B' };

// Creates a lightweight-charts candlestick chart with SMA5/10/15 overlaid.
// `candles` is [{time, open, high, low, close}]. `levels` (optional) is
// {support, resistance} from backend/levels.py, drawn as horizontal price lines.
export function useCandlestickChart(candles, levels) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef({});
  const priceLinesRef = useRef([]);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 280,
      layout: { background: { color: DARK.bg }, textColor: DARK.text, fontFamily: 'JetBrains Mono' },
      grid: { vertLines: { color: DARK.grid }, horzLines: { color: DARK.grid } },
      timeScale: { borderColor: DARK.grid },
      rightPriceScale: { borderColor: DARK.grid },
      crosshair: { mode: CrosshairMode.Normal },
    });

    const candle = chart.addSeries(CandlestickSeries, {
      upColor: '#10B981', downColor: '#EF4444', borderVisible: false,
      wickUpColor: '#10B981', wickDownColor: '#EF4444',
    });
    const sma5 = chart.addSeries(LineSeries, { color: '#7DD3FC', lineWidth: 1, title: 'SMA5' });   // biru muda
    const sma10 = chart.addSeries(LineSeries, { color: '#F97316', lineWidth: 1, title: 'SMA10' }); // orange
    const sma15 = chart.addSeries(LineSeries, { color: '#991B1B', lineWidth: 1, title: 'SMA15' }); // merah pekat

    seriesRef.current = { candle, sma5, sma10, sma15 };
    chartRef.current = chart;

    const onResize = () => chart.applyOptions({ width: containerRef.current.clientWidth });
    window.addEventListener('resize', onResize);

    return () => {
      window.removeEventListener('resize', onResize);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = {};
    };
  }, []);

  useEffect(() => {
    const s = seriesRef.current;
    if (!s.candle || !candles || candles.length === 0) return;

    s.candle.setData(candles);
    s.sma5.setData(smaLine(candles, 5));
    s.sma10.setData(smaLine(candles, 10));
    s.sma15.setData(smaLine(candles, 15));

    chartRef.current.timeScale().fitContent();
  }, [candles]);

  useEffect(() => {
    const s = seriesRef.current;
    if (!s.candle) return;

    priceLinesRef.current.forEach((line) => s.candle.removePriceLine(line));
    priceLinesRef.current = [];

    if (!levels) return;
    priceLinesRef.current.push(s.candle.createPriceLine({
      price: levels.support, color: '#10B981', lineWidth: 2, lineStyle: 2,
      axisLabelVisible: true, title: 'Support',
    }));
    priceLinesRef.current.push(s.candle.createPriceLine({
      price: levels.resistance, color: '#EF4444', lineWidth: 2, lineStyle: 2,
      axisLabelVisible: true, title: 'Resistance',
    }));
  }, [levels]);

  return containerRef;
}
