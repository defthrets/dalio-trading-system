import { useApi } from '../hooks/useApi';
import { getPortfolioHealth, getCorrelation } from '../lib/api';
import Panel from '../components/common/Panel';
import StatCard from '../components/common/StatCard';
import Loading from '../components/common/Loading';

const fmt = (n, d = 2) => n != null ? Number(n).toFixed(d) : '--';

export default function Risk() {
  const { data: health, loading } = useApi(getPortfolioHealth, 30000);
  const { data: corr } = useApi(getCorrelation, 60000);

  if (loading) return <Loading />;

  const h = health || {};
  const c = corr || {};

  return (
    <div>
      <div className="stat-grid" style={{ marginBottom: 16 }}>
        <StatCard
          label="Daily P&L"
          value={`${fmt(h.daily_pnl_pct)}%`}
          positive={h.daily_pnl_pct >= 0}
        />
        <StatCard
          label="Max Drawdown"
          value={`${fmt(h.drawdown_pct)}%`}
          positive={h.drawdown_pct < 5}
        />
        <StatCard
          label="Circuit Breaker"
          value={h.circuit_breaker_active ? 'HALTED' : 'OK'}
          positive={!h.circuit_breaker_active}
        />
        <StatCard
          label="Avg Correlation"
          value={fmt(c.avg_correlation ?? h.correlation_stats?.avg_correlation, 3)}
        />
      </div>

      <div className="dashboard-grid">
        <Panel title="Risk Limits">
          <table className="data-table">
            <thead>
              <tr><th>Parameter</th><th>Current</th><th>Limit</th><th>Status</th></tr>
            </thead>
            <tbody>
              <tr>
                <td>Daily Loss</td>
                <td className="mono">{fmt(h.daily_pnl_pct)}%</td>
                <td className="mono">-2.0%</td>
                <td style={{ color: (h.daily_pnl_pct ?? 0) > -2 ? 'var(--green)' : 'var(--red)' }}>
                  {(h.daily_pnl_pct ?? 0) > -2 ? 'OK' : 'BREACH'}
                </td>
              </tr>
              <tr>
                <td>Max Drawdown</td>
                <td className="mono">{fmt(h.drawdown_pct)}%</td>
                <td className="mono">10.0%</td>
                <td style={{ color: (h.drawdown_pct ?? 0) < 10 ? 'var(--green)' : 'var(--red)' }}>
                  {(h.drawdown_pct ?? 0) < 10 ? 'OK' : 'BREACH'}
                </td>
              </tr>
              <tr>
                <td>Open Positions</td>
                <td className="mono">{h.open_positions ?? 0}</td>
                <td className="mono">20</td>
                <td style={{ color: (h.open_positions ?? 0) <= 20 ? 'var(--green)' : 'var(--red)' }}>
                  {(h.open_positions ?? 0) <= 20 ? 'OK' : 'OVER'}
                </td>
              </tr>
              <tr>
                <td>Portfolio Correlation</td>
                <td className="mono">{fmt(c.avg_correlation, 3)}</td>
                <td className="mono">0.300</td>
                <td style={{ color: (c.avg_correlation ?? 0) < 0.3 ? 'var(--green)' : 'var(--red)' }}>
                  {(c.avg_correlation ?? 0) < 0.3 ? 'OK' : 'HIGH'}
                </td>
              </tr>
            </tbody>
          </table>
        </Panel>

        <Panel title="Correlation Matrix">
          {c.pairs ? (
            <table className="data-table">
              <thead>
                <tr><th>Asset A</th><th>Asset B</th><th>Correlation</th></tr>
              </thead>
              <tbody>
                {(c.pairs || []).slice(0, 10).map((p, i) => (
                  <tr key={i}>
                    <td className="mono">{p.asset_a}</td>
                    <td className="mono">{p.asset_b}</td>
                    <td className="mono" style={{
                      color: Math.abs(p.correlation) > 0.7 ? 'var(--red)' :
                             Math.abs(p.correlation) > 0.3 ? 'var(--yellow)' : 'var(--green)',
                    }}>
                      {fmt(p.correlation, 3)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty-state">Correlation data not yet computed</div>
          )}
        </Panel>
      </div>
    </div>
  );
}
