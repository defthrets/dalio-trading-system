import { useState, useEffect } from 'react';
import { useApi } from '../../hooks/useApi';
import { getStatus, setMode } from '../../lib/api';

export default function Header() {
  const { data: status } = useApi(getStatus, 15000);
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const mode = status?.mode || 'PAPER';
  const isLive = mode === 'LIVE';

  const toggleMode = async () => {
    try {
      await setMode(isLive ? 'paper' : 'live');
    } catch { /* handled by useApi */ }
  };

  return (
    <header className="header">
      <div className="header-left">
        <div className={`status-dot ${status ? '' : 'offline'}`} />
        <span style={{ color: 'var(--text-2)', fontSize: 12 }}>
          {status?.status || 'CONNECTING...'}
        </span>
      </div>
      <div className="header-right">
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-2)' }}>
          {time.toUTCString().slice(17, 25)} UTC
        </span>
        <button
          className={`mode-badge ${isLive ? 'live' : 'paper'}`}
          onClick={toggleMode}
          title="Toggle trading mode"
        >
          {mode}
        </button>
      </div>
    </header>
  );
}
