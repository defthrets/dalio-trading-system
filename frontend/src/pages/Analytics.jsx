import { useApi } from '../hooks/useApi';
import { getPaperAnalytics, getCorrelation, getSentiment } from '../lib/api';
import Panel from '../components/common/Panel';
import StatCard from '../components/common/StatCard';
import Loading from '../components/common/Loading';

const fmt = (n, d = 2) => n != null ? Number(n).toFixed(d) : '--';

export default function Analytics() {
  const { data: analytics, loading } = useApi(getPaperAnalytics, 60000);
  const { data: sentiment } = useApi(getSentiment, 60000);

  if (loading) return <Loading />;

  const a = analytics || {};
  const s = sentiment || {};

  return (
    <div>
      <div className="stat-grid" style={{ marginBottom: 16 }}>
        <StatCard label="Win Rate" value={`${fmt(a.win_rate, 1)}%`} positive={a.win_rate > 50} />
        <StatCard label="Profit Factor" value={fmt(a.profit_factor)} positive={a.profit_factor > 1} />
        <StatCard label="Sharpe Ratio" value={fmt(a.sharpe)} positive={a.sharpe > 1} />
        <StatCard label="Total P&L" value={`$${fmt(a.total_pnl)}`} positive={a.total_pnl > 0} />
        <StatCard label="Avg Win" value={`$${fmt(a.avg_win)}`} />
        <StatCard label="Avg Loss" value={`$${fmt(a.avg_loss)}`} />
      </div>

      <div className="dashboard-grid">
        <Panel title="Market Sentiment">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ fontSize: 13, color: 'var(--text-1)' }}>
              Articles Analyzed: <strong>{s.total_articles ?? 0}</strong>
            </div>
            <div style={{ fontSize: 13, color: 'var(--text-1)' }}>
              Dominant Quadrant: <strong style={{ color: 'var(--primary)' }}>
                {(s.dominant_quadrant || 'unknown').replace('_', ' ').toUpperCase()}
              </strong>
            </div>
            <div style={{ fontSize: 13, color: s.conflict_risk_elevated ? 'var(--red)' : 'var(--green)' }}>
              Conflict Risk: {s.conflict_risk_elevated ? 'ELEVATED' : 'Normal'}
              {s.conflict_risk_articles > 0 && ` (${s.conflict_risk_articles} articles)`}
            </div>
          </div>
        </Panel>

        <Panel title="Quadrant Sentiment Breakdown">
          {s.quadrant_sentiment ? (
            <table className="data-table">
              <thead>
                <tr><th>Quadrant</th><th>Articles</th><th>Avg Score</th><th>Bullish %</th></tr>
              </thead>
              <tbody>
                {Object.entries(s.quadrant_sentiment).map(([q, v]) => (
                  <tr key={q}>
                    <td>{q.replace('_', ' ')}</td>
                    <td className="mono">{v.article_count}</td>
                    <td className="mono" style={{
                      color: v.avg_score > 0 ? 'var(--green)' : v.avg_score < 0 ? 'var(--red)' : 'var(--text-1)',
                    }}>
                      {fmt(v.avg_score, 3)}
                    </td>
                    <td className="mono">{fmt(v.bullish_pct, 1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty-state">No sentiment data</div>
          )}
        </Panel>
      </div>
    </div>
  );
}
