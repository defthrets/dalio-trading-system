import { useApi } from '../hooks/useApi';
import { getAgentStatus, toggleAgent, runAgentCycle } from '../lib/api';
import Panel from '../components/common/Panel';
import StatCard from '../components/common/StatCard';
import Loading from '../components/common/Loading';

export default function Agent() {
  const { data, loading, refresh } = useApi(getAgentStatus, 10000);

  if (loading) return <Loading />;

  const enabled = data?.enabled ?? false;
  const interval = data?.interval_seconds ?? 300;
  const lastCycle = data?.last_cycle_time;
  const nextCycle = data?.next_cycle_time;

  const handleToggle = async () => {
    await toggleAgent();
    refresh();
  };

  const handleCycle = async () => {
    await runAgentCycle();
    refresh();
  };

  return (
    <div>
      <div className="stat-grid" style={{ marginBottom: 16 }}>
        <StatCard
          label="Agent Status"
          value={enabled ? 'ACTIVE' : 'PAUSED'}
          positive={enabled}
        />
        <StatCard label="Interval" value={`${interval}s`} />
        <StatCard
          label="Last Cycle"
          value={lastCycle ? new Date(lastCycle).toLocaleTimeString() : 'Never'}
        />
        <StatCard
          label="Next Cycle"
          value={nextCycle ? new Date(nextCycle).toLocaleTimeString() : '--'}
        />
      </div>

      <Panel title="Agent Controls">
        <div style={{ display: 'flex', gap: 12 }}>
          <button
            className={`btn ${enabled ? 'btn-danger' : 'btn-primary'}`}
            onClick={handleToggle}
          >
            {enabled ? 'Pause Agent' : 'Start Agent'}
          </button>
          <button className="btn" onClick={handleCycle}>
            Run Single Cycle
          </button>
        </div>
        <p style={{ marginTop: 16, fontSize: 12, color: 'var(--text-2)', lineHeight: 1.8 }}>
          The autonomous agent scans the market, generates signals, and executes trades
          on the configured interval. In paper mode, trades are simulated. In live mode,
          orders are routed to your connected broker.
        </p>
      </Panel>
    </div>
  );
}
