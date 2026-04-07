import { useApi } from '../hooks/useApi';
import { getSignals } from '../lib/api';
import Panel from '../components/common/Panel';
import Loading from '../components/common/Loading';

const fmt = (n, d = 2) => n != null ? Number(n).toFixed(d) : '--';
const fmtCur = (n) => n != null ? `$${Number(n).toLocaleString('en-AU', { minimumFractionDigits: 2 })}` : '--';

export default function Signals() {
  const { data, loading, refresh } = useApi(getSignals, 30000);

  if (loading) return <Loading />;

  const signals = data?.signals || data || [];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, fontWeight: 600 }}>Trade Signals</h2>
        <button className="btn btn-sm" onClick={refresh}>Refresh</button>
      </div>

      <Panel>
        {signals.length === 0 ? (
          <div className="empty-state">No signals generated yet. Wait for the next scan cycle.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Action</th>
                <th>Confidence</th>
                <th>Price</th>
                <th>SL</th>
                <th>TP</th>
                <th>R:R</th>
                <th>RSI</th>
                <th>Quadrant Fit</th>
                <th>Sentiment</th>
              </tr>
            </thead>
            <tbody>
              {signals.map((s, i) => (
                <tr key={i}>
                  <td className="mono" style={{ fontWeight: 600, color: 'var(--text-0)' }}>
                    {s.ticker}
                  </td>
                  <td>
                    <span className={`tag tag-${s.action?.toLowerCase()}`}>{s.action}</span>
                  </td>
                  <td className="mono">
                    <span style={{
                      color: s.confidence > 70 ? 'var(--green)' : s.confidence > 50 ? 'var(--yellow)' : 'var(--red)',
                    }}>
                      {fmt(s.confidence, 0)}%
                    </span>
                  </td>
                  <td className="mono">{fmtCur(s.price)}</td>
                  <td className="mono" style={{ color: 'var(--red)' }}>{fmtCur(s.stop_loss)}</td>
                  <td className="mono" style={{ color: 'var(--green)' }}>{fmtCur(s.take_profit)}</td>
                  <td className="mono">{fmt(s.risk_reward, 1)}</td>
                  <td className="mono">{fmt(s.rsi, 1)}</td>
                  <td>
                    <span style={{
                      fontSize: 11,
                      color: s.quadrant_fit === 'strong' ? 'var(--green)' :
                             s.quadrant_fit === 'avoid' ? 'var(--red)' : 'var(--text-1)',
                    }}>
                      {s.quadrant_fit || '--'}
                    </span>
                  </td>
                  <td>
                    <span style={{
                      fontSize: 11,
                      color: s.sentiment === 'positive' ? 'var(--green)' :
                             s.sentiment === 'negative' ? 'var(--red)' : 'var(--text-2)',
                    }}>
                      {s.sentiment || 'neutral'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {/* Signal reasons */}
      {signals.length > 0 && signals[0].reasons && (
        <Panel title="Signal Reasoning" className="full-width" style={{ marginTop: 16 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {signals.slice(0, 3).map((s, i) => (
              <div key={i} style={{ padding: '10px 0', borderBottom: '1px solid var(--border)' }}>
                <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, marginBottom: 6 }}>
                  {s.ticker} — {s.action}
                </div>
                <ul style={{ paddingLeft: 16, color: 'var(--text-1)', fontSize: 12 }}>
                  {(s.reasons || []).map((r, j) => <li key={j}>{r}</li>)}
                </ul>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}
