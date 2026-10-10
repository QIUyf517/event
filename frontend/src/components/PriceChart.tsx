import type { MarketBar } from "../types";

export function PriceChart({ rows }: { rows: MarketBar[] }) {
  if (rows.length < 2) return <div className="empty-state">暂无足够的价格数据。</div>;
  const width = 900;
  const height = 230;
  const pad = 18;
  const values = rows.map((row) => row.close);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const points = rows.map((row, index) => {
    const x = pad + index * ((width - pad * 2) / (rows.length - 1));
    const y = height - pad - ((row.close - min) / span) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");

  return (
    <figure className="price-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="最近价格轨迹">
        <line x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} className="chart-axis" />
        <polyline points={points} className="chart-line" />
      </svg>
      <figcaption>
        <span>{rows[0].trade_date}</span>
        <strong>{rows.at(-1)?.close.toFixed(4)} 元</strong>
        <span>{rows.at(-1)?.trade_date}</span>
      </figcaption>
    </figure>
  );
}
