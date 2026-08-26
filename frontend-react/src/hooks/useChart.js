import { useEffect, useRef } from 'react';
import { Chart } from 'chart.js/auto';

// Creates a Chart.js instance on the canvas whenever `config` changes, destroys it on cleanup.
// `config` should be `null` while there's nothing to render yet.
export function useChart(config) {
  const canvasRef = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    if (!config || !canvasRef.current) return;
    chartRef.current = new Chart(canvasRef.current, config);
    return () => {
      chartRef.current?.destroy();
      chartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(config)]);

  return canvasRef;
}
