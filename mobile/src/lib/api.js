/**
 * API client for Dalios mobile app.
 * Shares the same endpoint interface as the web frontend.
 * Set DALIOS_API_URL in your environment or app config.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

const BASE = __DEV__ ? 'http://localhost:8000' : 'https://your-dalios-server.com';

async function getToken() {
  return AsyncStorage.getItem('dalios_token');
}

async function request(path, options = {}) {
  const token = await getToken();
  const headers = { 'Content-Type': 'application/json', ...options.headers };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(`${BASE}${path}`, { ...options, headers });

  if (res.status === 401) {
    await AsyncStorage.removeItem('dalios_token');
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `API error ${res.status}`);
  }

  return res.json();
}

// Same API surface as web — shared business logic
export const getStatus = () => request('/api/status');
export const getHealth = () => request('/api/health');
export const getPortfolioHealth = () => request('/api/portfolio/health');
export const getSignals = () => request('/api/signals');
export const getMarkets = (type) => request(`/api/markets/${type}`);
export const getPaperPortfolio = () => request('/api/paper/portfolio');
export const getPaperAnalytics = () => request('/api/paper/analytics');
export const getQuadrant = () => request('/api/quadrant');
export const getSentiment = () => request('/api/sentiment');
export const getAgentStatus = () => request('/api/agent/status');
export const toggleAgent = () => request('/api/agent/toggle', { method: 'POST' });
export const login = (username, password) =>
  request('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) });
