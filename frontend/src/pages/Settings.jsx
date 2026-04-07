import { useState } from 'react';
import { useApi } from '../hooks/useApi';
import { getStatus } from '../lib/api';
import Panel from '../components/common/Panel';

export default function Settings() {
  const { data: status } = useApi(getStatus, 30000);
  const [saved, setSaved] = useState(false);

  return (
    <div>
      <div className="dashboard-grid">
        <Panel title="System Info">
          <table className="data-table">
            <tbody>
              <tr><td>Version</td><td className="mono">2.0.0</td></tr>
              <tr><td>Mode</td><td className="mono">{status?.mode || 'PAPER'}</td></tr>
              <tr><td>Status</td><td className="mono">{status?.status || '--'}</td></tr>
              <tr><td>Agent Booted</td><td className="mono">{status?.agent_booted ? 'Yes' : 'No'}</td></tr>
              <tr><td>Uptime</td><td className="mono">{formatUptime(status?.uptime_seconds)}</td></tr>
              <tr><td>Cycles</td><td className="mono">{status?.cycle_count ?? 0}</td></tr>
            </tbody>
          </table>
        </Panel>

        <Panel title="Deployment">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, fontSize: 13, color: 'var(--text-1)' }}>
            <div>
              <strong>Docker:</strong>{' '}
              <code style={{ color: 'var(--primary)' }}>docker compose up</code>
            </div>
            <div>
              <strong>PostgreSQL:</strong>{' '}
              <code style={{ color: 'var(--primary)' }}>docker compose --profile db up</code>
            </div>
            <div>
              <strong>Desktop (Tauri):</strong>{' '}
              <code style={{ color: 'var(--primary)' }}>cd frontend && npm run tauri build</code>
            </div>
          </div>
        </Panel>

        <Panel title="API Keys" className="full-width">
          <p style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 12 }}>
            Configure API keys in your <code>.env</code> file. All keys are optional — the system
            degrades gracefully without them. See <code>.env.example</code> for all options.
          </p>
          <table className="data-table">
            <thead>
              <tr><th>Service</th><th>Purpose</th><th>Required</th></tr>
            </thead>
            <tbody>
              <tr><td>yfinance</td><td>Market data (always available)</td><td style={{ color: 'var(--green)' }}>Built-in</td></tr>
              <tr><td>FINNHUB_API_KEY</td><td>News articles for sentiment</td><td>Optional</td></tr>
              <tr><td>EODHD_API_KEY</td><td>Macro data (GDP, CPI)</td><td>Optional</td></tr>
              <tr><td>ALPHA_VANTAGE_API_KEY</td><td>Fallback price data</td><td>Optional</td></tr>
              <tr><td>NEWSAPI_API_KEY</td><td>Additional news sources</td><td>Optional</td></tr>
              <tr><td>DISCORD_WEBHOOK_URL</td><td>Trade notifications</td><td>Optional</td></tr>
            </tbody>
          </table>
        </Panel>
      </div>
    </div>
  );
}

function formatUptime(seconds) {
  if (!seconds) return '--';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}
