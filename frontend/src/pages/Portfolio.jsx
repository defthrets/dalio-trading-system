import { useState } from 'react';
import { useApi } from '../hooks/useApi';
import { getPaperPortfolio, getPaperHistory, getPaperAnalytics, placePaperOrder } from '../lib/api';
import Panel from '../components/common/Panel';
import Loading from '../components/common/Loading';

const fmt = (n, d = 2) => n != null ? Number(n).toFixed(d) : '--';
const fmtCur = (n) => n != null ? `$${Number(n).toLocaleString('en-AU', { minimumFractionDigits: 2 })}` : '--';

export default function Portfolio() {
  const { data: portfolio, loading, refresh } = useApi(getPaperPortfolio, 15000);
  const { data: history } = useApi(getPaperHistory, 30000);
  const { data: analytics } = useApi(getPaperAnalytics, 60000);

  const [order, setOrder] = useState({ ticker: '', side: 'BUY', qty: 1 });
  const [orderMsg, setOrderMsg] = useState('');

  if (loading) return <Loading />;

  const positions = portfolio?.positions || [];
  const cash = portfolio?.cash ?? 0;
  const trades = (history?.trades || history || []).slice(0, 20);

  const handleOrder = async (e) => {
    e.preventDefault();
    try {
      const result = await placePaperOrder(order);
      setOrderMsg(result.message || 'Order placed');
      refresh();
    } catch (err) {
      setOrderMsg(err.message);
    }
  };

  return (
    <div>
      {/* Stats */}
      <div className="stat-grid" style={{ marginBottom: 16 }}>
        <div className="stat-card">
          <div className="label">Cash</div>
          <div className="value">{fmtCur(cash)}</div>
        </div>
        <div className="stat-card">
          <div className="label">Positions</div>
          <div className="value">{positions.length}</div>
        </div>
        <div className="stat-card">
          <div className="label">Win Rate</div>
          <div className="value">{fmt(analytics?.win_rate ?? 0, 1)}%</div>
        </div>
        <div className="stat-card">
          <div className="label">Profit Factor</div>
          <div className="value">{fmt(analytics?.profit_factor ?? 0, 2)}</div>
        </div>
      </div>

      <div className="dashboard-grid">
        {/* Open Positions */}
        <Panel title="Open Positions" className="full-width">
          {positions.length === 0 ? (
            <div className="empty-state">No open positions</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Side</th>
                  <th>Qty</th>
                  <th>Entry</th>
                  <th>Current</th>
                  <th>P&L</th>
                  <th>P&L %</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => {
                  const pnlPct = p.entry_price ? ((p.current_price - p.entry_price) / p.entry_price * 100) : 0;
                  const isPos = (p.pnl ?? pnlPct) >= 0;
                  return (
                    <tr key={i}>
                      <td className="mono">{p.ticker}</td>
                      <td><span className={`tag tag-${p.side?.toLowerCase()}`}>{p.side}</span></td>
                      <td className="mono">{p.qty}</td>
                      <td className="mono">{fmtCur(p.entry_price)}</td>
                      <td className="mono">{fmtCur(p.current_price)}</td>
                      <td className="mono" style={{ color: isPos ? 'var(--green)' : 'var(--red)' }}>
                        {fmtCur(p.pnl ?? (p.current_price - p.entry_price) * p.qty)}
                      </td>
                      <td className="mono" style={{ color: isPos ? 'var(--green)' : 'var(--red)' }}>
                        {isPos ? '+' : ''}{fmt(pnlPct)}%
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Panel>

        {/* Quick Order */}
        <Panel title="Quick Order">
          <form onSubmit={handleOrder} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <input
              type="text"
              placeholder="Ticker (e.g. BHP.AX)"
              value={order.ticker}
              onChange={(e) => setOrder({ ...order, ticker: e.target.value.toUpperCase() })}
              style={inputStyle}
            />
            <select
              value={order.side}
              onChange={(e) => setOrder({ ...order, side: e.target.value })}
              style={inputStyle}
            >
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
              <option value="SHORT">SHORT</option>
              <option value="COVER">COVER</option>
            </select>
            <input
              type="number"
              placeholder="Quantity"
              value={order.qty}
              onChange={(e) => setOrder({ ...order, qty: Number(e.target.value) })}
              min={1}
              style={inputStyle}
            />
            <button type="submit" className="btn btn-primary">Place Order</button>
            {orderMsg && <div style={{ fontSize: 12, color: 'var(--text-1)' }}>{orderMsg}</div>}
          </form>
        </Panel>

        {/* Trade History */}
        <Panel title="Recent Trades">
          {trades.length === 0 ? (
            <div className="empty-state">No trades yet</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr><th>Ticker</th><th>Side</th><th>Price</th><th>P&L</th></tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr key={i}>
                    <td className="mono">{t.ticker}</td>
                    <td><span className={`tag tag-${t.side?.toLowerCase()}`}>{t.side}</span></td>
                    <td className="mono">{fmtCur(t.price)}</td>
                    <td className="mono" style={{ color: (t.pnl ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>
                      {fmtCur(t.pnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>
    </div>
  );
}

const inputStyle = {
  background: 'var(--bg-2)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-sm)',
  padding: '8px 12px',
  color: 'var(--text-0)',
  fontSize: 13,
  fontFamily: 'var(--font-mono)',
};
