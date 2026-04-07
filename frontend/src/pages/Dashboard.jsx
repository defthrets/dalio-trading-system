import { useApi } from '../hooks/useApi';
import { getStatus, getPortfolioHealth, getPaperEquity, getSignals, getQuadrant } from '../lib/api';
import StatCard from '../components/common/StatCard';
import Panel from '../components/common/Panel';
import Loading from '../components/common/Loading';
import EquityChart from '../components/charts/EquityChart';

const fmt = (n, d = 2) => n != null ? Number(n).toFixed(d) : '--';
const fmtCur = (n) => n != null ? `$${Number(n).toLocaleString('en-AU', { minimumFractionDigits: 2 })}` : '--';

export default function Dashboard() {
  const { data: status, loading: sl } = useApi(getStatus, 15000);
  const { data: health } = useApi(getPortfolioHealth, 30000);
  const { data: equity } = useApi(getPaperEquity, 60000);
  const { data: signals } = useApi(getSignals, 30000);
  const { data: quadrant } = useApi(getQuadrant, 60000);

  if (sl) return <Loading />;

  const nav = health?.equity ?? health?.nav ?? 0;
  const pnl = health?.daily_pnl_pct ?? 0;
  const dd = health?.drawdown_pct ?? 0;
  const trades = health?.total_trades ?? 0;
  const topSignals = (signals?.signals || signals || []).slice(0, 5);

  return (
    <div>
      {/* Stats row */}
      <div className="stat-grid" style={{ marginBottom: 16 }}>
        <StatCard label="NAV" value={fmtCur(nav)} />
        <StatCard
          label="Today P&L"
          value={`${pnl >= 0 ? '+' : ''}${fmt(pnl)}%`}
          positive={pnl >= 0}
        />
        <StatCard
          label="Drawdown"
          value={`${fmt(dd)}%`}
          positive={dd < 5}
        />
        <StatCard label="Open Positions" value={health?.open_positions ?? 0} />
        <StatCard label="Total Trades" value={trades} />
        <StatCard
          label="Quadrant"
          value={(quadrant?.quadrant || 'unknown').replace('_', ' ').toUpperCase()}
        />
      </div>

      {/* Main grid */}
      <div className="dashboard-grid">
        {/* Equity Chart */}
        <Panel title="Equity Curve" className="full-width">
          <EquityChart data={equity?.curve || equity || []} height={250} />
        </Panel>

        {/* Top Signals */}
        <Panel title="Top Signals">
          {topSignals.length === 0 ? (
            <div className="empty-state">No signals</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Action</th>
                  <th>Confidence</th>
                  <th>Price</th>
                </tr>
              </thead>
              <tbody>
                {topSignals.map((s, i) => (
                  <tr key={i}>
                    <td className="mono">{s.ticker}</td>
                    <td>
                      <span className={`tag tag-${s.action?.toLowerCase()}`}>
                        {s.action}
                      </span>
                    </td>
                    <td className="mono">{fmt(s.confidence, 0)}%</td>
                    <td className="mono">{fmtCur(s.price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        {/* Quadrant Info */}
        <Panel title="Economic Quadrant">
          <div style={{ padding: '8px 0' }}>
            <div style={{
              fontSize: 18,
              fontFamily: 'var(--font-mono)',
              fontWeight: 700,
              color: 'var(--primary)',
              marginBottom: 12,
            }}>
              {(quadrant?.quadrant || 'unknown').replace('_', ' ').toUpperCase()}
            </div>
            <p style={{ color: 'var(--text-1)', fontSize: 13, lineHeight: 1.6 }}>
              {quadrant?.description || 'Quadrant data loading...'}
            </p>
            {quadrant?.conflict_risk && (
              <div style={{
                marginTop: 12,
                padding: '8px 12px',
                background: 'var(--red-dim)',
                borderRadius: 'var(--radius-sm)',
                color: 'var(--red)',
                fontSize: 12,
                fontWeight: 600,
              }}>
                CONFLICT RISK ELEVATED
              </div>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}
