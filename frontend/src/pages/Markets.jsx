import { useState } from 'react';
import { useApi } from '../hooks/useApi';
import { getMarkets } from '../lib/api';
import Panel from '../components/common/Panel';
import Loading from '../components/common/Loading';

const fmt = (n, d = 2) => n != null ? Number(n).toFixed(d) : '--';
const fmtCur = (n) => n != null ? `$${Number(n).toLocaleString('en-AU', { minimumFractionDigits: 2 })}` : '--';

const TABS = [
  { id: 'asx', label: 'ASX' },
  { id: 'commodities', label: 'Commodities' },
];

export default function Markets() {
  const [tab, setTab] = useState('asx');
  const { data, loading, refresh } = useApi(() => getMarkets(tab), 30000, [tab]);

  const rows = data?.rows || data || [];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 4 }}>
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`btn btn-sm ${tab === t.id ? 'btn-primary' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <button className="btn btn-sm" onClick={refresh}>Refresh</button>
      </div>

      <Panel title={`${tab.toUpperCase()} Markets`}>
        {loading ? (
          <Loading />
        ) : rows.length === 0 ? (
          <div className="empty-state">No market data available</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Name</th>
                <th>Price</th>
                <th>Change %</th>
                <th>Volume</th>
                <th>RSI</th>
                <th>Signal</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const chg = r.change_pct ?? r.pct ?? 0;
                const isPos = chg >= 0;
                return (
                  <tr key={i}>
                    <td className="mono" style={{ fontWeight: 600, color: 'var(--text-0)' }}>
                      {r.ticker || r.symbol}
                    </td>
                    <td>{r.name || '--'}</td>
                    <td className="mono">{fmtCur(r.price)}</td>
                    <td className="mono" style={{ color: isPos ? 'var(--green)' : 'var(--red)' }}>
                      {isPos ? '+' : ''}{fmt(chg)}%
                    </td>
                    <td className="mono">{r.volume ? (r.volume / 1e6).toFixed(1) + 'M' : '--'}</td>
                    <td className="mono" style={{
                      color: r.rsi > 70 ? 'var(--red)' : r.rsi < 30 ? 'var(--green)' : 'var(--text-1)',
                    }}>
                      {fmt(r.rsi, 1)}
                    </td>
                    <td>
                      {r.signal && (
                        <span className={`tag tag-${r.signal?.toLowerCase()}`}>{r.signal}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
