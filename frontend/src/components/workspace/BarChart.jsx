// Horizontal bar chart for grouped answers (themed, no chart dependency).
export default function BarChart({ chart }) {
  const max = Math.max(...chart.bars.map((b) => b.value), 1);
  return (
    <div className="barchart">
      <div className="barchart-title">{chart.title}</div>
      {chart.bars.map((b) => (
        <div className="bc-row" key={b.label}>
          <div className="bc-label" title={b.label}>{b.label}</div>
          <div className="bc-track">
            <div className="bc-fill" style={{ width: `${(b.value / max) * 100}%` }} />
          </div>
          <div className="bc-value">{b.value}</div>
        </div>
      ))}
      <div className="bc-axis">unit: {chart.unit}</div>
    </div>
  );
}
