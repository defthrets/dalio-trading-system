/**
 * API client for Dalios backend.
 * All endpoints return JSON. Auth token auto-attached if present.
 */

const BASE = import.meta.env.VITE_API_URL || '';

function getToken() {
  return localStorage.getItem('dalios_token');
}

async function request(path, options = {}) {
  const token = getToken();
  const headers = { 'Content-Type': 'application/json', ...options.headers };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(`${BASE}${path}`, { ...options, headers });

  if (res.status === 401) {
    localStorage.removeItem('dalios_token');
    window.dispatchEvent(new Event('dalios:logout'));
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `API error ${res.status}`);
  }

  return res.json();
}

// ── System ──────────────────────────────────────────────────
export const getStatus = () => request('/api/status');
export const getHealth = () => request('/api/health');
export const togglePause = () => request('/api/system/pause', { method: 'POST' });

// ── Auth ────────────────────────────────────────────────────
export const login = (username, password) =>
  request('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) });
export const register = (username, password) =>
  request('/api/auth/register', { method: 'POST', body: JSON.stringify({ username, password }) });
export const getMe = () => request('/api/auth/me');

// ── Portfolio ───────────────────────────────────────────────
export const getPortfolioHealth = () => request('/api/portfolio/health');
export const getEquityHistory = () => request('/api/portfolio/equity_history');

// ── Signals ─────────────────────────────────────────────────
export const getSignals = () => request('/api/signals');

// ── Markets ─────────────────────────────────────────────────
export const getMarketSummary = () => request('/api/market_summary');
export const getMarkets = (type) => request(`/api/markets/${type}`);

// ── Paper Trading ───────────────────────────────────────────
export const getPaperPortfolio = () => request('/api/paper/portfolio');
export const getPaperHistory = () => request('/api/paper/history');
export const getPaperAnalytics = () => request('/api/paper/analytics');
export const getPaperEquity = () => request('/api/paper/equity_curve');
export const placePaperOrder = (order) =>
  request('/api/paper/order', { method: 'POST', body: JSON.stringify(order) });

// ── Quadrant & Sentiment ────────────────────────────────────
export const getQuadrant = () => request('/api/quadrant');
export const getSentiment = () => request('/api/sentiment');
export const getCorrelation = () => request('/api/correlation');

// ── Agent ───────────────────────────────────────────────────
export const getAgentStatus = () => request('/api/agent/status');
export const toggleAgent = () => request('/api/agent/toggle', { method: 'POST' });
export const runAgentCycle = () => request('/api/agent/cycle', { method: 'POST' });

// ── Settings ────────────────────────────────────────────────
export const getSettings = () => request('/api/settings');
export const updateSettings = (settings) =>
  request('/api/settings', { method: 'POST', body: JSON.stringify(settings) });

// ── Mode ────────────────────────────────────────────────────
export const setMode = (mode) =>
  request('/api/mode', { method: 'POST', body: JSON.stringify({ mode }) });

// ── WebSocket ───────────────────────────────────────────────
export function connectWS(onMessage, onClose) {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const host = BASE ? new URL(BASE).host : window.location.host;
  const ws = new WebSocket(`${protocol}://${host}/ws`);

  ws.onmessage = (e) => {
    try { onMessage(JSON.parse(e.data)); } catch { onMessage(e.data); }
  };
  ws.onclose = () => onClose?.();
  ws.onerror = () => ws.close();

  return ws;
}
