export default function StatCard({ label, value, change, positive }) {
  const cls = positive === true ? 'positive' : positive === false ? 'negative' : '';
  return (
    <div className="stat-card">
      <div className="label">{label}</div>
      <div className={`value ${cls}`}>{value ?? '--'}</div>
      {change != null && <div className="change">{change}</div>}
    </div>
  );
}
