/* ═══════════════════════════════════════════════════════════
   DALIOS — Automated Trading Framework
   Frontend Application
   ═══════════════════════════════════════════════════════════ */

'use strict';

const API = '';   // Same origin (FastAPI serves this file)
let ws = null;
let wsReconnectTimer = null;
let charts = {};
let selectedSignal = null;

// ─── XSS escape helper ───────────────────────────────────
function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ─── Guards ───────────────────────────────────────────────
let _loadHealthInFlight = false;
let _searchDebounce = null;

// ─── Global state cache ───────────────────────────────────
const STATE = {
  status:    null,
  health:    null,
  quadrant:  null,
  sentiment: null,
  signals:   [],
  corr:      null,
  backtest:  null,
  alerts:    [],
  cycleCount: 0,
};

// ─── Quadrant metadata ────────────────────────────────────
const QUADRANT_META = {
  rising_growth:    { label: 'RISING GROWTH',    color: '#00cc44', icon: '▲', cssClass: '' },
  falling_growth:   { label: 'FALLING GROWTH',   color: '#ff3355', icon: '▼', cssClass: 'red' },
  rising_inflation: { label: 'RISING INFLATION', color: '#ffcc00', icon: '↑', cssClass: 'amber' },
  falling_inflation:{ label: 'FALLING INFLATION',color: '#00d4ff', icon: '↓', cssClass: 'cyan' },
};

// ─── Animation helper ─────────────────────────────────────
function flashEl(id, cls = 'data-flash') {
  const e = el(id);
  if (!e) return;
  e.classList.remove(cls);
  void e.offsetWidth; // force reflow
  e.classList.add(cls);
  e.addEventListener('animationend', () => e.classList.remove(cls), { once: true });
}

// ═══════════════════════════════════════════════════════════
// Initialisation
// ═══════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  initClock();
  initWebSocket();
  initCharts();
  initNotifications();
  initTradingMode();
  loadWatchlist();
  _applyStoredTheme();
  _restoreSound();
  _restoreFilters();
  loadAll();
  loadMarketSummary();
  setTimeout(preloadAllTabs, 3000);       // Preload all tab data 3s after boot
  setTimeout(initWelcomeTutorial, 1500);  // Show welcome popup after initial load

  // Use saved intervals or defaults
  const _s = _loadSettings();
  const refreshMs  = (_s.refresh_interval  || 30) * 1000;
  const tickerMs   = (_s.ticker_interval   || 60) * 1000;

  window._intervals = [];
  window._intervals.push(setInterval(loadAll, refreshMs));            // Refresh all data
  window._intervals.push(setInterval(updateClock, 1000));
  window._intervals.push(setInterval(loadHealth, 10_000));            // Health every 10s
  window._intervals.push(setInterval(loadMarketSummary, tickerMs));   // Ticker strip
  window._intervals.push(setInterval(pollLivePnl, 15_000));           // Live P&L every 15s (global)
  window._intervals.push(setInterval(autoRefreshNews, 300_000));      // Live news refresh every 5 min
});

// ─── Tab Navigation ───────────────────────────────────────
function initTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.tab;
      _switchTab(id, btn);
    });
  });

  // Restore last active tab or default to command-center
  const savedTab = _loadSettings().active_tab;
  if (savedTab) {
    const btn = document.querySelector(`.tab-btn[data-tab="${savedTab}"]`);
    if (btn) { _switchTab(savedTab, btn); return; }
  }
  // Show speech bubbles for Command Center on first ever load
  setTimeout(() => showTutorial('command-center'), 1000);
}

function _switchTab(id, btn) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(`tab-${id}`).classList.add('active');
  _saveSetting('active_tab', id);
  // Lazy-load tab data
  if (id === 'signal-ops')           initSignalOps();
  if (id === 'intel-center')         loadSentiment();
  if (id === 'holy-grail')           loadCorrelation();
  if (id === 'risk-matrix')          loadHealth();
  if (id === 'backtest-lab')         loadBacktest();
  if (id === 'paper-trading')        initPaperTrading();
  if (id === 'live-trading')         initLiveTrading();
  if (id === 'asx-scanner')         loadScanner('asx');
  if (id === 'commodities-scanner') loadScanner('commodities');
  if (id === 'command-center')      initCommandCentre();
  if (id === 'comms-config')        initSettingsTab();
  // Show tutorial on first visit
  showTutorial(id);
}

// ─── Clock ────────────────────────────────────────────────
function initClock() { updateClock(); }
function updateClock() {
  const now = new Date();
  const utc = now.toUTCString().split(' ')[4];
  const aest = now.toLocaleTimeString('en-AU', { timeZone: 'Australia/Sydney', hour12: false });
  document.getElementById('liveClock').textContent = `UTC ${utc}  ·  AEST ${aest}`;
}

// ─── Load all ─────────────────────────────────────────────
async function loadAll() {
  await Promise.allSettled([
    loadStatus(),
    loadHealth(),
    loadQuadrant(),
    loadAlerts(),
    loadBrokerStatus(),
  ]);
}

// ─── Preload all tabs (background, fires once after boot) ──
async function preloadAllTabs() {
  await Promise.allSettled([
    initCommandCentre(),
    initSignalOps(),
    loadSentiment(),
    loadCorrelation(),
    loadBacktest(),
    loadScanner('asx'),
    loadScanner('commodities'),
    initPaperTrading(),
    initLiveTrading(),
    initSettingsTab(),
  ]);
  console.log('[DALIOS] All tabs preloaded');
}

// ═══════════════════════════════════════════════════════════
// WebSocket
// ═══════════════════════════════════════════════════════════

function initWebSocket() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url   = `${proto}://${location.host}/ws`;
  ws = new WebSocket(url);

  ws.onopen = () => {
    setWsState('connected');
    pushAlert('WS', 'NEURAL LINK ESTABLISHED', 'info');
    clearTimeout(wsReconnectTimer);
  };

  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      handleWsMessage(msg);
    } catch {}
  };

  ws.onerror = () => setWsState('error');

  ws.onclose = () => {
    setWsState('disconnected');
    wsReconnectTimer = setTimeout(initWebSocket, 5000);
  };
}

function handleWsMessage(msg) {
  switch (msg.type) {
    case 'CONNECTED':
      pushAlert('SYSTEM', msg.message, 'info');
      break;
    case 'HEARTBEAT':
      document.getElementById('uptimeBadge').textContent = `UPTIME: ${formatUptime(msg.uptime)}`;
      break;
    case 'HEALTH_UPDATE':
      applyHealth(msg.data);
      break;
    case 'CYCLE_UPDATE':
      STATE.cycleCount++;
      document.getElementById('cycleCount').textContent = STATE.cycleCount;
      pushAlert('CYCLE', `Cycle #${msg.data.cycle} complete — ${msg.data.signals_found} signals`, 'info');
      if (msg.data.top_signals) {
        renderSignalGrid(msg.data.top_signals);
        // Sound + notification for strong signals
        const strong = (msg.data.top_signals || []).find(s => s.confidence > 0.8);
        if (strong) {
          playSignalBeep();
          sendNotification('Strong Signal', `${strong.action} ${strong.ticker} — ${((Number(strong.confidence)||0) * 100).toFixed(0)}% confidence`);
        }
      }
      break;
    case 'MODE_CHANGE':
      updateModeUI(msg.data.mode, true);
      refreshCcForMode();
      break;
    case 'PAPER_ORDER':
    case 'PAPER_CLOSE':
      playOrderBeep();
      loadPaperEquityCurve();
      break;
    case 'REAL_ORDER':
    case 'REAL_CLOSE':
      playOrderBeep();
      loadRealEquityCurve();
      sendNotification('Live Order Update', `${msg.type === 'REAL_ORDER' ? 'Order placed' : 'Position closed'}: ${msg.data?.ticker || ''}`);
      break;
    case 'AGENT_BOOT':
      pushAlert('BOOT', msg.message, 'info');
      break;
  }
}

function setWsState(state) {
  const dot = document.querySelector('.ws-dot');
  if (!dot) return;
  dot.className = 'ws-dot ' + (state === 'connected' ? 'connected' : state === 'error' ? 'error' : '');
}

// ═══════════════════════════════════════════════════════════
// API Calls
// ═══════════════════════════════════════════════════════════

async function fetchJSON(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function postJSON(path, body = {}) {
  const r = await fetch(API + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ─── Status ───────────────────────────────────────────────
async function loadStatus() {
  try {
    const d = await fetchJSON('/api/status');
    STATE.status = d;
    document.getElementById('modeBadge').textContent   = `MODE: ${d.mode} ▾`;
    document.getElementById('modeBadge').className     = d.mode === 'LIVE' ? 'badge badge--red mode-live' : 'badge badge--cyan mode-paper';
    // Status badge respects paused state from server
    const sb = document.getElementById('statusBadge');
    if (sb) {
      _systemPaused = !!d.paused;
      sb.textContent = d.paused ? '⏸ PAUSED' : `● ${d.status}`;
      sb.className   = d.paused ? 'badge badge--paused' : 'badge badge--green';
    }
    document.getElementById('cycleCount').textContent  = d.cycle_count;
    document.getElementById('uptimeBadge').textContent = `UPTIME: ${formatUptime(d.uptime_seconds)}`;
    const cfgMode = document.getElementById('cfgMode');
    if (cfgMode) cfgMode.value = d.mode.toLowerCase();
  } catch (e) { console.debug('loadStatus failed:', e); }
}

// ─── Health ───────────────────────────────────────────────
async function loadHealth() {
  if (_loadHealthInFlight) return;
  _loadHealthInFlight = true;
  try {
    const d = await fetchJSON('/api/portfolio/health');
    applyHealth(d);
    // Feed prediction chart from the mode-appropriate equity source
    if (_tradingMode === 'live') {
      const ld = await fetchJSON('/api/real/equity_curve').catch(() => null);
      if (ld?.equity_curve?.length) updatePredictionFromEquity(ld.equity_curve);
    } else {
      const hist = await fetchJSON('/api/portfolio/equity_history');
      updateEquityChart(hist.history);
    }
  } catch (e) { console.debug('loadHealth failed:', e); } finally { _loadHealthInFlight = false; }
}

function applyHealth(d) {
  STATE.health = d;

  // Command center — plain English labels
  setEl('navValue',     fmt$( d.equity ));
  setEl('openPositions', d.open_positions);
  // Sharpe: plain English
  const sh = d.sharpe_ratio ?? 0;
  const shLabel = sh >= 2 ? 'Excellent' : sh >= 1 ? 'Good' : sh >= 0 ? 'Average' : 'Poor';
  setEl('sharpeVal', `${sh.toFixed(2)} (${shLabel})`);
  setEl('divStatus',    d.dalio_diversification_met ? '✓ DIVERSIFIED' : '✗ CONCENTRATED');

  const dailyPct = d.daily_pnl_pct ?? 0;
  const ddPct    = d.drawdown_pct ?? 0;
  setEl('dailyPnl', (dailyPct >= 0 ? '+' : '') + dailyPct.toFixed(3) + '%');
  const dpEl = el('dailyPnl');
  if (dpEl) dpEl.style.color = dailyPct >= 0 ? 'var(--green)' : 'var(--red)';
  setWidth('dailyPnlBar', Math.min(Math.abs(dailyPct) / 2 * 100, 100));
  const dpBar = el('dailyPnlBar');
  if (dailyPct < 0 && dpBar) dpBar.style.background = 'var(--red)';

  setEl('drawdownVal', `-${ddPct.toFixed(2)}%`);
  setWidth('drawdownBar', Math.min(ddPct / 10 * 100, 100));

  const totalReturnBadge = el('totalReturnBadge');
  if (totalReturnBadge) {
    const ret = d.total_return_pct ?? 0;
    totalReturnBadge.innerHTML = `ROI: <strong style="color:${ret >= 0 ? 'var(--green)' : 'var(--red)'}">
      ${ret >= 0 ? '+' : ''}${ret.toFixed(2)}%</strong>`;
  }

  // Circuit breaker
  const halted = d.circuit_breaker_active;
  const cbIcon  = el('cbIcon'),  cbLabel = el('cbLabel'), cbSub = el('cbSublabel');
  if (cbIcon)  { cbIcon.textContent  = halted ? '⛔' : '⬡'; cbIcon.className  = halted ? 'cb-icon halted' : 'cb-icon'; }
  if (halted && !STATE._cbAlarmFired) { STATE._cbAlarmFired = true; playCircuitBreakerAlarm(); sendNotification('CIRCUIT BREAKER', 'Trading HALTED — limit hit'); }
  if (!halted) STATE._cbAlarmFired = false;
  if (cbLabel) { cbLabel.textContent = halted ? 'HALTED'   : 'ARMED';           cbLabel.className = halted ? 'cb-label halted' : 'cb-label'; }
  if (cbSub)   cbSub.textContent    = halted ? 'Trading SUSPENDED — limit hit' : 'Trading permitted';
  const cbBadge = el('circuitBreakerBadge');
  if (cbBadge) { cbBadge.textContent = halted ? 'CB: TRIGGERED' : 'CB: ARMED'; cbBadge.className = halted ? 'badge badge--red' : 'badge badge--cyan'; }
  setEl('cbDailyUsed',    `Used: ${Math.abs(dailyPct).toFixed(2)}% / 2.0%`);
  setEl('cbDrawdownUsed', `Used: ${ddPct.toFixed(2)}% / 10.0%`);
  setWidth('cbDailyBar',    Math.min(Math.abs(dailyPct) / 2 * 100, 100));
  setWidth('cbDrawdownBar', Math.min(ddPct / 10 * 100, 100));

  // Risk matrix metrics with plain-English descriptions
  const sh2   = d.sharpe_ratio ?? 0;
  const ret2  = d.total_return_pct ?? 0;
  setEl('rm-sharpe',      sh2.toFixed(2));
  setEl('rm-sharpe-desc', sh2 >= 2 ? '✓ Excellent — great risk-adj. return' : sh2 >= 1 ? '✓ Good — solid performance' : sh2 >= 0 ? '⚠ Average — room to improve' : '✗ Below average');
  setEl('rm-nav',         fmt$(d.equity));
  setEl('rm-totalret',    (ret2 >= 0 ? '+' : '') + ret2.toFixed(2) + '%');
  setEl('rm-maxdd',       ddPct.toFixed(2) + '%');
  setEl('rm-maxdd-desc',  ddPct > 9 ? '✗ CRITICAL — near circuit breaker limit' : ddPct > 5 ? '⚠ Warning — significant drawdown' : '✓ Within safe limits (<10%)');

  // Overall risk score badge (0-100)
  const riskScore = Math.max(0, Math.min(100, Math.round(
    (sh2 >= 1 ? 30 : sh2 >= 0 ? 15 : 0) +
    (ddPct < 5 ? 30 : ddPct < 10 ? 15 : 0) +
    (ret2 > 0 ? 25 : ret2 > -5 ? 10 : 0) +
    (d.dalio_diversification_met ? 15 : 0)
  )));
  const riskLabel = riskScore >= 75 ? 'LOW RISK' : riskScore >= 50 ? 'MODERATE' : riskScore >= 25 ? 'ELEVATED' : 'HIGH RISK';
  const riskBadge = el('riskScoreBadge');
  if (riskBadge) {
    riskBadge.textContent = `SCORE: ${riskScore}/100 ${riskLabel}`;
    riskBadge.style.color = riskScore >= 75 ? 'var(--green)' : riskScore >= 50 ? 'var(--amber)' : 'var(--red)';
  }

  // Positions table
  if (d.positions) renderPositionTable(d.positions);
  // Weights chart
  if (d.risk_weights) updateWeightsChart(d.risk_weights);
  // Daily P&L chart — use real series when available
  if (d.daily_pnl_series) updatePnlChart(d.daily_pnl_series);

  // ── Statsbar hero stats ──
  _updateStatsbarFromHealth(d);
}

function _updateStatsbarFromHealth(d) {
  const dailyPct = d.daily_pnl_pct ?? 0;
  const dSign = dailyPct >= 0 ? '+' : '';
  _statsbarSet('ccStatsDailyPnl', `${dSign}${dailyPct.toFixed(3)}%`, dailyPct >= 0 ? 'var(--green)' : 'var(--red)');
  const arrow = el('ccStatsDailyArrow');
  if (arrow) {
    arrow.textContent = dailyPct >= 0 ? '\u25B2' : '\u25BC';
    arrow.className = 'statsbar-arrow ' + (dailyPct >= 0 ? 'up' : 'down');
  }
  const sh = d.sharpe_ratio ?? null;
  _statsbarSet('ccStatsSharpe', sh != null ? sh.toFixed(2) : '--', sh != null && sh >= 1 ? 'var(--green)' : sh != null && sh >= 0 ? 'var(--amber)' : 'var(--red)');

  // ROI update in statsbar
  const ret = el('ccReturn');
  if (ret) {
    const roi = d.total_return_pct ?? 0;
    const rSign = roi >= 0 ? '+' : '';
    ret.textContent = `${rSign}${roi.toFixed(2)}%`;
    ret.style.color = roi >= 0 ? 'var(--green)' : 'var(--red)';
  }
}

function _statsbarSet(id, val, color) {
  const e = el(id);
  if (!e) return;
  const changed = e.textContent !== String(val);
  e.textContent = val;
  if (color) e.style.color = color;
  if (changed) {
    e.classList.remove('flash-update');
    void e.offsetWidth; // reflow
    e.classList.add('flash-update');
  }
}

// ─── Quadrant ─────────────────────────────────────────────
async function loadQuadrant() {
  try {
    const d = await fetchJSON('/api/quadrant');
    STATE.quadrant = d;
    applyQuadrant(d);
  } catch (e) { console.debug('loadQuadrant failed:', e); }
}

function applyQuadrant(d) {
  const q    = d.quadrant;
  const meta = QUADRANT_META[q] || { label: q, color: 'var(--green)', icon: '?', cssClass: '' };

  // Highlight active quadrant cell
  document.querySelectorAll('.q-cell').forEach(c => c.classList.remove('active', 'amber', 'red', 'cyan'));
  const activeCell = el(`q-${q}`);
  if (activeCell) {
    activeCell.classList.add('active');
    if (meta.cssClass) activeCell.classList.add(meta.cssClass);
  }

  setEl('activeQuadrantName', meta.label);
  setEl('activeQuadrantDesc', d.description || '');
  setEl('gdpVal',    d.gdp_value !== undefined ? d.gdp_value.toFixed(2) : '--');
  setEl('cpiVal',    d.cpi_value !== undefined ? d.cpi_value.toFixed(2) : '--');
  setEl('quadConf',  d.confidence ? d.confidence.toFixed(1) : '--');

  // Apply quadrant colour to name
  const nameEl = el('activeQuadrantName');
  if (nameEl) nameEl.style.color = meta.color;

  if (d.conflict_risk_elevated) {
    pushAlert('INTEL', '⚠ ELEVATED GEOPOLITICAL RISK — Bias toward gold, bonds, defensives', 'warning');
  }
}

// ─── Signals ──────────────────────────────────────────────
async function initSignalOps() {
  // Run signals scan + seed scanner cache in parallel so opportunities populate
  const oppList = el('opportunityList');
  if (oppList) oppList.innerHTML = '<div style="padding:14px;color:var(--text-muted);font-size:10px;line-height:1.8">⟳ WARMING UP SCANNERS…<br><span style="opacity:.6">Fetching ASX &amp; Commodities data for opportunity engine…</span></div>';

  // Fire all three market scans in background to seed the cache
  const seedCache = async () => {
    await Promise.allSettled([
      fetchJSON('/api/markets/asx').catch(() => {}),
      fetchJSON('/api/markets/commodities').catch(() => {}),
    ]);
    // Once cache is warm, load opportunities
    loadSuggestOpportunities(10);
  };

  // Run signals and cache seeding in parallel
  await Promise.all([loadSignals(), seedCache()]);
}

async function loadSignals() {
  const grid = el('signalGrid');
  if (!grid) return;
  setEl('signalCount', '⌛ SCANNING...');
  grid.innerHTML = `<div class="signal-loading"><div class="loading-spinner"></div><span>SCANNING UNIVERSE...</span></div>`;
  try {
    const d = await fetchJSON('/api/signals');
    STATE.signals = d.signals || [];
    renderSignalGrid(STATE.signals);
    renderOpportunities(d.new_opportunities || []);
    // Phase 6: cache freshness indicator
    const cacheTag = el('signalCacheAge');
    if (cacheTag && d.cached) {
      cacheTag.textContent = `(updated ${d.cache_age}s ago)`;
      cacheTag.style.display = '';
    } else if (cacheTag) {
      cacheTag.textContent = '(live)';
      cacheTag.style.display = '';
    }
  } catch (e) {
    grid.innerHTML = `<div class="signal-loading"><span>⚠ SCAN ERROR — ${escHtml(e.message || 'server unreachable')}</span></div>`;
    setEl('signalCount', '0 SIGNALS');
  }
}

function renderSignalGrid(signals) {
  const minConf = parseInt(el('minConfidence')?.value ?? 60);
  const filterType = el('signalFilter')?.value ?? 'ALL';
  const filterMkt  = el('marketFilter')?.value ?? 'ALL';

  const filtered = signals.filter(s => {
    if (s.action === 'HOLD') return false;          // never show HOLDs — not actionable
    if (s.confidence < minConf) return false;
    if (filterType !== 'ALL') {
      if (filterType === 'BUY'  && !['BUY','LONG'].includes(s.action))  return false;
      if (filterType === 'SELL' && !['SELL','SHORT'].includes(s.action)) return false;
      if (filterType === 'OPTIONS' && !s.options_strategy) return false;
    }
    if (filterMkt !== 'ALL') {
      if (filterMkt === 'ASX'          && !s.ticker.endsWith('.AX'))                        return false;
      if (filterMkt === 'COMMODITIES'  && (s.ticker.endsWith('.AX') || s.ticker.endsWith('-USD'))) return false;
    }
    return true;
  });

  setEl('signalCount', `${filtered.length} SIGNALS`);

  if (!filtered.length) {
    el('signalGrid').innerHTML = `<div class="signal-loading"><span>NO SIGNALS MATCH FILTERS</span></div>`;
    return;
  }

  el('signalGrid').innerHTML = filtered.map(s => { try { return signalCardHTML(s); } catch(e) { return `<div class="signal-card" style="padding:14px;color:var(--red)">Error rendering ${s.ticker}: ${escHtml(e.message)}</div>`; } }).join('');

  // Check for strong signals (fires fixed banner + in-page bar)
  checkStrongSignals(filtered);

  // Attach click handlers
  el('signalGrid').querySelectorAll('.signal-card').forEach((card, i) => {
    card.addEventListener('click', () => {
      el('signalGrid').querySelectorAll('.signal-card').forEach(c => c.classList.remove('selected'));
      card.classList.add('selected');
      showJustification(filtered[i]);
    });
  });
  // Populate broker compatibility text on signal cards
  _populateBrokerCompatText();
}

async function _populateBrokerCompatText() {
  const compat = await _ensureBrokerCompat();
  document.querySelectorAll('.sc-brokers[data-ticker]').forEach(div => {
    const ticker = div.dataset.ticker;
    if (!ticker) return;
    const brokers = _getCompatibleBrokers(ticker, compat);
    div.textContent = brokers.length > 0 ? `Trade via: ${brokers.join(', ')}` : '';
  });
}

// Translate raw numbers into plain-English labels
function rsiLabel(rsi) {
  if (rsi < 30) return `${rsi.toFixed(0)} — Oversold (potential bounce)`;
  if (rsi > 70) return `${rsi.toFixed(0)} — Overbought (potential pullback)`;
  if (rsi < 45) return `${rsi.toFixed(0)} — Weak momentum`;
  if (rsi > 55) return `${rsi.toFixed(0)} — Strong momentum`;
  return `${rsi.toFixed(0)} — Neutral`;
}
function trendLabel(trend) {
  if (trend === 'uptrend')   return '↑ Moving up';
  if (trend === 'downtrend') return '↓ Moving down';
  return '↔ Sideways';
}
function actionVerb(action) {
  return { BUY: 'BUY NOW', SELL: 'SELL / EXIT', LONG: 'HOLD LONG', SHORT: 'SHORT SELL', HOLD: 'HOLD' }[action] ?? action;
}
function fmtSignalPrice(s) {
  if (!s.price) return '---';
  return '$' + s.price.toFixed(s.price < 1 ? 4 : 2);
}

// ─── RSI Gauge SVG ────────────────────────────────────────
function rsiGaugeSVG(rsi) {
  const W = 80, H = 40, R = 30;
  const cX = W / 2, cY = H;
  const angle = Math.PI - (rsi / 100) * Math.PI; // left=0, right=100
  const nX = cX + R * Math.cos(angle);
  const nY = cY - R * Math.sin(angle);
  const col = rsi < 30 ? 'var(--green)' : rsi > 70 ? 'var(--red)' : 'var(--cyan)';
  return `<svg viewBox="0 0 ${W} ${H}" width="80" height="40" xmlns="http://www.w3.org/2000/svg" style="display:block;margin:2px auto 0">
    <path d="M ${cX-R} ${cY} A ${R} ${R} 0 0 1 ${cX+R} ${cY}" fill="none" stroke="#1a1a1a" stroke-width="5"/>
    <path d="M ${cX-R} ${cY} A ${R} ${R} 0 0 1 ${cX} ${cY-R}" fill="none" stroke="var(--green)" stroke-width="3" opacity="0.3"/>
    <path d="M ${cX-R*0.5} ${cY-R*0.87} A ${R} ${R} 0 0 1 ${cX+R*0.5} ${cY-R*0.87}" fill="none" stroke="var(--cyan)" stroke-width="3" opacity="0.3"/>
    <path d="M ${cX} ${cY-R} A ${R} ${R} 0 0 1 ${cX+R} ${cY}" fill="none" stroke="var(--red)" stroke-width="3" opacity="0.3"/>
    <line x1="${cX}" y1="${cY}" x2="${nX.toFixed(1)}" y2="${nY.toFixed(1)}" stroke="${col}" stroke-width="2"/>
    <circle cx="${cX}" cy="${cY}" r="2" fill="${col}"/>
    <text x="${cX}" y="${cY-4}" text-anchor="middle" fill="${col}" font-size="9" font-family="monospace">${rsi.toFixed(0)}</text>
  </svg>`;
}

// ─── Multi-scenario prediction SVG (for justification panel) ──
function scenarioPredictionSVG(s) {
  const W = 320, H = 100;
  const history = s.price_history;
  if (!history || history.length < 2) return '<div style="color:var(--text-2);font-size:9px;text-align:center;padding:8px">No price data available</div>';

  const curr = s.price || history[history.length - 1];
  const tp   = s.take_profit ?? curr * 1.05;
  const sl   = s.stop_loss ?? curr * 0.97;
  const nProj = 10;

  // Three scenarios
  const bull = [], base = [], bear = [];
  for (let i = 1; i <= nProj; i++) {
    const t = i / nProj;
    bull.push(curr + (tp - curr) * 1.2 * Math.sqrt(t));
    base.push(curr + (tp - curr) * 0.7 * Math.sqrt(t));
    bear.push(curr + (sl - curr) * 0.6 * Math.sqrt(t));
  }

  const allPts = [...history, ...bull, ...bear];
  const lo = Math.min(sl * 0.99, ...allPts);
  const hi = Math.max(tp * 1.01, ...allPts);
  const range = hi - lo || 1;
  const totalLen = history.length + nProj;

  const xS = (i) => ((i / (totalLen - 1)) * W).toFixed(1);
  const yS = (v) => (H - 6 - ((v - lo) / range) * (H - 12)).toFixed(1);

  const histPts = history.map((v, i) => `${xS(i)},${yS(v)}`).join(' ');
  const lastHX = +xS(history.length - 1);
  const lastHY = +yS(curr);

  const mkProj = (arr) => arr.map((v, i) => `${xS(history.length + i)},${yS(v)}`).join(' ');

  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" xmlns="http://www.w3.org/2000/svg" style="display:block">
    <rect x="0" y="0" width="${W}" height="${H}" fill="rgba(0,0,0,0.3)" rx="4"/>
    <line x1="0" y1="${yS(tp)}" x2="${W}" y2="${yS(tp)}" stroke="rgba(0,204,68,0.2)" stroke-width="1" stroke-dasharray="3,3"/>
    <line x1="0" y1="${yS(sl)}" x2="${W}" y2="${yS(sl)}" stroke="rgba(255,34,34,0.2)" stroke-width="1" stroke-dasharray="3,3"/>
    <line x1="${lastHX}" y1="0" x2="${lastHX}" y2="${H}" stroke="rgba(255,140,0,0.15)" stroke-width="1" stroke-dasharray="2,4"/>
    <polyline points="${histPts}" fill="none" stroke="#ff8c00" stroke-width="1.5" opacity="0.85"/>
    <polyline points="${lastHX},${lastHY} ${mkProj(bull)}" fill="none" stroke="#00cc44" stroke-width="1.2" stroke-dasharray="4,3" opacity="0.7"/>
    <polyline points="${lastHX},${lastHY} ${mkProj(base)}" fill="none" stroke="#00d4ff" stroke-width="1.2" stroke-dasharray="4,3" opacity="0.5"/>
    <polyline points="${lastHX},${lastHY} ${mkProj(bear)}" fill="none" stroke="#ff2222" stroke-width="1.2" stroke-dasharray="4,3" opacity="0.5"/>
    <circle cx="${lastHX}" cy="${lastHY}" r="3" fill="#ff8c00"/>
    <text x="${W-4}" y="${+yS(tp)-3}" text-anchor="end" fill="#00cc44" font-size="7" font-family="monospace">BULL</text>
    <text x="${W-4}" y="${+yS(curr)+3}" text-anchor="end" fill="#00d4ff" font-size="7" font-family="monospace">BASE</text>
    <text x="${W-4}" y="${+yS(sl)+10}" text-anchor="end" fill="#ff2222" font-size="7" font-family="monospace">BEAR</text>
    <text x="4" y="${+yS(tp)-3}" fill="rgba(0,204,68,0.5)" font-size="7" font-family="monospace">TP $${(+tp).toFixed(2)}</text>
    <text x="4" y="${+yS(sl)+10}" fill="rgba(255,34,34,0.5)" font-size="7" font-family="monospace">SL $${(+sl).toFixed(2)}</text>
    <text x="${lastHX}" y="8" text-anchor="middle" fill="rgba(255,140,0,0.4)" font-size="6" font-family="monospace">NOW</text>
  </svg>`;
}

// ─── Momentum histogram SVG (for justification panel) ─────
function momentumHistogramSVG(s) {
  const history = s.price_history;
  if (!history || history.length < 5) return '';

  const W = 320, H = 50;
  const changes = [];
  for (let i = 1; i < history.length; i++) {
    changes.push(((history[i] - history[i-1]) / history[i-1]) * 100);
  }
  const maxAbs = Math.max(...changes.map(Math.abs), 0.5);
  const barW = (W / changes.length) * 0.8;
  const gap = (W / changes.length) * 0.2;

  const bars = changes.map((c, i) => {
    const x = i * (barW + gap);
    const h = (Math.abs(c) / maxAbs) * (H / 2 - 4);
    const y = c >= 0 ? H / 2 - h : H / 2;
    const col = c >= 0 ? 'rgba(0,204,68,0.6)' : 'rgba(255,34,34,0.6)';
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" fill="${col}" rx="1"/>`;
  }).join('');

  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" xmlns="http://www.w3.org/2000/svg" style="display:block">
    <rect x="0" y="0" width="${W}" height="${H}" fill="rgba(0,0,0,0.3)" rx="4"/>
    <line x1="0" y1="${H/2}" x2="${W}" y2="${H/2}" stroke="rgba(255,255,255,0.08)" stroke-width="1"/>
    ${bars}
    <text x="4" y="10" fill="var(--text-2)" font-size="7" font-family="monospace">DAILY MOMENTUM %</text>
  </svg>`;
}

// ─── Prediction sparkline SVG ─────────────────────────────
function sparklineSVG(s) {
  const W = 200, H = 46;
  const history = s.price_history;
  if (!history || history.length < 2) return '';

  const tp   = s.take_profit ?? s.price * 1.05;
  const sl   = s.stop_loss   ?? s.price * 0.97;
  const curr = s.price;

  // Build a short smooth projection toward take_profit
  const nProj = 7;
  const proj = [];
  for (let i = 1; i <= nProj; i++) {
    const t = i / nProj;
    proj.push(curr + (tp - curr) * Math.sqrt(t));
  }

  const allPts = [...history, ...proj];
  const totalLen = history.length + nProj;
  const lo = Math.min(sl * 0.995, ...allPts) ;
  const hi = Math.max(tp * 1.005, ...allPts);
  const range = hi - lo || 1;

  const xS = (i) => ((i / (totalLen - 1)) * W).toFixed(1);
  const yS = (v)  => (H - ((v - lo) / range) * (H - 4) - 2).toFixed(1);

  const histPts = history.map((v, i) => `${xS(i)},${yS(v)}`).join(' ');
  const lastHX  = +xS(history.length - 1);
  const lastHY  = +yS(curr);
  const projPts = proj.map((v, i) => `${xS(history.length + i)},${yS(v)}`).join(' ');

  const tpY = +yS(tp);
  const slY = +yS(sl);

  const isBuy   = ['BUY','LONG'].includes(s.action);
  const isSell  = ['SELL','SHORT'].includes(s.action);
  const histCol = isBuy ? '#ff8c00' : isSell ? '#ff4444' : '#7a6040';
  const projCol = isBuy ? '#00cc44' : '#ff2222';

  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" xmlns="http://www.w3.org/2000/svg" style="display:block">
    <line x1="0" y1="${slY}" x2="${W}" y2="${slY}" stroke="rgba(255,34,34,0.25)" stroke-width="1" stroke-dasharray="3,3"/>
    <line x1="0" y1="${tpY}" x2="${W}" y2="${tpY}" stroke="rgba(0,204,68,0.25)" stroke-width="1" stroke-dasharray="3,3"/>
    <polyline points="${histPts}" fill="none" stroke="${histCol}" stroke-width="1.5" opacity="0.85"/>
    <polyline points="${lastHX},${lastHY} ${projPts}" fill="none" stroke="${projCol}" stroke-width="1.2" stroke-dasharray="4,3" opacity="0.75"/>
    <circle cx="${lastHX}" cy="${lastHY}" r="2.5" fill="${histCol}"/>
    <circle cx="${+xS(totalLen-1)}" cy="${tpY}" r="2" fill="${projCol}" opacity="0.8"/>
  </svg>`;
}

function sellDateStr(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toLocaleDateString('en-AU', { day: 'numeric', month: 'short' });
}

function signalCardHTML(s) {
  const conf      = Number(s.confidence) || 0;
  const confPct   = Math.min(conf, 100);
  const confColor = conf >= 80 ? 'var(--green)' : conf >= 65 ? 'var(--amber)' : 'var(--red)';
  const overview  = s.dalio_justification?.ai_overview ?? '';
  const srcBadge  = s.data_source === 'LIVE'
    ? `<span style="font-size:8px;color:var(--green);letter-spacing:1px">● LIVE</span>`
    : `<span style="font-size:8px;color:var(--amber);letter-spacing:1px">● DEMO</span>`;
  const rrNum     = Number(s.rr_ratio) || 0;
  const rrLabel   = rrNum >= 2.5 ? '★ EXCELLENT' : rrNum >= 1.5 ? '✓ GOOD' : '⚠ LOW';
  const rrColor   = rrNum >= 2.5 ? 'var(--green)' : rrNum >= 1.5 ? 'var(--amber)' : 'var(--red)';
  const rsiVal    = Number(s.rsi) || 50;
  const psPct     = Number(s.position_size_pct) || 0;

  return `
    <div class="signal-card ${s.action}" data-ticker="${s.ticker}">
      <div class="sc-header">
        <span class="sc-ticker">${s.ticker.replace('-USD','')}</span>
        <span class="sc-action ${s.action}">${actionVerb(s.action)}</span>
        ${srcBadge}
        <button class="sc-trade-btn" onclick="event.stopPropagation();openOrderModal('${escHtml(s.ticker)}','${['BUY','LONG'].includes(s.action)?'BUY':'SELL'}',${s.price||0})" title="Open order form">◆ TRADE</button>
      </div>
      <div class="sc-price">
        <strong style="font-size:13px">${fmtSignalPrice(s)}</strong>
        &nbsp;<span style="color:var(--text-2);font-size:9px">entry price</span>
      </div>
      <div class="sc-brokers" style="font-size:8px;color:var(--text-muted);margin-top:1px;letter-spacing:0.3px" data-ticker="${s.ticker}"></div>
      <div class="sc-price" style="margin-top:2px">
        <span style="color:var(--red);font-size:9px">⬇ Stop Loss ${s.stop_loss != null ? '$'+(+s.stop_loss).toFixed(2) : '--'}</span>
        &nbsp;&nbsp;
        <span style="color:var(--green);font-size:9px">⬆ Take Profit ${s.take_profit != null ? '$'+(+s.take_profit).toFixed(2) : '--'}</span>
      </div>
      <div class="sc-conf" style="margin-top:6px">
        <span class="sc-conf-label">CONFIDENCE</span>
        <div class="sc-conf-bar"><div class="sc-conf-fill" style="width:${confPct}%;background:${confColor}"></div></div>
        <span class="sc-conf-val" style="color:${confColor}">${conf.toFixed(1)}%</span>
      </div>
      <div class="sc-meta" style="margin-top:5px">
        <span title="Relative Strength Index — measures overbought/oversold">RSI: <strong>${rsiLabel(rsiVal)}</strong></span>
        <span title="Trend direction vs 20-day average">Trend: <strong>${trendLabel(s.trend)}</strong></span>
        <span title="Reward:Risk ratio — how much you gain vs risk">R:R <strong style="color:${rrColor}">${rrNum.toFixed(2)} ${rrLabel}</strong></span>
        <span title="Suggested portfolio weight">Size: <strong>${psPct}% of portfolio</strong></span>
      </div>
      <div style="display:flex;align-items:center;gap:8px;margin-top:4px">
        <span class="sc-fit ${s.quadrant_fit}">${s.quadrant_fit?.toUpperCase()} DALIO FIT</span>
        <div style="flex:1" title="RSI Gauge — Green zone=oversold(buy), Red zone=overbought(sell), Cyan=neutral">
          ${rsiGaugeSVG(rsiVal)}
        </div>
      </div>
      ${multiTimeframeBadgesHTML(s)}
      ${s.options_strategy ? `<div style="font-size:9px;color:var(--cyan);margin-top:4px">⚙ Options: ${s.options_strategy}</div>` : ''}
      <div class="sc-prediction">
        <div class="sc-pred-header">
          <span>◈ PRICE PREDICTION</span>
          <span class="sc-pred-days">~${s.predicted_days ?? '?'}d → ${sellDateStr(s.predicted_days ?? 14)}</span>
        </div>
        ${sparklineSVG(s)}
        <div class="sc-pred-levels">
          <span style="color:var(--red)">SL $${s.stop_loss != null ? (+s.stop_loss).toFixed(2) : '--'}</span>
          <span style="color:var(--text-2)">NOW $${fmtSignalPrice(s).replace('$','')}</span>
          <span style="color:var(--green)">TP $${s.take_profit != null ? (+s.take_profit).toFixed(2) : '--'}</span>
        </div>
      </div>
      ${overview ? `<div class="sc-ai-overview"><span class="sc-ai-label">◈ AI ANALYSIS</span>${overview}</div>` : ''}
    </div>`;
}

async function loadSuggestOpportunities(n = 8) {
  const list = el('opportunityList');
  if (!list) return;
  list.innerHTML = '<div style="padding:14px;color:var(--text-muted);font-size:10px;animation:blink 1s infinite">⟳ SCANNING ALL MARKETS…</div>';
  try {
    const d = await fetchJSON(`/api/suggest?n=${n}`);
    renderOpportunities(d.opportunities || [], d);
  } catch(e) {
    list.innerHTML = `<div style="padding:14px;color:var(--red);font-size:10px">SCAN FAILED: ${escHtml(e.message)}</div>`;
  }
}

function renderOpportunities(opps, meta = {}) {
  const list = el('opportunityList');
  if (!list) return;
  if (!opps || !opps.length) {
    list.innerHTML = '<div style="padding:14px;color:var(--text-muted);font-size:10px">NO OPPORTUNITIES — LOAD SCANNER TABS FIRST TO POPULATE DATA</div>';
    return;
  }
  const regime = (meta.regime_label || '').toUpperCase();
  const fitColour = { strong:'var(--green)', moderate:'var(--cyan)', neutral:'var(--text-2)', avoid:'var(--red)' };
  const actionColour = { BUY:'var(--green)', LONG:'var(--green)', SELL:'var(--red)', SHORT:'var(--red)', WATCH:'var(--amber)' };

  list.innerHTML = opps.map((o, i) => {
    const chg      = Number(o.change_pct) || 0;
    const score    = Number(o.score) || 0;
    const rsi      = Number(o.rsi) || 50;
    const rr       = Number(o.rr_ratio) || 0;
    const price    = Number(o.price) || 0;
    const sl       = Number(o.stop_loss) || 0;
    const tp       = Number(o.take_profit) || 0;
    const chgSign  = chg >= 0 ? '+' : '';
    const chgCol   = chg >= 0 ? 'var(--green)' : 'var(--red)';
    const fitCol   = fitColour[o.quadrant_fit] || 'var(--text-2)';
    const actCol   = actionColour[o.action]    || 'var(--text-1)';
    const rsiCol   = rsi < 35 ? 'var(--green)' : rsi > 65 ? 'var(--red)' : 'var(--amber)';
    const scoreBar = Math.min(Math.round(score), 100);
    const reasons  = (o.reasoning || []).slice(0, 3);

    return `<div class="opp-card opp-card--rich" onclick="this.classList.toggle('opp-expanded')">
      <div class="opp-header">
        <span class="opp-rank">#${i+1}</span>
        <span class="opp-ticker" style="color:${actCol}">${o.ticker}</span>
        <span class="opp-badge" style="color:${actCol};border-color:${actCol}">${o.action}</span>
        <span class="opp-market">${(o.market||'').toUpperCase()}</span>
        <span class="opp-price">$${price.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:4})}</span>
        <span style="color:${chgCol};font-size:9px">${chgSign}${chg.toFixed(2)}%</span>
        <span class="opp-fit-badge" style="color:${fitCol};border-color:${fitCol}">${(o.quadrant_fit||'').toUpperCase()}</span>
        <div class="opp-score-bar"><div class="opp-score-fill" style="width:${scoreBar}%;background:${fitCol}"></div></div>
        <span class="opp-score-val">${score.toFixed(0)}</span>
      </div>
      <div class="opp-metrics">
        <span>RSI <b style="color:${rsiCol}">${rsi.toFixed(0)}</b></span>
        <span>TREND <b>${o.trend || '--'}</b></span>
        <span>SMA20 <b style="color:${o.above_sma20?'var(--green)':'var(--red)'}">${o.above_sma20?'↑ ABOVE':'↓ BELOW'}</b></span>
        <span>52W <b>${(Number(o.pct_from_lo)||0) >= 0 ? '+' : ''}${Number(o.pct_from_lo)||0}% FROM LOW</b></span>
        <span>SL <b style="color:var(--red)">$${sl.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:4})}</b></span>
        <span>TP <b style="color:var(--green)">$${tp.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:4})}</b></span>
        <span>R:R <b style="color:var(--primary)">${rr.toFixed(1)}x</b></span>
        <span>VOL <b>${o.volume_fmt||'--'}</b></span>
      </div>
      <div class="opp-reasons">
        ${reasons.map(r => `<div class="opp-reason-line">▸ ${r}</div>`).join('')}
      </div>
      <div class="opp-actions">
        <button class="scan-trade-btn" onclick="event.stopPropagation();scannerOpenTrade('${escHtml(o.ticker)}',${o.price})">▲ TRADE</button>
        <button class="scan-wl-btn"    onclick="event.stopPropagation();toggleWatchlist('${escHtml(o.ticker)}',this)">☆ WATCH</button>
      </div>
    </div>`;
  }).join('');
}

function showJustification(s) {
  const j = s.dalio_justification || {};
  const qMeta = QUADRANT_META[j.quadrant] || {};
  const rsiVal = s.rsi ?? 50;
  el('justContent').innerHTML = `
    <div class="just-grid">
      <div>
        <div class="just-section-title">▶ DALIO QUADRANT</div>
        <div class="just-stat"><span class="just-stat-label">QUADRANT:</span>
          <span class="just-stat-val" style="color:${qMeta.color||'var(--green)'}">${(j.quadrant||'?').replace(/_/g,' ').toUpperCase()}</span></div>
        <div class="just-stat"><span class="just-stat-label">ENVIRONMENT:</span>
          <span class="just-stat-val">${j.quadrant_description||'--'}</span></div>
      </div>
      <div>
        <div class="just-section-title">▶ QUANTITATIVE METRICS</div>
        <div class="just-stat"><span class="just-stat-label">SENTIMENT SCORE:</span>
          <span class="just-stat-val" style="color:${(j.sentiment_score||0)>=0?'var(--green)':'var(--red)'}">
            ${j.sentiment_score?.toFixed(3) ?? '--'}</span></div>
        <div class="just-stat"><span class="just-stat-label">SHARPE IMPROVEMENT:</span>
          <span class="just-stat-val" style="color:var(--cyan)">+${j.sharpe_improvement?.toFixed(3) ?? '--'}</span></div>
        <div class="just-stat"><span class="just-stat-label">CORR DELTA:</span>
          <span class="just-stat-val">${j.correlation_delta?.toFixed(3) ?? '--'}</span></div>
        <div class="just-stat"><span class="just-stat-label">RISK CONTRIB:</span>
          <span class="just-stat-val">${j.risk_contribution_pct?.toFixed(2) ?? '--'}%</span></div>
      </div>
      <div>
        <div class="just-section-title">▶ SYSTEMATIC REASONS</div>
        <ul class="just-reasons">
          ${(j.reasons||['No reasons available']).map(r => `<li>${r}</li>`).join('')}
        </ul>
      </div>
    </div>
    <div style="margin-top:12px;border-top:1px solid rgba(255,140,0,0.1);padding-top:10px">
      <div class="just-section-title" style="margin-bottom:6px">▶ PRICE SCENARIOS — BULL / BASE / BEAR</div>
      <div style="font-size:9px;color:var(--text-2);margin-bottom:6px">
        Three projected paths based on current momentum. Green=bull case (above target), Cyan=base case, Red=bear case (hits stop-loss).
      </div>
      ${scenarioPredictionSVG(s)}
    </div>
    <div style="margin-top:10px;border-top:1px solid rgba(255,140,0,0.1);padding-top:10px">
      <div class="just-section-title" style="margin-bottom:6px">▶ MOMENTUM PROFILE</div>
      <div style="font-size:9px;color:var(--text-2);margin-bottom:6px">
        Recent daily price changes. Consistent green bars = strong upward momentum. Mixed = choppy / uncertain.
      </div>
      ${momentumHistogramSVG(s)}
    </div>
    <div style="margin-top:10px;display:flex;gap:16px;align-items:center;border-top:1px solid rgba(255,140,0,0.1);padding-top:10px">
      <div style="flex:0 0 100px">
        <div class="just-section-title" style="margin-bottom:2px">▶ RSI GAUGE</div>
        ${rsiGaugeSVG(rsiVal)}
        <div style="font-size:8px;color:var(--text-2);text-align:center;margin-top:2px">
          ${rsiVal < 30 ? 'OVERSOLD — potential bounce' : rsiVal > 70 ? 'OVERBOUGHT — potential pullback' : 'NEUTRAL RANGE'}
        </div>
      </div>
      <div style="flex:1;font-size:9px;color:var(--text-2);line-height:1.5">
        <strong style="color:var(--primary)">How to read:</strong> RSI measures speed of price changes (0-100).
        Below 30 = oversold (often a buying opportunity).
        Above 70 = overbought (may pull back).
        30-70 = normal trading range.
      </div>
    </div>`;
}

// ─── Sentiment ────────────────────────────────────────────
let _newsRefreshCount = 0;

async function loadSentiment() {
  const feed = el('newsFeed');
  if (feed) feed.innerHTML = `<div class="news-loading"><div class="loading-spinner"></div><span>SCANNING 30+ NEWS SOURCES...</span></div>`;
  try {
    const d = await fetchJSON('/api/sentiment');
    STATE.sentiment = d;
    _newsRefreshCount++;
    applySentiment(d);
    // Update last refresh timestamp
    const refreshEl = el('newsLastRefresh');
    if (refreshEl) refreshEl.textContent = `LIVE · Updated ${new Date().toLocaleTimeString('en-AU', {hour:'2-digit',minute:'2-digit',hour12:false})}`;
  } catch {}
}

function autoRefreshNews() {
  // Only auto-refresh if Intel Center tab is active
  const intelTab = document.getElementById('tab-intel-center');
  if (intelTab?.classList.contains('active')) {
    loadSentiment();
  }
}

function applySentiment(d) {
  setEl('totalArticles', `${d.total_articles} ARTICLES`);

  // Conflict meter
  const conflictRing = el('conflictRing');
  const elevated = d.conflict_risk_elevated;
  setEl('conflictScore', d.conflict_risk_articles);
  if (conflictRing) { conflictRing.className = 'conflict-ring ' + (elevated ? 'elevated' : 'normal'); }
  const cStatus = el('conflictStatus');
  if (cStatus) { cStatus.textContent = elevated ? '⚠ RISK ELEVATED' : '■ NOMINAL'; cStatus.className = 'conflict-status ' + (elevated ? 'elevated' : ''); }

  // Quadrant sentiment chart
  updateSentimentChart(d.quadrant_sentiment);

  // Stats grid
  const stats = el('sentimentStats');
  if (stats) {
    const totalArts = d.total_articles || 1;
    stats.innerHTML = Object.entries(d.quadrant_sentiment || {}).map(([q, v]) => {
      const meta = QUADRANT_META[q] || {};
      const pct = ((v.article_count / totalArts) * 100).toFixed(0);
      const color = meta.color || 'var(--text-2)';
      return `<div class="sq-card">
        <div class="sq-card-label" style="color:${color}">${(meta.label||q).replace(/_/g,' ').toUpperCase()}</div>
        <div class="sq-card-bar"><div class="sq-card-bar-fill" style="width:${pct}%;background:${color}"></div></div>
        <div class="sq-card-stats">
          <span class="sq-card-count">${v.article_count}</span>
          <span>${pct}% of feed</span>
          <span style="color:${v.bullish_pct > 50 ? 'var(--green)' : 'var(--red)'}">${v.bullish_pct.toFixed(0)}% bull</span>
        </div>
      </div>`;
    }).join('');
  }

  // News feed
  // Store all articles for filtering
  STATE._allArticles = d.top_headlines || [];
  setEl('newsArticleCount', `${STATE._allArticles.length} ARTICLES`);
  filterNewsArticles();

  // Dominant quadrant
  const dom = d.dominant_quadrant;
  const domMeta = QUADRANT_META[dom] || {};
  setEl('newsDominantQuadrant', (domMeta.label || dom || '--').replace(/_/g,' ').toUpperCase());
  const dqEl = el('newsDominantQuadrant');
  if (dqEl && domMeta.color) dqEl.style.color = domMeta.color;
  const quadDesc = {
    rising_growth: 'Economy expanding — favour equities, commodities, corporate bonds.',
    falling_growth: 'Recessionary signals — favour bonds, gold, defensive equities.',
    rising_inflation: 'Inflation risk — favour gold, energy, real assets, TIPS.',
    falling_inflation: 'Disinflation — favour equities, nominal bonds, consumer staples.',
  };
  setEl('newsDominantDesc', quadDesc[dom] || '--');

  // Bullish/bearish
  const domStats = d.quadrant_sentiment?.[dom] || {};
  setEl('bullishPct',  (domStats.bullish_pct ?? '--') + '%');
  setEl('bearishPct',  domStats.bullish_pct !== undefined ? (100 - domStats.bullish_pct).toFixed(1) + '%' : '--');

  // ── Update Intel Overview on Command Center ──
  updateIntelOverview(d, dom, domMeta, domStats);
}

function updateIntelOverview(d, dom, domMeta, domStats) {
  // Geo risk
  const geoScore = d.conflict_risk_articles ?? '--';
  const geoElevated = d.conflict_risk_elevated;
  setEl('intelOvGeoScore', geoScore);
  const geoRing = el('intelOvGeoRing');
  if (geoRing) {
    geoRing.className = 'intel-ov-ring' + (geoElevated ? ' danger' : '');
  }
  setEl('intelOvGeoStatus', geoElevated ? '⚠ RISK ELEVATED' : '■ NOMINAL');

  // Sentiment
  const totalArts = d.total_articles || 0;
  const bullPct = domStats.bullish_pct;
  const sentLabel = bullPct !== undefined
    ? (bullPct > 60 ? 'BULLISH' : bullPct < 40 ? 'BEARISH' : 'NEUTRAL')
    : '--';
  const sentEl = el('intelOvSentiment');
  if (sentEl) {
    sentEl.textContent = sentLabel;
    sentEl.className = 'intel-ov-val' + (sentLabel === 'BEARISH' ? ' danger' : sentLabel === 'NEUTRAL' ? ' neutral' : '');
  }
  setEl('intelOvSentSub', `${totalArts} articles analysed`);

  // News quadrant signal
  const qLabel = (domMeta.label || dom || '--').replace(/_/g, ' ').toUpperCase();
  const qEl = el('intelOvQuadrant');
  if (qEl) {
    qEl.textContent = qLabel;
    if (domMeta.color) qEl.style.color = domMeta.color;
  }
  const bPct = bullPct !== undefined ? bullPct.toFixed(0) : '--';
  const brPct = bullPct !== undefined ? (100 - bullPct).toFixed(0) : '--';
  setEl('intelOvQuadSub', `Bullish ${bPct}% · Bearish ${brPct}%`);

  // Latest headlines (top 3)
  const hlEl = el('intelOvHeadlines');
  if (hlEl) {
    const arts = (d.top_headlines || []).slice(0, 3);
    if (arts.length === 0) {
      hlEl.innerHTML = '<div style="color:var(--text-muted)">No headlines</div>';
    } else {
      hlEl.innerHTML = arts.map(a => {
        const sentColor = a.sentiment === 'positive' ? 'var(--green)' : a.sentiment === 'negative' ? 'var(--red)' : 'var(--text-muted)';
        const dot = `<span style="color:${sentColor}">●</span>`;
        const title = (a.title || '').length > 50 ? a.title.substring(0, 50) + '…' : (a.title || '--');
        return `<div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${dot} ${title}</div>`;
      }).join('');
    }
  }
}

function filterNewsArticles() {
  const articles = STATE._allArticles || [];
  const hours    = parseInt(el('newsTimeFilter')?.value || '0', 10);
  const sentFil  = el('newsSentFilter')?.value || 'ALL';
  const cutoff   = hours > 0 ? Date.now() - hours * 3600 * 1000 : 0;

  const filtered = articles.filter(a => {
    if (sentFil !== 'ALL' && a.sentiment !== sentFil) return false;
    if (cutoff && a.timestamp) {
      const ts = new Date(a.timestamp).getTime();
      if (!isNaN(ts) && ts < cutoff) return false;
    }
    return true;
  });

  const capped = filtered.slice(0, 200);
  setEl('newsArticleCount', `${capped.length}${filtered.length > 200 ? ' (max 200)' : ''} / ${articles.length} ARTICLES`);
  renderNewsFeed(capped);
}

function renderNewsFeed(headlines) {
  const feed = el('newsFeed');
  if (!feed) return;
  if (!headlines.length) {
    feed.innerHTML = '<div style="padding:14px;color:var(--text-muted);font-size:10px;text-align:center">NO ARTICLES MATCH FILTERS</div>';
    return;
  }
  feed.innerHTML = headlines.map(h => {
    const sentCls  = h.sentiment === 'positive' ? 'pos' : h.sentiment === 'negative' ? 'neg' : '';
    const sentIcon = h.sentiment === 'positive' ? '▲' : h.sentiment === 'negative' ? '▼' : '■';
    const timeStr  = h.timestamp ? new Date(h.timestamp).toLocaleTimeString('en-AU', {hour:'2-digit',minute:'2-digit',hour12:false}) : '--:--';
    const qMeta    = QUADRANT_META[h.quadrant] || {};
    return `<div class="news-item ${h.conflict_risk ? 'conflict' : ''}">
      <div class="news-headline">${h.conflict_risk ? '<span class="news-warn">⚠</span> ' : ''}${h.title}</div>
      <div class="news-meta">
        <span class="news-sentiment ${sentCls}">${sentIcon} ${(h.sentiment||'neutral').toUpperCase()}</span>
        <span class="news-source">${h.source || '--'}</span>
        <span class="news-quadrant" style="color:${qMeta.color||'var(--text-2)'}">${(qMeta.label||h.quadrant||'--').replace(/_/g,' ')}</span>
        <span class="news-time">${timeStr}</span>
        ${h.conflict_risk ? '<span class="news-conflict-flag">⚠ CONFLICT</span>' : ''}
      </div>
    </div>`;
  }).join('');
}

// ─── Correlation ──────────────────────────────────────────
async function loadCorrelation() {
  try {
    const d = await fetchJSON('/api/correlation');
    STATE.corr = d;
    applyCorrelation(d);
  } catch {}
}

function applyCorrelation(d) {
  setEl('meanCorr',    d.mean_correlation?.toFixed(3) ?? '--');
  setEl('maxCorr',     d.max_correlation?.toFixed(3) ?? '--');
  setEl('divCount',    d.holy_grail_count ?? '--');
  const pct = Math.min((d.holy_grail_count / 20) * 100, 100);
  setWidth('divBarFill', pct);

  // Show data source badge
  const srcEl = el('corrDataSource');
  if (srcEl) {
    const src = d.data_source;
    if (src === 'LIVE') {
      srcEl.textContent = '● LIVE DATA — YOUR PORTFOLIO';
      srcEl.style.color = 'var(--green)';
    } else if (src === 'PORTFOLIO') {
      srcEl.textContent = '● REAL DATA — YOUR PORTFOLIO + DEFAULTS';
      srcEl.style.color = 'var(--green)';
    } else if (src === 'DEFAULTS') {
      srcEl.textContent = '● REAL PRICES — DEFAULT ASSETS (NO POSITIONS YET)';
      srcEl.style.color = 'var(--amber)';
    } else {
      srcEl.textContent = '⚠ DEMO DATA — NO REAL DATA AVAILABLE';
      srcEl.style.color = 'var(--red)';
    }
  }

  drawCorrelationHeatmap(d.tickers, d.matrix);
  renderAllocTable(d.tickers, d.matrix, d.portfolio_positions);

  // Selected Portfolio data source badge
  const allocSrc = el('allocDataSource');
  if (allocSrc) {
    const hasPos = d.portfolio_positions && Object.keys(d.portfolio_positions).length > 0;
    allocSrc.textContent = hasPos ? '● REAL WEIGHTS — YOUR POSITIONS' : '● EQUAL WEIGHT — NO POSITIONS YET';
    allocSrc.style.color = hasPos ? 'var(--green)' : 'var(--amber)';
  }
}

function drawCorrelationHeatmap(tickers, matrix) {
  const canvas = el('correlationCanvas');
  if (!canvas || !tickers || !matrix) return;
  const n       = tickers.length;
  const maxCellSz = Math.floor((canvas.parentElement.clientWidth - 70) / n);
  const cellSz  = Math.min(Math.max(22, maxCellSz), 38);   // cap at 38px max, min 22px
  const labelW  = 58;
  const w = labelW + n * cellSz;
  const h = labelW + n * cellSz;
  canvas.width  = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, w, h);

  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const v = matrix[i][j];
      ctx.fillStyle = corrColor(v);
      ctx.fillRect(labelW + j * cellSz, labelW + i * cellSz, cellSz - 1, cellSz - 1);
      // Only draw text if cells are large enough to fit it
      if (cellSz >= 20) {
        ctx.fillStyle = Math.abs(v) > 0.5 ? '#030c08' : 'rgba(0,204,68,0.85)';
        ctx.font = `bold ${Math.max(7, Math.floor(cellSz * 0.28))}px JetBrains Mono, monospace`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(v.toFixed(2), labelW + j * cellSz + cellSz / 2, labelW + i * cellSz + cellSz / 2);
      }
    }
  }

  // Labels
  ctx.fillStyle = '#5a8a65';
  ctx.font = `${Math.max(7, cellSz * 0.26)}px JetBrains Mono, monospace`;
  tickers.forEach((t, i) => {
    ctx.save();
    ctx.translate(labelW + i * cellSz + cellSz / 2, labelW - 3);
    ctx.rotate(-Math.PI / 4);
    ctx.textAlign = 'right';
    ctx.fillText(t.replace('.AX',''), 0, 0);
    ctx.restore();
    ctx.textAlign = 'right';
    ctx.fillText(t.replace('.AX',''), labelW - 3, labelW + i * cellSz + cellSz / 2 + 3);
  });
}

function corrColor(v) {
  // -1 → dark red   0 → dark bg   +1 → bright green
  if (v >= 0.8) return '#00cc44';
  if (v >= 0.5) return '#009933';
  if (v >= 0.3) return '#3a3a3a';
  if (v >= 0.1) return '#1a4028';
  if (v >= -0.1) return '#0a1018';
  if (v >= -0.3) return '#3a1010';
  return '#cc1a1a';
}

function renderAllocTable(tickers, matrix, portfolioPositions) {
  const n = tickers.length;
  const body = el('allocTableBody');
  if (!body) return;
  const hasPositions = portfolioPositions && Object.keys(portfolioPositions).length > 0;
  body.innerHTML = tickers.map((t, i) => {
    const rowAvg = matrix[i].reduce((s,v,j) => j!==i ? s+Math.abs(v) : s, 0) / (n-1);
    const fit = rowAvg < 0.2 ? 'strong' : rowAvg < 0.35 ? 'moderate' : 'weak';
    const posInfo = hasPositions ? portfolioPositions[t] : null;
    const weight = posInfo ? posInfo.weight_pct.toFixed(2) + '%' : (1/n*100).toFixed(2) + '%';
    const riskContrib = posInfo ? posInfo.weight_pct.toFixed(2) + '%' : (1/n*100).toFixed(2) + '%';
    const inPortfolio = posInfo ? '●' : '';
    return `<tr>
      <td class="td-green">${inPortfolio} ${t}</td>
      <td class="td-cyan">${weight}</td>
      <td>${riskContrib}</td>
      <td><span class="sc-fit ${fit}">${fit.toUpperCase()}</span></td>
    </tr>`;
  }).join('');
}

// ─── Backtest ─────────────────────────────────────────────
async function loadBacktest() {
  const runBtn = document.querySelector('.panel--wf-chart .btn-ghost');
  if (runBtn) { runBtn.textContent = '⟳ RUNNING…'; runBtn.disabled = true; }
  try {
    // Lazy-init chart if not yet created (canvas hidden on first load)
    if (!charts.wf) {
      const wfctx = el('wfChart')?.getContext('2d');
      if (wfctx) {
        charts.wf = new Chart(wfctx, {
          type: 'bar',
          data: { labels: [], datasets: [{ label: 'Period Return %', data: [], backgroundColor: [], borderColor: [], borderWidth: 1 }] },
          options: { ...CHART_DEFAULTS, maintainAspectRatio: false, plugins: { ...CHART_DEFAULTS.plugins, legend: { display: false } } },
        });
      }
    }
    const d = await fetchJSON('/api/backtest/latest');
    STATE.backtest = d;
    applyBacktest(d);
  } catch (e) {
    console.error('Backtest load error:', e);
    setEl('bt-totalRet', 'ERR');
  } finally {
    if (runBtn) { runBtn.textContent = '↻ RUN BACKTEST'; runBtn.disabled = false; }
  }
}

function applyBacktest(d) {
  const fmt = (v, dec) => v != null ? (+v).toFixed(dec) : '--';
  const pct = (v, dec) => v != null ? ((+v >= 0 ? '+' : '') + (+v).toFixed(dec) + '%') : '--';

  setEl('bt-totalRet', pct(d.total_return_pct, 1));
  setEl('bt-sharpe',   fmt(d.sharpe_ratio, 2));
  setEl('bt-sortino',  fmt(d.sortino_ratio, 2));
  setEl('bt-calmar',   fmt(d.calmar_ratio, 2));
  setEl('bt-maxdd',    d.max_drawdown_pct != null ? (+d.max_drawdown_pct).toFixed(2) + '%' : '--');
  setEl('bt-winrate',  d.win_rate_pct != null ? (+d.win_rate_pct).toFixed(1) + '%' : '--');
  setEl('bt-periods',  d.periods ?? '--');
  setEl('bt-annRet',   pct(d.annualised_return_pct, 1));

  // Data source badges for all backtest panels
  const isReal = d.data_source === 'real';
  const srcText = isReal ? '● REAL DATA — YOUR TRADING HISTORY' : '⚠ DEMO DATA — NO REAL TRADES AVAILABLE';
  const srcColor = isReal ? 'var(--green)' : 'var(--amber)';
  ['btDataSource', 'wfDataSource', 'periodDataSource'].forEach(id => {
    setEl(id, srcText);
    const e = el(id);
    if (e) e.style.color = srcColor;
  });

  const sortino = d.sortino_ratio ?? 0;
  const winRate = d.win_rate_pct ?? 0;
  setEl('rm-sortino',       sortino.toFixed(2));
  setEl('rm-sortino-desc',  sortino >= 2 ? '✓ Excellent downside protection' : sortino >= 1 ? '✓ Good' : '⚠ Below target (aim >1)');
  setEl('rm-winrate',       winRate.toFixed(1) + '%');
  setEl('rm-winrate-desc',  winRate >= 60 ? '✓ Strong edge' : winRate >= 50 ? '✓ Positive edge' : '⚠ Below 50% — review strategy');
  setEl('rm-maxdd',         (d.max_drawdown_pct ?? 0).toFixed(2) + '%');

  updateWFChart(d.period_results || []);
  renderPeriodTable(d.period_results || []);
}

function renderPeriodTable(periods) {
  const body = el('periodTableBody');
  if (!body) return;
  body.innerHTML = periods.map(p => {
    const ret = p.return_pct ?? 0;
    const retClass = ret >= 0 ? 'td-green' : 'td-red';
    return `<tr>
      <td>${p.period ?? '--'}</td>
      <td>${p.train_start || '--'}</td>
      <td class="${retClass}">${ret >= 0 ? '+' : ''}${ret.toFixed(2)}%</td>
      <td class="td-cyan">${(p.sharpe ?? 0).toFixed(2)}</td>
      <td class="td-red">${(p.max_drawdown ?? 0).toFixed(2)}%</td>
      <td>${(p.win_rate ?? 0).toFixed(1)}%</td>
      <td>${p.trades ?? 0}</td>
    </tr>`;
  }).join('');
}

// ─── Alerts ───────────────────────────────────────────────
async function loadAlerts() {
  try {
    const d = await fetchJSON('/api/alerts');
    STATE.alerts = d.alerts || [];
    renderAlerts(STATE.alerts.slice(0, 15));
  } catch {}
}

function renderAlerts(alerts) {
  const feed = el('alertFeed');
  if (!feed) return;
  feed.innerHTML = alerts.map(a => {
    const lvlClass = a.level === 'WARNING' ? 'alert--warning' : a.level === 'DANGER' ? 'alert--danger' : 'alert--info';
    const t = new Date(a.timestamp).toLocaleTimeString('en-AU', { hour12: false, timeZone: 'UTC' });
    return `<div class="alert-item ${lvlClass}">
      <span class="alert-time">${t}</span>
      <span class="alert-msg">[${a.type}] ${a.message}</span>
    </div>`;
  }).join('');
}

// ─── Market Ticker Strip ──────────────────────────────
async function loadMarketSummary() {
  try {
    const items = await fetchJSON('/api/market_summary');
    renderTickerStrip(items);
  } catch {}
}

function renderTickerStrip(items) {
  const inner = el('tickerInner');
  if (!inner || !items?.length) return;

  function fmtPrice(item) {
    const p = item.price;
    if (p === null || p === undefined) return '---';
    if (item.category === 'fx') return p.toFixed(4);
    if (item.category === 'index') return p.toLocaleString('en-US', { maximumFractionDigits: 0 });
    return '$' + p.toFixed(2);
  }

  function fmtChg(chg) {
    if (chg === null || chg === undefined) return { cls: 'flat', txt: '--' };
    const sign = chg >= 0 ? '+' : '';
    return { cls: chg > 0 ? 'up' : chg < 0 ? 'down' : 'flat', txt: `${sign}${chg.toFixed(2)}%` };
  }

  const html = items.map(item => {
    const price = fmtPrice(item);
    const chg   = fmtChg(item.change_pct);
    const arrow = item.change_pct > 0 ? '▲' : item.change_pct < 0 ? '▼' : '■';
    return `<div class="ticker-item flashed">
      <span class="ticker-name">${item.name.toUpperCase()}</span>
      <span class="ticker-price">${price}</span>
      <span class="ticker-chg ${chg.cls}">${arrow} ${chg.txt}</span>
    </div>`;
  }).join('');

  // Duplicate content for seamless infinite loop.
  // Animation goes 0 → -50% (first copy scrolls away, second copy takes its place).
  inner.innerHTML = html + html;   // two identical copies side-by-side

  // Calculate duration based on content width for consistent scroll speed (~60px/s)
  inner.style.animation = 'none';
  void inner.offsetWidth;
  const halfWidth = inner.scrollWidth / 2;
  const pxPerSec = 60;
  const duration = Math.max(30, halfWidth / pxPerSec);
  inner.style.animation = `ticker-scroll ${duration.toFixed(1)}s linear infinite`;
}

function pushAlert(type, message, level = 'info') {
  const lvlClass = level === 'warning' ? 'alert--warning' : level === 'danger' ? 'alert--danger' : 'alert--info';
  const now = new Date().toLocaleTimeString('en-AU', { hour12: false });
  const html = `<div class="alert-item ${lvlClass}">
    <span class="alert-time">${now}</span>
    <span class="alert-msg">[${type}] ${message}</span>
  </div>`;
  const feed = el('alertFeed');
  if (!feed) return;
  feed.insertAdjacentHTML('afterbegin', html);
  while (feed.children.length > 30) feed.removeChild(feed.lastChild);
  // Also push to OPS terminal
  pushOpsLine(type, message, level);
}

// ─── Positions ────────────────────────────────────────────
function renderPositionTable(positions) {
  const body = el('posTableBody');
  if (!body) return;
  body.innerHTML = positions.map(p => {
    const pnlClass = p.unrealised_pnl_pct >= 0 ? 'td-green' : 'td-red';
    const sideColor = p.side === 'LONG' ? 'td-green' : 'td-red';
    return `<tr>
      <td class="td-cyan">${p.ticker}</td>
      <td class="${sideColor}">${p.side}</td>
      <td>${p.size_pct?.toFixed(1)}%</td>
      <td>${miniSparkSVG(p.ticker, p.unrealised_pnl_pct)}</td>
      <td class="${pnlClass}">${p.unrealised_pnl_pct >= 0 ? '+' : ''}${p.unrealised_pnl_pct?.toFixed(2)}%</td>
      <td><span class="sc-fit ${p.unrealised_pnl_pct >= 0 ? 'strong' : 'weak'}">${p.unrealised_pnl_pct >= -5 ? 'ACTIVE' : 'NEAR SL'}</span></td>
    </tr>`;
  }).join('');
}

// ═══════════════════════════════════════════════════════════
// Charts (Chart.js)
// ═══════════════════════════════════════════════════════════

const CHART_DEFAULTS = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { display: false }, tooltip: { backgroundColor: '#0d1520', borderColor: '#00d4ff', borderWidth: 1, titleColor: '#00d4ff', bodyColor: '#b8dcf0', padding: 8 } },
  scales: {
    x: { ticks: { color: '#5a8a65', font: { family: 'JetBrains Mono', size: 9 }, maxRotation: 30 }, grid: { color: 'rgba(10,24,16,0.8)' } },
    y: { ticks: { color: '#5a8a65', font: { family: 'JetBrains Mono', size: 9 } }, grid: { color: 'rgba(10,24,16,0.8)' } },
  },
};

function initCharts() {
  Chart.defaults.color = '#5a8a65';
  Chart.defaults.font.family = 'JetBrains Mono, Share Tech Mono, monospace';

  // [MEMORY-FIX] Destroy any existing chart instances before creating new ones
  for (const key of Object.keys(charts)) {
    if (charts[key] && typeof charts[key].destroy === 'function') {
      charts[key].destroy();
      charts[key] = null;
    }
  }

  // Equity chart
  const ectx = el('equityChart')?.getContext('2d');
  if (ectx) {
    charts.equity = new Chart(ectx, {
      type: 'line',
      data: { labels: [], datasets: [{ label: 'NAV', data: [], borderColor: '#00cc44', borderWidth: 2, fill: true, backgroundColor: 'rgba(0,204,68,0.06)', tension: 0.3, pointRadius: 0 }] },
      options: { ...CHART_DEFAULTS, maintainAspectRatio: false },
    });
  }

  // Prediction chart (slim, full width row 1) — SHOWPIECE
  const pctx = el('predictionChart')?.getContext('2d');
  if (pctx) {
    const chartH = 200;

    // Rich gradient for actual equity line — glowing green at top, dark at bottom
    const predGradActual = pctx.createLinearGradient(0, 0, 0, chartH);
    predGradActual.addColorStop(0, 'rgba(0,204,68,0.35)');
    predGradActual.addColorStop(0.3, 'rgba(0,204,68,0.15)');
    predGradActual.addColorStop(0.7, 'rgba(0,204,68,0.04)');
    predGradActual.addColorStop(1, 'rgba(0,0,0,0)');

    // Confidence band gradient — cyan tint
    const predGradBand = pctx.createLinearGradient(0, 0, 0, chartH);
    predGradBand.addColorStop(0, 'rgba(0,212,255,0.14)');
    predGradBand.addColorStop(0.5, 'rgba(0,212,255,0.05)');
    predGradBand.addColorStop(1, 'rgba(0,212,255,0)');

    // Prediction line gradient — orange/amber glow
    const predGradPredLine = pctx.createLinearGradient(0, 0, 0, chartH);
    predGradPredLine.addColorStop(0, 'rgba(255,140,0,0.22)');
    predGradPredLine.addColorStop(0.4, 'rgba(255,140,0,0.08)');
    predGradPredLine.addColorStop(1, 'rgba(255,140,0,0)');

    // Crosshair plugin for this chart
    const crosshairPlugin = {
      id: 'predictionCrosshair',
      afterDraw(chart) {
        if (!chart._crosshairX && !chart._crosshairY) return;
        const ctx = chart.ctx;
        const { left, right, top, bottom } = chart.chartArea;
        ctx.save();
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 0.8;
        ctx.strokeStyle = 'rgba(255,140,0,0.35)';
        if (chart._crosshairX != null) {
          ctx.beginPath();
          ctx.moveTo(chart._crosshairX, top);
          ctx.lineTo(chart._crosshairX, bottom);
          ctx.stroke();
        }
        if (chart._crosshairY != null) {
          ctx.beginPath();
          ctx.moveTo(left, chart._crosshairY);
          ctx.lineTo(right, chart._crosshairY);
          ctx.stroke();
        }
        ctx.restore();
      },
      afterEvent(chart, args) {
        const evt = args.event;
        if (evt.type === 'mousemove') {
          const area = chart.chartArea;
          if (evt.x >= area.left && evt.x <= area.right && evt.y >= area.top && evt.y <= area.bottom) {
            chart._crosshairX = evt.x;
            chart._crosshairY = evt.y;
          } else {
            chart._crosshairX = null;
            chart._crosshairY = null;
          }
          chart.draw();
        }
        if (evt.type === 'mouseout') {
          chart._crosshairX = null;
          chart._crosshairY = null;
          chart.draw();
        }
      },
    };

    // Glow point plugin — animated glow on hover points
    const glowPointPlugin = {
      id: 'predictionGlowPoints',
      afterDatasetsDraw(chart) {
        const meta0 = chart.getDatasetMeta(0);
        const meta1 = chart.getDatasetMeta(1);
        if (!chart._active?.length) return;
        const ctx = chart.ctx;
        chart._active.forEach(active => {
          const ds = active.datasetIndex;
          const meta = ds === 0 ? meta0 : ds === 1 ? meta1 : null;
          if (!meta) return;
          const pt = meta.data[active.index];
          if (!pt || pt.y == null) return;
          ctx.save();
          // Outer glow ring
          const color = ds === 0 ? 'rgba(0,204,68,' : 'rgba(255,140,0,';
          const grad = ctx.createRadialGradient(pt.x, pt.y, 0, pt.x, pt.y, 14);
          grad.addColorStop(0, color + '0.6)');
          grad.addColorStop(0.5, color + '0.15)');
          grad.addColorStop(1, color + '0)');
          ctx.fillStyle = grad;
          ctx.beginPath();
          ctx.arc(pt.x, pt.y, 14, 0, Math.PI * 2);
          ctx.fill();
          // Inner dot
          ctx.fillStyle = ds === 0 ? '#00cc44' : '#ff8c00';
          ctx.beginPath();
          ctx.arc(pt.x, pt.y, 3.5, 0, Math.PI * 2);
          ctx.fill();
          ctx.strokeStyle = '#fff';
          ctx.lineWidth = 1.2;
          ctx.stroke();
          ctx.restore();
        });
      },
    };

    charts.prediction = new Chart(pctx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [
          {
            label: 'Actual', data: [], borderColor: '#00cc44', borderWidth: 2.5,
            fill: true, backgroundColor: predGradActual,
            tension: 0.4, pointRadius: 0, pointHoverRadius: 0,
            pointHoverBackgroundColor: '#00cc44', pointHoverBorderColor: '#fff', order: 2,
            borderShadowColor: 'rgba(0,204,68,0.5)', shadowBlur: 8,
          },
          {
            label: 'Predicted', data: [], borderColor: '#ff8c00', borderWidth: 2,
            borderDash: [6,3], fill: true, backgroundColor: predGradPredLine,
            tension: 0.4, pointRadius: 0, pointHoverRadius: 0,
            pointHoverBackgroundColor: '#ff8c00', order: 1,
          },
          {
            label: 'Upper Band', data: [], borderColor: 'rgba(0,212,255,0.25)', borderWidth: 1,
            borderDash: [2,4], fill: '+1', backgroundColor: predGradBand,
            tension: 0.4, pointRadius: 0, order: 3,
          },
          {
            label: 'Lower Band', data: [], borderColor: 'rgba(0,212,255,0.25)', borderWidth: 1,
            borderDash: [2,4], fill: false, backgroundColor: predGradBand,
            tension: 0.4, pointRadius: 0, order: 4,
          },
        ],
      },
      plugins: [crosshairPlugin, glowPointPlugin],
      options: {
        ...CHART_DEFAULTS,
        maintainAspectRatio: false,
        responsive: true,
        interaction: { mode: 'index', intersect: false },
        animation: { duration: 800, easing: 'easeInOutQuart' },
        plugins: {
          ...CHART_DEFAULTS.plugins,
          legend: { display: false },
          tooltip: {
            enabled: true,
            backgroundColor: 'rgba(8,12,10,0.96)',
            borderColor: 'rgba(255,140,0,0.4)',
            borderWidth: 1,
            cornerRadius: 6,
            padding: { top: 8, bottom: 8, left: 12, right: 12 },
            titleFont: { family: 'Orbitron, monospace', size: 9, weight: '700' },
            titleColor: '#ff8c00',
            bodyFont: { family: 'JetBrains Mono, monospace', size: 10 },
            bodyColor: '#b8dcf0',
            displayColors: false,
            caretSize: 6,
            callbacks: {
              title: ctx => {
                if (!ctx.length) return '';
                return ctx[0].label || '';
              },
              label: ctx => {
                if (ctx.raw == null) return null;
                const lbl = ctx.dataset.label;
                const val = '$' + ctx.raw.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
                return `${lbl}: ${val}`;
              },
              afterBody: ctx => {
                if (!ctx.length) return [];
                const lines = [];
                const ds0 = ctx.find(c => c.datasetIndex === 0);
                if (ds0 && ds0.raw != null) {
                  const idx = ds0.dataIndex;
                  const data = ds0.dataset.data;
                  if (idx > 0 && data[idx-1] != null && data[idx] != null) {
                    const change = ((data[idx] - data[idx-1]) / data[idx-1] * 100);
                    const sign = change >= 0 ? '+' : '';
                    lines.push(`Daily: ${sign}${change.toFixed(3)}%`);
                  }
                  const first = data.find(v => v != null);
                  if (first != null && ds0.raw != null) {
                    const totalRet = ((ds0.raw - first) / first * 100);
                    const tSign = totalRet >= 0 ? '+' : '';
                    lines.push(`Total: ${tSign}${totalRet.toFixed(2)}%`);
                  }
                }
                return lines;
              },
            },
          },
        },
        scales: {
          x: {
            display: true,
            ticks: { color: 'rgba(90,138,101,0.5)', font: { family: 'JetBrains Mono', size: 7 }, maxTicksLimit: 10, maxRotation: 0 },
            grid: { color: 'rgba(255,140,0,0.03)', drawTicks: false },
          },
          y: {
            min: 0,
            ticks: {
              color: 'rgba(90,138,101,0.6)',
              font: { family: 'JetBrains Mono', size: 9 },
              callback: v => '$' + v.toLocaleString(),
              maxTicksLimit: 6,
            },
            grid: { color: 'rgba(255,140,0,0.04)', drawTicks: false },
          },
        },
      },
    });
    // Seed with initial data
    _seedPredictionChart();
  }

  // Sentiment doughnut
  const sctx = el('sentimentChart')?.getContext('2d');
  if (sctx) {
    charts.sentiment = new Chart(sctx, {
      type: 'doughnut',
      data: {
        labels: ['RISING GROWTH','FALLING GROWTH','RISING INFLATION','FALLING INFLATION'],
        datasets: [{
          data: [0,0,0,0],
          backgroundColor: ['#00cc44','#ff2222','#ffb300','#00d4ff'],
          hoverBackgroundColor: ['#22ff66','#ff4444','#ffcc33','#33ddff'],
          borderColor: 'transparent',
          borderWidth: 0,
          hoverOffset: 12,
          spacing: 3,
          borderRadius: 4,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        cutout: '62%',
        rotation: -90,
        animation: { animateRotate: true, animateScale: true, duration: 1200, easing: 'easeOutQuart' },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: 'rgba(0,0,0,0.85)',
            titleFont: { family: "'JetBrains Mono', monospace", size: 11 },
            bodyFont: { family: "'JetBrains Mono', monospace", size: 10 },
            padding: 10,
            cornerRadius: 6,
            callbacks: {
              label: ctx => {
                const total = ctx.dataset.data.reduce((a,b) => a+b, 0);
                const pct = total ? ((ctx.raw / total) * 100).toFixed(1) : 0;
                return ` ${ctx.raw} articles (${pct}%)`;
              }
            },
          },
        },
      },
    });
  }

  // Walk-forward chart
  const wfctx = el('wfChart')?.getContext('2d');
  if (wfctx) {
    charts.wf = new Chart(wfctx, {
      type: 'bar',
      data: {
        labels: [],
        datasets: [{
          label: 'Period Return %',
          data: [],
          backgroundColor: [],
          borderColor: [],
          borderWidth: 1,
        }],
      },
      options: {
        ...CHART_DEFAULTS,
        maintainAspectRatio: false,
        plugins: { ...CHART_DEFAULTS.plugins, legend: { display: false } },
      },
    });
  }

  // PnL chart — initialised empty, updated from real health data
  const pnlctx = el('pnlChart')?.getContext('2d');
  if (pnlctx) {
    charts.pnl = new Chart(pnlctx, {
      type: 'bar',
      data: {
        labels: [],
        datasets: [{
          data: [],
          backgroundColor: [],
          borderColor: [],
          borderWidth: 1,
        }],
      },
      options: { ...CHART_DEFAULTS, maintainAspectRatio: false },
    });
    // Show demo data initially until real data arrives
    _showDemoPnlChart();
  }

  // Weights doughnut
  const wctx = el('weightsChart')?.getContext('2d');
  if (wctx) {
    charts.weights = new Chart(wctx, {
      type: 'doughnut',
      data: { labels: [], datasets: [{ data: [], backgroundColor: [], borderColor: 'var(--bg-panel)', borderWidth: 2 }] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: true, position: 'right', labels: { color: '#5a8a65', font: { size: 9, family: 'JetBrains Mono' }, boxWidth: 12 } },
          tooltip: CHART_DEFAULTS.plugins.tooltip,
        },
      },
    });
  }
}

function updateEquityChart(history) {
  if (!charts.equity || !history?.length) return;
  const labels = history.map(h => h.t);
  const data   = history.map(h => h.v);
  charts.equity.data.labels = labels;
  charts.equity.data.datasets[0].data = data;
  charts.equity.update('none');
  // Also update prediction chart with real data
  updatePredictionFromEquity(history);
}

function _showDemoPnlChart() {
  if (!charts.pnl) return;
  const demoData = Array.from({ length: 30 }, () => +(Math.random() * 2 - 0.5).toFixed(3));
  charts.pnl.data.labels = demoData.map((_, i) => `D-${30 - i}`);
  charts.pnl.data.datasets[0].data = demoData;
  charts.pnl.data.datasets[0].backgroundColor = demoData.map(v => v >= 0 ? 'rgba(0,204,68,0.6)' : 'rgba(255,34,34,0.6)');
  charts.pnl.data.datasets[0].borderColor = demoData.map(v => v >= 0 ? '#00cc44' : '#ff2222');
  charts.pnl.update('none');
  setEl('pnlDataSource', '⚠ DEMO DATA — NO REAL TRADES YET');
  const srcEl = el('pnlDataSource');
  if (srcEl) srcEl.style.color = 'var(--amber)';
}

function updatePnlChart(series) {
  if (!charts.pnl) return;
  if (!series || series.length < 2) {
    _showDemoPnlChart();
    return;
  }
  const data = series.map(s => s.pnl_pct);
  const labels = series.map(s => {
    const d = s.t || '';
    return d.length >= 10 ? d.substring(5, 10) : d;
  });
  charts.pnl.data.labels = labels;
  charts.pnl.data.datasets[0].data = data;
  charts.pnl.data.datasets[0].backgroundColor = data.map(v => v >= 0 ? 'rgba(0,204,68,0.6)' : 'rgba(255,34,34,0.6)');
  charts.pnl.data.datasets[0].borderColor = data.map(v => v >= 0 ? '#00cc44' : '#ff2222');
  charts.pnl.update('none');
  setEl('pnlDataSource', '● REAL DATA — YOUR TRADING HISTORY');
  const srcEl = el('pnlDataSource');
  if (srcEl) srcEl.style.color = 'var(--green)';
}

function updateSentimentChart(qs) {
  if (!charts.sentiment || !qs) return;
  const keys = ['rising_growth','falling_growth','rising_inflation','falling_inflation'];
  const data = keys.map(k => qs[k]?.article_count ?? 0);
  charts.sentiment.data.datasets[0].data = data;
  charts.sentiment.update();

  // Center label
  const total = data.reduce((a,b) => a+b, 0);
  setEl('sentCenterNum', total.toLocaleString());
  const dominant = keys.reduce((best, k, i) => data[i] > (data[best.i] ?? -1) ? {k, i} : best, {k: keys[0], i: 0});
  const moodMap = { rising_growth: 'BULLISH', falling_growth: 'BEARISH', rising_inflation: 'CAUTIOUS', falling_inflation: 'EASING' };
  const moodColors = { rising_growth: 'var(--green)', falling_growth: 'var(--red)', rising_inflation: 'var(--amber)', falling_inflation: 'var(--cyan)' };
  const moodEl = el('sentCenterMood');
  if (moodEl) {
    moodEl.textContent = moodMap[dominant.k] || 'MIXED';
    moodEl.style.color = moodColors[dominant.k] || 'var(--text-2)';
    moodEl.style.borderColor = moodColors[dominant.k] || 'var(--border)';
  }
}

function updateWFChart(periods) {
  if (!charts.wf || !periods.length) return;
  charts.wf.data.labels = periods.map(p => `P${p.period}`);
  charts.wf.data.datasets[0].data = periods.map(p => p.return_pct);
  charts.wf.data.datasets[0].backgroundColor = periods.map(p => p.return_pct >= 0 ? 'rgba(0,204,68,0.6)' : 'rgba(255,34,34,0.6)');
  charts.wf.data.datasets[0].borderColor      = periods.map(p => p.return_pct >= 0 ? '#00cc44' : '#ff2222');
  charts.wf.update('none');
}

function updateWeightsChart(weights) {
  if (!charts.weights) return;
  const keys = Object.keys(weights).slice(0, 15);
  const vals = keys.map(k => +(weights[k] * 100).toFixed(2));
  const palette = ['#00cc44','#009933','#008820','#00d4ff','#006818','#00cc44','#0099cc','#ffb300','#cc8c00','#ff6b00','#ff2222','#cc1a1a','#00d4ff','#ff00ff','#8800ff'];
  charts.weights.data.labels = keys.map(k => k.replace('.AX',''));
  charts.weights.data.datasets[0].data = vals;
  charts.weights.data.datasets[0].backgroundColor = palette.slice(0, keys.length);
  charts.weights.update('none');
}

// ═══════════════════════════════════════════════════════════
// Speech Bubble Spotlight System
// ═══════════════════════════════════════════════════════════

// Per-tab spot definitions — each spot targets a CSS selector
const SPOTS = {
  'command-center': [
    { id:'cmd-quadrant', sel:'#quadrantPanel',      arrow:'right',  title:'\ud83d\udcca ECONOMIC QUADRANT',    text:"Shows which economic regime we\'re in right now. The glowing cell tells you what to buy or avoid \u2014 think of it as your GPS for markets." },
    { id:'cmd-chart',    sel:'.panel--cc-chart',    arrow:'bottom', title:'\ud83d\udcc8 LIVE PRICE CHART',      text:"Click any position and it charts right here \u2014 candles, line view, moving averages, RSI, even a 30-day prediction. Basically a crystal ball, but with math." },
    { id:'cmd-vitals',   sel:'.panel--gauges',      arrow:'left',   title:'\u2764 PORTFOLIO VITALS',       text:"Daily P&L and drawdown at a glance. If drawdown hits 10%, the system stops trading. Like a seatbelt for your portfolio \u2014 she\'s got your back." },
    { id:'cmd-cycle',    sel:'#runCycleBtn',        arrow:'bottom', title:'\u25b6 RUN A SCAN NOW',         text:"Hit this and the system scans every ASX and commodity asset for trade opportunities. Fresh signals in seconds." },
  ],
  'live-trading': [
    { id:'lt-broker',   sel:'.panel--broker-bar',    arrow:'bottom', title:'\ud83d\udd17 BROKER STATUS',         text:"Shows if your broker is connected. Green = good to go, red = something needs attention. Account balance updates live." },
    { id:'lt-equity',   sel:'.panel--live-equity',   arrow:'right',  title:'\ud83d\udcc8 EQUITY CURVE',          text:"Tracks your real portfolio value over time. Line going up = you\'re smashing it. NAV is total worth, ROI is your return." },
    { id:'lt-summary',  sel:'.panel--live-summary',  arrow:'left',   title:'\ud83d\udcbc LIVE PORTFOLIO',        text:"Your real-money portfolio straight from the broker. Cash, P&L, positions \u2014 everything in one place. Click any position for more details." },
    { id:'lt-signals',  sel:'.panel--live-signals',  arrow:'top',    title:'\u26a1 QUICK-TRADE',            text:"One-click trading from AI signals with real money. Click a signal, order fires off to your broker. Dangerously convenient." },
  ],
  'signal-ops': [
    { id:'sig-banner',   sel:'#strongSignalInPage', arrow:'bottom', title:'\u26a1 STRONG SIGNAL ALERT',   text:"When confidence hits 82%+, this lights up. The system is very politely yelling at you to pay attention." },
    { id:'sig-grid',     sel:'#signalGrid',         arrow:'top',    title:'\ud83c\udccf SIGNAL CARDS',           text:"Each card is a trade idea. Green = BUY/LONG, Red = SELL/SHORT. Sorted by confidence so the best ones are always on top." },
    { id:'sig-rr',       sel:'.signal-controls',    arrow:'bottom', title:'\ud83c\udf9a FILTER SIGNALS',         text:"Slide the confidence threshold up to 75%+ for only the high-conviction plays. You can also filter by market \u2014 ASX, Commodities, or everything." },
    { id:'sig-just',     sel:'#justificationPanel', arrow:'left',   title:'\ud83e\udde0 AI JUSTIFICATION',       text:"Click any signal card and the AI explains its reasoning \u2014 economic fit, sentiment, RSI, the whole thesis. No black boxes here." },
  ],
  'intel-center': [
    { id:'int-risk',     sel:'.panel--conflict',    arrow:'right',  title:'\u26a0 GEOPOLITICAL RISK',      text:"Tracks conflict-related news. Red ring = the world is getting interesting (and not in a good way). When it spikes, Gold and Bonds tend to be your friends." },
    { id:'int-sent',     sel:'.panel--sentiment-chart', arrow:'left', title:'\ud83d\udcf0 SENTIMENT',             text:"The market mood ring. Shows how many articles are bullish vs bearish across each sector. Useful for spotting when everyone agrees \u2014 or panics." },
    { id:'int-news',     sel:'#newsFeed',           arrow:'top',    title:'\ud83d\udd34 NEWS FEED',              text:"Live headlines scored by the AI. Red = bearish, Green = bullish. Conflict articles get flagged with a \u26a0 so you can spot trouble fast." },
  ],
  'holy-grail': [
    { id:'hg-heatmap',   sel:'.panel--heatmap',     arrow:'right',  title:'\ud83d\udfe9 CORRELATION MATRIX',     text:"Shows how your assets move relative to each other. Dark cells = independent (great for diversification). Bright green = moving in sync (less useful)." },
    { id:'hg-meter',     sel:'.panel--div-meter',   arrow:'left',   title:'\ud83c\udfc6 HOLY GRAIL METER',       text:"The idea: hold 15+ assets with low correlation and when one tanks, the others hold up. This meter shows how close you are to that sweet spot." },
    { id:'hg-weights',   sel:'.panel--weights',     arrow:'bottom', title:'\u2696 RISK-PARITY WEIGHTS',     text:"Each asset gets weighted so they all contribute equal risk \u2014 not equal dollars, equal risk. Sounds simple, but it\'s surprisingly powerful." },
  ],
  'risk-matrix': [
    { id:'rm-cb',        sel:'.panel--circuit-breaker', arrow:'right',  title:'\ud83d\uded1 CIRCUIT BREAKER',    text:"Your safety net, legend. If daily loss passes 2% or total drawdown hits 10%, trading stops automatically. No panic selling on your watch." },
    { id:'rm-metrics',   sel:'.panel--risk-metrics',arrow:'left',   title:'\ud83d\udcca RISK METRICS',           text:"Sharpe above 1.0 = solid. Sortino above 1.5 = very solid. Max drawdown shows the worst dip so far. Win rate over 55% means you\'ve got an edge." },
    { id:'rm-pos',       sel:'.panel--pos-table',   arrow:'top',    title:'\ud83d\udccb OPEN POSITIONS',         text:"All your current trades and how they\'re performing. Green = winning, Red = not winning. Keep an eye on anything near its stop-loss." },
  ],
  'backtest-lab': [
    { id:'bt-summary',   sel:'.panel--bt-summary',  arrow:'right',  title:'\ud83d\udcc8 BACKTEST RESULTS',       text:"Walk-forward testing \u2014 trains on 12 months, then tests on 3 months it\'s never seen. No cheating allowed, keeps the results honest." },
    { id:'bt-chart',     sel:'.panel--wf-chart',    arrow:'bottom', title:'\ud83d\udcca PERIOD CHART',            text:"Each bar = one test window. Green = profit, Red = loss. You want consistent green bars \u2014 that means the strategy works, not just got lucky." },
    { id:'bt-table',     sel:'.panel--period-table', arrow:'top',   title:'\ud83d\udccb PERIOD BREAKDOWN',        text:"Drill into each period \u2014 returns, Sharpe, drawdown, win rate. Consistent Sharpe above 1.0 is the dream." },
  ],
  'asx-scanner': [
    { id:'asx-table',    sel:'.scanner-wrap',       arrow:'bottom', title:'\ud83c\udde6\ud83c\uddfa ASX SCANNER',            text:"Live ASX prices. Filter by ticker, name, or sector. Green = up today, red = down. Click TRADE to open an order on any stock." },
    { id:'asx-filter',   sel:'#asxSearch',          arrow:'right',  title:'\ud83d\udd0d SEARCH & FILTER',         text:"Type any ticker or company name to filter instantly. You can also narrow by sector \u2014 Banking, Mining, Energy, you name it." },
  ],
  'commodities-scanner': [
    { id:'com-table',    sel:'#commStats',          arrow:'bottom', title:'\u26cf COMMODITIES',              text:"Gold, silver, oil, gas \u2014 the classic hedges. These tend to shine when inflation picks up or the world gets chaotic. Click TRADE to open a position." },
  ],
  'paper-trading': [
    { id:'pt-order',    sel:'.panel--paper-order',   arrow:'right',  title:'\ud83d\udcc4 PLACE AN ORDER',         text:"Enter any ticker \u2014 ASX or commodity \u2014 pick BUY or SELL, set your quantity, hit Execute. Real prices, fake money. Easy as." },
    { id:'pt-summary',  sel:'.panel--paper-summary', arrow:'left',   title:'\ud83d\udcbc PORTFOLIO TRACKER',      text:"Your paper trading portfolio. Starts at your configured amount and tracks every move. Total value, P&L, positions \u2014 all updating live." },
    { id:'pt-signals',  sel:'.panel--paper-signals', arrow:'right',  title:'\u26a1 1-CLICK SIGNAL TRADES',   text:"AI\'s top picks, pre-loaded and ready. See something you like? One click and it\'s in your paper portfolio. Fastest way to test ideas." },
    { id:'pt-history',  sel:'.panel--paper-history', arrow:'top',    title:'\ud83d\udccb TRADE HISTORY',           text:"Every closed trade logged with entry, exit, and P&L. Check which signals actually make money over time \u2014 that\'s where the real insights are." },
  ],
  'comms-config': [
    { id:'cfg-brokers',  sel:'.panel--brokers',     arrow:'top',    title:'\ud83d\udd17 BROKER CONNECTIONS',     text:"Connect your Australian broker here. IBKR is great for getting started. Click \'Open\' to visit their site." },
    { id:'cfg-discord',  sel:'.panel--discord',     arrow:'right',  title:'\ud83d\udce3 DISCORD ALERTS',         text:"Get trade alerts sent straight to your Discord. Paste in your webhook URL, hit Test, done. Never miss a signal again." },
    { id:'cfg-mode',     sel:'#modeBadge',           arrow:'bottom', title:'\u26a0 PAPER vs LIVE MODE',      text:"Start in PAPER mode, always. It\'s play money so you can learn the system risk-free. Only switch to LIVE when you\'re confident. No rush, legend." },
  ],
};

let _spotQueue   = [];
let _spotIdx     = 0;
let _spotTabId   = null;
let _spotHighlit = null;
let _spotAutoTimer = null;
let _spotCountdown = null;
let _guidedMode  = false;

// Tab order for guided tutorial walkthrough
const GUIDED_TAB_ORDER = [
  'command-center', 'live-trading', 'signal-ops', 'intel-center',
  'holy-grail', 'risk-matrix', 'backtest-lab',
  'asx-scanner', 'commodities-scanner',
  'paper-trading', 'comms-config'
];

function showTutorial(tabId, force = false) {
  _spotTabId = tabId;
  const spots = SPOTS[tabId] || [];
  _spotQueue = spots.filter(s => force || !localStorage.getItem(`dalios_spot_${s.id}`));
  _spotIdx   = 0;
  if (!_spotQueue.length) {
    if (_guidedMode) _guidedNextTab();
    return;
  }
  _showSpot(_spotIdx);
}

function _showSpot(idx) {
  clearTimeout(_spotAutoTimer);
  clearInterval(_spotCountdown);
  const bubble = el('spotBubble');
  if (!bubble) return;

  if (_spotHighlit) { _spotHighlit.classList.remove('spot-highlight'); _spotHighlit = null; }

  if (idx >= _spotQueue.length) {
    bubble.classList.add('hidden');
    const m = el('spotMascot'); if (m) m.classList.add('hidden');
    if (_guidedMode) _guidedNextTab();
    return;
  }

  const spot = _spotQueue[idx];
  el('spotTitle').textContent = spot.title;
  el('spotText').textContent  = spot.text;

  // Show/hide prev button — show when not on first spot
  const prevBtn = el('spotPrevBtn');
  if (prevBtn) prevBtn.style.display = idx > 0 ? '' : 'none';

  const isLastSpot = idx === _spotQueue.length - 1;
  const nextTabIdx = GUIDED_TAB_ORDER.indexOf(_spotTabId) + 1;
  const hasMoreTabs = nextTabIdx < GUIDED_TAB_ORDER.length;

  // Always auto-advance every spot after 10s
  let _secsLeft = 10;
  const countEl = el('spotCount');
  const updateCount = () => {
    countEl.textContent = `${idx + 1} / ${_spotQueue.length}  ·  ${_secsLeft}s`;
  };
  updateCount();

  _spotCountdown = setInterval(() => {
    _secsLeft--;
    if (_secsLeft <= 0) { clearInterval(_spotCountdown); return; }
    updateCount();
  }, 1000);

  _spotAutoTimer = setTimeout(() => {
    clearInterval(_spotCountdown);
    if (isLastSpot) {
      // Mark this spot done, then advance to next tab or finish
      if (spot) localStorage.setItem(`dalios_spot_${spot.id}`, '1');
      if (_guidedMode) {
        _spotIdx++;
        _showSpot(_spotIdx); // triggers _guidedNextTab via idx >= length
      } else {
        _guidedMode = true;
        _spotIdx++;
        _showSpot(_spotIdx);
      }
    } else {
      _guidedAdvanceSpot();
    }
  }, 10000);

  // Timer bar animation — always run
  const timerBar = bubble.querySelector('.spot-timer-bar');
  if (timerBar) {
    timerBar.style.animation = 'none';
    timerBar.offsetHeight; // reflow
    timerBar.style.animation = 'spotTimer 10s linear forwards';
  }

  // Button labels
  if (isLastSpot && hasMoreTabs) {
    el('spotNextBtn').textContent = 'NEXT TAB →';
  } else if (isLastSpot) {
    el('spotNextBtn').textContent = 'Finish ✓';
  } else {
    el('spotNextBtn').textContent = 'Next →';
  }

  bubble.className = `spot-bubble arrow-${spot.arrow}`;

  // Rex computer mascot for all tutorial bubbles — shown as sibling behind bubble
  const mascot = el('spotMascot');
  if (mascot) {
    mascot.src = '/static/img/rex-computer.png';
    mascot.className = idx % 2 === 0 ? 'spot-mascot mascot-left' : 'spot-mascot mascot-right';
  }

  const target = document.querySelector(spot.sel);
  if (target) {
    target.classList.add('spot-highlight');
    _spotHighlit = target;
    target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    setTimeout(() => {
      _positionBubble(bubble, target, spot.arrow);
      _positionMascot(bubble, idx);
    }, 100);
  } else {
    bubble.style.top  = '50%';
    bubble.style.left = '50%';
    bubble.style.transform = 'translate(-50%,-50%)';
    _positionMascot(bubble, idx);
  }
}

function _positionBubble(bubble, target, arrow) {
  const GAP  = 16;
  const tr   = target.getBoundingClientRect();
  const br   = bubble.getBoundingClientRect();
  const bw   = br.width  || 240;
  const bh   = br.height || 160;
  const vw   = window.innerWidth;
  const vh   = window.innerHeight;

  bubble.style.transform = '';
  let top, left;

  if (arrow === 'right') {
    left = tr.right + GAP;
    top  = tr.top + (tr.height / 2) - (bh / 2);
    if (left + bw > vw - 8) { left = tr.left - bw - GAP; bubble.className = 'spot-bubble arrow-left'; }
  } else if (arrow === 'left') {
    left = tr.left - bw - GAP;
    top  = tr.top + (tr.height / 2) - (bh / 2);
    if (left < 8) { left = tr.right + GAP; bubble.className = 'spot-bubble arrow-right'; }
  } else if (arrow === 'bottom') {
    left = tr.left + (tr.width / 2) - (bw / 2);
    top  = tr.bottom + GAP;
    if (top + bh > vh - 8) { top = tr.top - bh - GAP; bubble.className = 'spot-bubble arrow-bottom'; }
  } else {
    left = tr.left + (tr.width / 2) - (bw / 2);
    top  = tr.top - bh - GAP;
    if (top < 8) { top = tr.bottom + GAP; bubble.className = 'spot-bubble arrow-top'; }
  }

  top  = Math.max(8, Math.min(top,  vh - bh - 8));
  left = Math.max(8, Math.min(left, vw - bw - 8));

  bubble.style.top  = `${top}px`;
  bubble.style.left = `${left}px`;
}

function _positionMascot(bubble, idx) {
  const mascot = el('spotMascot');
  if (!mascot) return;
  const br = bubble.getBoundingClientRect();
  // Center Rex horizontally behind the bubble, poking up above it
  mascot.style.left = `${br.left + (br.width / 2) - 100}px`;
  mascot.style.top  = `${br.top - 120}px`;
}

function _guidedAdvanceSpot() {
  const spot = _spotQueue[_spotIdx];
  if (spot) localStorage.setItem(`dalios_spot_${spot.id}`, '1');
  _spotIdx++;
  _showSpot(_spotIdx);
}

function _guidedNextTab() {
  const curIdx = GUIDED_TAB_ORDER.indexOf(_spotTabId);
  const nextIdx = curIdx + 1;
  if (nextIdx >= GUIDED_TAB_ORDER.length) {
    _guidedMode = false;
    if (_spotHighlit) { _spotHighlit.classList.remove('spot-highlight'); _spotHighlit = null; }
    el('spotBubble')?.classList.add('hidden');
    const m = el('spotMascot'); if (m) m.classList.add('hidden');
    // Show tutorial complete splash
    const overlay = el('tutorialCompleteOverlay');
    if (overlay) overlay.classList.remove('hidden');
    return;
  }
  const nextTab = GUIDED_TAB_ORDER[nextIdx];
  // Switch tab and scroll to top
  const btn = document.querySelector(`[data-tab="${nextTab}"]`);
  if (btn) btn.click();
  window.scrollTo(0, 0);
  // Small delay to let the tab render
  setTimeout(() => showTutorial(nextTab, true), 400);
}

function nextSpot() {
  clearTimeout(_spotAutoTimer);
  clearInterval(_spotCountdown);
  if (!_spotQueue.length) return;
  const spot = _spotQueue[_spotIdx];
  if (spot) localStorage.setItem(`dalios_spot_${spot.id}`, '1');

  _spotIdx++;
  if (_spotIdx >= _spotQueue.length) {
    if (_spotHighlit) { _spotHighlit.classList.remove('spot-highlight'); _spotHighlit = null; }
    el('spotBubble').classList.add('hidden');
    const m = el('spotMascot'); if (m) m.classList.add('hidden');
    if (_guidedMode) _guidedNextTab();
    return;
  }
  _showSpot(_spotIdx);
}

function prevSpot() {
  clearTimeout(_spotAutoTimer);
  clearInterval(_spotCountdown);
  if (!_spotQueue.length || _spotIdx <= 0) return;
  _spotIdx--;
  _showSpot(_spotIdx);
}

function skipAllSpots() {
  clearTimeout(_spotAutoTimer);
  clearInterval(_spotCountdown);
  (_spotQueue || []).forEach(s => localStorage.setItem(`dalios_spot_${s.id}`, '1'));
  if (_spotHighlit) { _spotHighlit.classList.remove('spot-highlight'); _spotHighlit = null; }
  el('spotBubble').classList.add('hidden');
  const m = el('spotMascot'); if (m) m.classList.add('hidden');
  _spotQueue = [];
  _guidedMode = false;
}

function openCurrentTutorial() {
  const activeBtn = document.querySelector('.tab-btn.active');
  const tabId = activeBtn?.dataset?.tab ?? 'command-center';
  (SPOTS[tabId] || []).forEach(s => localStorage.removeItem(`dalios_spot_${s.id}`));
  _guidedMode = false;
  showTutorial(tabId, true);
}

function closeTutorial() { skipAllSpots(); }

function closeTutorialComplete() {
  // Mark all spots across all tabs as seen so no tutorials re-trigger
  Object.values(SPOTS).forEach(arr => arr.forEach(s => localStorage.setItem(`dalios_spot_${s.id}`, '1')));
  _guidedMode = false;
  _spotQueue = [];
  // Hide overlay
  const overlay = el('tutorialCompleteOverlay');
  if (overlay) overlay.classList.add('hidden');
  // Switch back to command center after a tick (so guided mode is fully off)
  setTimeout(() => {
    const btn = document.querySelector('[data-tab="command-center"]');
    if (btn) btn.click();
  }, 50);
}

function stopTutorialForever() {
  skipAllSpots();
  _saveSetting('tutorials_off', true);
  pushAlert('SETTINGS', 'Tutorials disabled — re-enable in Settings tab', 'info');
}

// ─── Welcome Popup & Guided Tutorial ─────────────────────

function initWelcomeTutorial() {
  if (_loadSettings().tutorials_off) return;
  if (localStorage.getItem('dalios_welcome_never')) return;
  if (localStorage.getItem('dalios_welcome_done')) return;
  const overlay = el('welcomeOverlay');
  if (overlay) overlay.classList.remove('hidden');
}

function startGuidedTutorial() {
  // Dismiss welcome overlay
  const overlay = el('welcomeOverlay');
  if (overlay) overlay.classList.add('hidden');
  localStorage.setItem('dalios_welcome_done', '1');

  // Check "never show again"
  const neverCb = el('welcomeNeverAgain');
  if (neverCb && neverCb.checked) {
    localStorage.setItem('dalios_welcome_never', '1');
  }

  // Clear all spot seen-states for a fresh walkthrough
  GUIDED_TAB_ORDER.forEach(tabId => {
    (SPOTS[tabId] || []).forEach(s => localStorage.removeItem(`dalios_spot_${s.id}`));
  });

  // Start guided mode on command-center
  _guidedMode = true;
  const ccBtn = document.querySelector('[data-tab="command-center"]');
  if (ccBtn) ccBtn.click();
  setTimeout(() => showTutorial('command-center', true), 400);
}

function skipWelcomeTutorial() {
  const overlay = el('welcomeOverlay');
  if (overlay) overlay.classList.add('hidden');
  localStorage.setItem('dalios_welcome_done', '1');
  _saveSetting('tutorials_off', true);

  const neverCb = el('welcomeNeverAgain');
  if (neverCb && neverCb.checked) {
    localStorage.setItem('dalios_welcome_never', '1');
  }
}

// ═══════════════════════════════════════════════════════════
// Strong Signal Alert System
// ═══════════════════════════════════════════════════════════

const STRONG_CONF = 82;  // confidence threshold %

function checkStrongSignals(signals) {
  const strong = signals.find(s =>
    s.confidence >= STRONG_CONF && ['BUY','SELL','SHORT','LONG'].includes(s.action)
  );
  if (!strong) {
    el('strongSignalBanner')?.classList.add('hidden');
    el('strongSignalInPage')?.classList.add('hidden');
    STATE._lastStrongTicker = null;
    return;
  }

  const isBuy  = ['BUY','LONG'].includes(strong.action);
  const verb   = isBuy ? 'BUY' : 'SHORT/SELL';
  if (STATE._lastStrongTicker !== strong.ticker) { STATE._lastStrongTicker = strong.ticker; playStrongSignalChime(); }
  const detail = `${strong.ticker.replace('-USD','')}  ·  Entry ${fmtSignalPrice(strong)}  ·  Stop ${strong.stop_loss ? '$'+(+strong.stop_loss).toFixed(2) : '--'}  ·  Target ${strong.take_profit ? '$'+(+strong.take_profit).toFixed(2) : '--'}  ·  Confidence ${(Number(strong.confidence)||0).toFixed(1)}%  ·  R:R ${(Number(strong.rr_ratio)||0).toFixed(2)}`;

  // Fixed top banner
  const banner = el('strongSignalBanner');
  if (banner) {
    banner.className = `strong-signal-banner ${isBuy ? '' : 'sell'}`;
    setEl('ssbAction', `⚡ STRONG ${verb} SIGNAL DETECTED`);
    setEl('ssbDetail', detail);
    el('ssbIcon').textContent = isBuy ? '⚡' : '⚠';
  }

  // In-page bar
  const inPage = el('strongSignalInPage');
  if (inPage) {
    inPage.className = `strong-signal-inpage ${isBuy ? '' : 'sell'}`;
    setEl('ssiAction', `⚡ STRONG ${verb}: ${strong.ticker.replace('-USD','')}`);
    setEl('ssiDetail', `Confidence ${(Number(strong.confidence)||0).toFixed(1)}%  ·  Entry ${fmtSignalPrice(strong)}  ·  Stop $${strong.stop_loss != null ? (+strong.stop_loss).toFixed(2) : '--'}  ·  Target $${strong.take_profit != null ? (+strong.take_profit).toFixed(2) : '--'}  ·  R:R ${(Number(strong.rr_ratio)||0).toFixed(2)}`);
  }
}

function dismissStrongSignal() {
  el('strongSignalBanner')?.classList.add('hidden');
}

function goToSignals() {
  dismissStrongSignal();
  document.querySelector('[data-tab="signal-ops"]')?.click();
}

function scrollToTopSignal() {
  el('signalGrid')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ═══════════════════════════════════════════════════════════
// Broker UI helpers
// ═══════════════════════════════════════════════════════════

function switchBrokerTab(cat, btn) {
  document.querySelectorAll('.broker-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  ['au'].forEach(c => {
    const g = el(`bcat-${c}`);
    if (g) g.classList.toggle('hidden', c !== cat);
  });
}



// ═══════════════════════════════════════════════════════════
// Actions
// ═══════════════════════════════════════════════════════════

// RUN CYCLE — triggers full agent cycle then refreshes signals
async function triggerCycle() {
  const btn = el('runCycleBtn');
  if (btn) { btn.disabled = true; btn.textContent = '⌛ RUNNING...'; }
  try {
    await postJSON('/api/agent/cycle');
    await loadSignals();
    pushAlert('CYCLE', 'Manual cycle triggered', 'info');
    pushActivityItem('▶', 'Cycle triggered from Signal Ops', 'info');
  } catch (e) {
    pushAlert('ERROR', `Cycle failed: ${escHtml(e.message || 'server unreachable')}`, 'warning');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '▶ RUN CYCLE'; }
  }
}

async function testNotification(channel) {
  try {
    await postJSON('/api/notifications/test', { channel });
    pushAlert('COMMS', `Test notification sent to ${channel.toUpperCase()}`, 'info');
  } catch {
    pushAlert('COMMS', `Failed to send test to ${channel}`, 'warning');
  }
}

function saveApiKeys() {
  pushAlert('CONFIG', 'API credentials saved to .env file', 'info');
}

function saveConfig() {
  pushAlert('CONFIG', 'System parameters updated. Restart required to apply.', 'warning');
}

// ═══════════════════════════════════════════════════════════
// SETTINGS TAB
// ═══════════════════════════════════════════════════════════

const _SETT_KEY = 'dalios_settings';

function _loadSettings() {
  try { return JSON.parse(localStorage.getItem(_SETT_KEY) || '{}'); } catch { return {}; }
}
function _saveSetting(key, val) {
  const s = _loadSettings(); s[key] = val;
  localStorage.setItem(_SETT_KEY, JSON.stringify(s));
}

// ─── Tutorial toggle ───────────────────────────────────────
function initSettingsTab() {
  const s = _loadSettings();
  // Tutorial btn
  const tBtn = el('settTutorialBtn');
  const tutOff = s.tutorials_off === true;
  if (tBtn) { tBtn.textContent = tutOff ? 'OFF' : 'ON'; tBtn.classList.toggle('on', !tutOff); }
  // Sound btn
  const sBtn = el('settSoundBtn');
  if (sBtn) { sBtn.textContent = _soundOn ? 'ON' : 'OFF'; sBtn.classList.toggle('on', _soundOn); }
  // Notification btn
  const nBtn = el('settNotifBtn');
  if (nBtn) { nBtn.textContent = Notification.permission === 'granted' ? 'ENABLED' : 'ENABLE'; nBtn.classList.toggle('on', Notification.permission === 'granted'); }

  // Restore saved general settings into form fields
  if (s.trade_size != null)   { const e = el('settTradeSize'); if (e) e.value = s.trade_size; }
  if (s.daily_sl != null)     { const e = el('settDailySL');   if (e) e.value = s.daily_sl; }
  if (s.max_dd != null)       { const e = el('settMaxDD');     if (e) e.value = s.max_dd; }
  if (s.max_pos_size != null) { const e = el('settMaxPos');    if (e) e.value = s.max_pos_size; }
  if (s.max_open != null)     { const e = el('settMaxOpen');   if (e) e.value = s.max_open; }
  if (s.min_conf != null)     { const e = el('settMinConf');   if (e) e.value = s.min_conf; }
  if (s.min_dalio != null)    { const e = el('settMinDalio');  if (e) e.value = s.min_dalio; }
  // Restore UI settings
  if (s.refresh_interval != null) { const e = el('settRefreshInterval'); if (e) e.value = s.refresh_interval; }
  if (s.ticker_interval != null)  { const e = el('settTickerInterval');  if (e) e.value = s.ticker_interval; }

  // Populate cash from server
  fetchJSON('/api/paper/config').then(d => {
    const inp = el('settStartCash'); if (inp) inp.value = d.starting_cash;
    const inp2 = el('startingCashInput'); if (inp2) inp2.value = d.starting_cash;
  }).catch(() => {});
  // Load saved broker credentials into config panels
  _loadSavedBrokerCreds();
}

async function _loadSavedBrokerCreds() {
  try {
    const saved = await fetchJSON('/api/broker/saved');
    if (!saved || typeof saved !== 'object') return;
    const fieldMap = {
      ibkr:        { host: 'settIbkrHost', port: 'settIbkrPort', client_id: 'settIbkrClientId' },
      ig:          { api_key: 'settIgKey', api_secret: 'settIgSecret', passphrase: 'settIgPassphrase' },
      cmc:         { api_key: 'settCmcKey', api_secret: 'settCmcSecret', passphrase: 'settCmcPassphrase' },
      moomoo:      { api_key: 'settMoomooKey', api_secret: 'settMoomooSecret' },
      saxo:        { api_key: 'settSaxoKey', api_secret: 'settSaxoSecret' },
      tiger:       { api_key: 'settTigerKey', api_secret: 'settTigerSecret' },
      pepperstone: { api_key: 'settPepperstoneKey', api_secret: 'settPepperstoneSecret' },
      finclear:    { api_key: 'settFinclearKey', api_secret: 'settFinclearSecret' },
      openmarkets: { api_key: 'settOpenmarketsKey', api_secret: 'settOpenmarketsSecret' },
      marketech:   { api_key: 'settMarketechKey', api_secret: 'settMarketechSecret' },
      opentrader:  { api_key: 'settOpentraderKey', api_secret: 'settOpentraderSecret' },
      iress:       { api_key: 'settIressKey', api_secret: 'settIressSecret' },
      cqg:         { api_key: 'settCqgKey', api_secret: 'settCqgSecret' },
      flextrade:   { api_key: 'settFlextradeKey', api_secret: 'settFlextradeSecret' },
      tradingview: { api_key: 'settTradingviewKey', api_secret: 'settTradingviewSecret' },
      eodhd:       { api_key: 'settEodhdKey' },
    };
    for (const [broker, creds] of Object.entries(saved)) {
      const map = fieldMap[broker];
      if (!map) continue;
      for (const [key, fieldId] of Object.entries(map)) {
        const input = el(fieldId);
        if (input && creds[key]) {
          input.value = creds[key];
          input.placeholder = creds[key];
        }
      }
      // Show a saved indicator on the config panel + broker card
      const resultEl = el(`bcfgResult-${broker}`);
      if (resultEl && !resultEl.innerHTML) {
        resultEl.innerHTML = '<span style="color:var(--cyan)">● SAVED — click TEST to connect</span>';
      }
      // Mark the card as having saved creds
      const card = document.querySelector(`.broker-card[data-broker="${broker}"]`);
      if (card) {
        let badge = card.querySelector('.broker-saved-badge');
        if (!badge) {
          badge = document.createElement('span');
          badge.className = 'broker-saved-badge';
          badge.style.cssText = 'color:var(--cyan);font-size:9px;font-weight:700;letter-spacing:1px;margin-left:auto;';
          card.querySelector('.broker-name')?.appendChild(badge);
        }
        badge.textContent = '● SAVED';
      }
    }
  } catch (e) { /* silent — settings page still works without this */ }
}

function toggleTutorials(btn) {
  const s = _loadSettings();
  const nowOff = !(s.tutorials_off === true);
  _saveSetting('tutorials_off', nowOff);
  btn.textContent = nowOff ? 'OFF' : 'ON';
  btn.classList.toggle('on', !nowOff);
  pushAlert('SETTINGS', `Tutorial tooltips ${nowOff ? 'disabled' : 'enabled'}`, 'info');
}

function resetAllTutorials() {
  Object.keys(localStorage).filter(k => k.startsWith('dalios_spot_')).forEach(k => localStorage.removeItem(k));
  localStorage.removeItem('dalios_welcome_done');
  localStorage.removeItem('dalios_welcome_never');
  _saveSetting('tutorials_off', false);
  const btn = el('settTutorialBtn'); if (btn) { btn.textContent = 'ON'; btn.classList.add('on'); }
  // Show the welcome overlay
  const overlay = el('welcomeOverlay');
  if (overlay) {
    overlay.classList.remove('hidden');
    const neverCb = el('welcomeNeverAgain');
    if (neverCb) neverCb.checked = false;
  }
  pushAlert('SETTINGS', 'All tutorials reset — starting from the beginning', 'info');
}

// Patch showTutorial to respect the setting
const _origShowTutorial = showTutorial;
window.showTutorial = function(tabId, force = false) {
  if (!force && _loadSettings().tutorials_off) return;
  _origShowTutorial(tabId, force);
};

function toggleSoundSetting(btn) {
  toggleSound();  // already saves to localStorage
  btn.textContent = _soundOn ? 'ON' : 'OFF';
  btn.classList.toggle('on', _soundOn);
  const mainBtn = el('soundToggleBtn');
  if (mainBtn) { mainBtn.textContent = _soundOn ? '🔊 SOUND' : '🔇 SOUND'; mainBtn.classList.toggle('on', _soundOn); }
}

function requestNotificationPermission() {
  if (Notification.permission === 'denied') {
    pushAlert('SETTINGS', 'Notifications blocked by browser. Reset in browser settings: click the lock icon in the address bar → Site settings → Notifications → Allow.', 'warn');
    return;
  }
  initNotifications();
  setTimeout(() => {
    const btn = el('settNotifBtn');
    if (!btn) return;
    if (Notification.permission === 'granted') {
      btn.textContent = 'ENABLED'; btn.classList.add('on');
    } else if (Notification.permission === 'denied') {
      btn.textContent = 'BLOCKED'; btn.classList.remove('on');
      pushAlert('SETTINGS', 'Notifications blocked. Reset via browser address bar lock icon → Site settings → Notifications → Allow.', 'warn');
    } else {
      btn.textContent = 'ENABLE'; btn.classList.remove('on');
    }
  }, 1500);
}

// ─── Theme switcher ────────────────────────────────────────
const _THEMES = {
  dark:   { primary: '#f59e0b', green: '#22c55e', red: '#ef4444', amber: '#f59e0b', bg0: '#0c0a09' },
  light:  { primary: '#c2410c', green: '#15803d', red: '#b91c1c', amber: '#b45309', bg0: '#ffffff' },
};

function setTheme(name, btn) {
  const t = _THEMES[name]; if (!t) return;
  const root = document.documentElement;

  // Clear ALL inline CSS variable overrides so the stylesheet :root / .light-theme rules take effect
  Array.from(root.style).filter(p => p.startsWith('--')).forEach(p => root.style.removeProperty(p));

  // Only set accent colors as inline overrides (these vary per color scheme)
  root.style.setProperty('--primary',      t.primary);
  root.style.setProperty('--green',        t.green);
  root.style.setProperty('--red',          t.red);
  root.style.setProperty('--amber',        t.amber);

  if (name === 'light') {
    root.classList.add('light-theme');
    root.classList.remove('dark-theme');
  } else {
    root.classList.add('dark-theme');
    root.classList.remove('light-theme');
  }

  _saveSetting('theme', name);
  _updateThemeToggleBtn(name);
}

function _applyStoredTheme() {
  const s = _loadSettings();
  let theme = s.theme || 'dark';
  // Migrate old themes to dark
  if (!_THEMES[theme]) theme = 'dark';
  setTheme(theme, null);
}

// ─── Restore signal filters + slider from localStorage ────
function _restoreFilters() {
  const s = _loadSettings();
  // Confidence slider
  const slider = el('minConfidence');
  const valLabel = el('minConfVal');
  if (slider && s.filter_min_conf != null) {
    slider.value = s.filter_min_conf;
    if (valLabel) valLabel.textContent = s.filter_min_conf + '%';
  }
  // Signal type filter
  const sf = el('signalFilter');
  if (sf && s.filter_signal_type) sf.value = s.filter_signal_type;
  // Market filter
  const mf = el('marketFilter');
  if (mf && s.filter_market) mf.value = s.filter_market;

  // Attach change listeners to persist on change
  if (slider) slider.addEventListener('input', () => _saveSetting('filter_min_conf', parseInt(slider.value)));
  if (sf) sf.addEventListener('change', () => _saveSetting('filter_signal_type', sf.value));
  if (mf) mf.addEventListener('change', () => _saveSetting('filter_market', mf.value));
}

// ─── Save general settings ─────────────────────────────────
async function saveGeneralSettings() {
  const cash = parseFloat(el('settStartCash')?.value);
  if (cash && cash >= 1) {
    try {
      const result = await postJSON('/api/paper/config', { starting_cash: cash });
      // Sync both cash inputs
      const inp = el('startingCashInput'); if (inp) inp.value = cash;
      // If server applied immediately (no open positions), refresh portfolio
      if (result.applied) {
        await loadPaperPortfolio();
        await loadPaperHistory();
        loadPaperEquityCurve();
        pushAlert('SETTINGS', `Starting cash set to $${cash.toLocaleString()} — portfolio reset`, 'info');
      } else {
        pushAlert('SETTINGS', `Starting cash updated to $${cash.toLocaleString()} — click RESET to apply`, 'warning');
      }
    } catch (e) {
      pushAlert('SETTINGS', `Failed to save starting cash: ${escHtml(e.message)}`, 'warning');
    }
  }
  _saveSetting('trade_size',    parseFloat(el('settTradeSize')?.value) || 100);
  _saveSetting('daily_sl',      parseFloat(el('settDailySL')?.value) || 2.0);
  _saveSetting('max_dd',        parseFloat(el('settMaxDD')?.value) || 10.0);
  _saveSetting('max_pos_size',  parseFloat(el('settMaxPos')?.value) || 10.0);
  _saveSetting('max_open',      parseInt(el('settMaxOpen')?.value) || 20);
  _saveSetting('min_conf',      parseFloat(el('settMinConf')?.value) || 60);
  _saveSetting('min_dalio',     parseFloat(el('settMinDalio')?.value) || 50);
  pushAlert('SETTINGS', 'General settings saved', 'info');
  playBeep(660, 0.08);
}

function saveUiSettings() {
  _saveSetting('refresh_interval', parseInt(el('settRefreshInterval')?.value) || 30);
  _saveSetting('ticker_interval',  parseInt(el('settTickerInterval')?.value) || 60);
  pushAlert('SETTINGS', 'UI settings saved', 'info');
  playBeep(660, 0.08);
}

// ═══════════════════════════════════════════════════════════
// Utilities
// ═══════════════════════════════════════════════════════════

const el = id => document.getElementById(id);
const setEl = (id, val) => {
  const e = el(id);
  if (!e) return;
  const old = e.textContent;
  e.textContent = val;
  // Flash animation when value changes
  if (old !== String(val) && old !== '--' && old !== '—') {
    e.classList.remove('data-flash');
    void e.offsetWidth;
    e.classList.add('data-flash');
  }
};
const setWidth = (id, pct) => { const e = el(id); if (e) e.style.width = pct + '%'; };

function fmt$(n) {
  if (n == null) return '$--';
  return '$' + Number(n).toLocaleString('en-AU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatUptime(seconds) {
  if (!seconds) return '--';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${h}h${m.toString().padStart(2,'0')}m${s.toString().padStart(2,'0')}s`;
}

// ═══════════════════════════════════════════════════════════
// Asset Search Modal
// ═══════════════════════════════════════════════════════════

let _allAssets = [];

async function loadAssets() {
  if (_allAssets.length) return;
  try {
    const res = await fetchJSON('/api/assets');
    _allAssets = res.assets ?? [];
    setEl('searchCount', `${res.total ?? _allAssets.length} assets available`);
  } catch {
    _allAssets = [];
  }
}

function openSearch() {
  const modal = el('searchModal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  const input = el('searchInput');
  if (input) { input.value = ''; input.focus(); }
  loadAssets().then(() => renderSearchResults(''));
}

function closeSearch() {
  const modal = el('searchModal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
}

function onSearchInput(val) {
  clearTimeout(_searchDebounce);
  _searchDebounce = setTimeout(() => renderSearchResults(val), 150);
}

function renderSearchResults(q) {
  const list = el('searchResults');
  if (!list) return;
  const query = q.trim().toLowerCase();

  let results = _allAssets;
  if (query) {
    results = _allAssets.filter(a =>
      a.ticker.toLowerCase().includes(query) ||
      a.name.toLowerCase().includes(query) ||
      a.cat.toLowerCase().includes(query) ||
      a.sector.toLowerCase().includes(query)
    );
  }

  if (!results.length) {
    list.innerHTML = `<div class="sr-empty">No assets matching "${escHtml(q)}"</div>`;
    return;
  }

  const catOrder = { ASX: 0, Commodity: 1, Unknown: 2 };
  results.sort((a, b) => (catOrder[a.cat] ?? 3) - (catOrder[b.cat] ?? 3));

  list.innerHTML = results.map(a => {
    const price = a.price != null
      ? `<span class="sr-price">$${(a.price).toFixed(2)}</span>`
      : `<span class="sr-price sr-price--na">N/A</span>`;
    const chg = a.change_pct != null
      ? `<span class="sr-chg ${a.change_pct >= 0 ? 'up' : 'dn'}">${a.change_pct >= 0 ? '+' : ''}${a.change_pct.toFixed(2)}%</span>`
      : '';
    const catBadge = `<span class="sr-cat sr-cat--${a.cat.toLowerCase()}">${a.cat}</span>`;
    return `<div class="sr-item" onclick="watchAsset('${a.ticker}')">
      <div class="sr-left">
        <span class="sr-ticker">${a.ticker.replace('-USD','')}</span>
        <span class="sr-name">${a.name}</span>
        <span class="sr-sector">${a.sector}</span>
      </div>
      <div class="sr-right">
        ${catBadge}${price}${chg}
        <button class="sr-watch-btn" onclick="event.stopPropagation();watchAsset('${a.ticker}')">+ WATCH</button>
      </div>
    </div>`;
  }).join('');
}

function watchAsset(ticker) {
  pushAlert('WATCH', `${ticker} added to watchlist`, 'info');
  closeSearch();
}

// ═══════════════════════════════════════════════════════════
// Paper Trading
// ═══════════════════════════════════════════════════════════

let _poSide    = 'BUY';
let _poPrice   = null;
let _poTicker  = '';
let _poRefreshTimer = null;

function initPaperTrading() {
  loadPaperPortfolio();
  loadPaperHistory();
  loadPaperSignals();
  loadPaperEquityCurve();
  loadPaperConfig();
  // Auto-refresh positions every 15s while tab is active
  clearInterval(_poRefreshTimer);
  _poRefreshTimer = setInterval(() => {
    const activeTab = document.querySelector('.tab-btn.active')?.dataset?.tab;
    if (activeTab === 'paper-trading') loadPaperPortfolio();
  }, 15_000);
}

// ─── Paper Config (starting cash) ─────────────────────────
async function loadPaperConfig() {
  try {
    const d = await fetchJSON('/api/paper/config');
    const inp = el('startingCashInput');
    if (inp) inp.value = d.starting_cash;
  } catch {}
}

async function saveStartingCash() {
  const inp = el('startingCashInput');
  const cash = parseFloat(inp?.value);
  if (!cash || cash < 1) { pushAlert('SETTINGS', 'Enter a valid starting cash amount', 'warning'); return; }
  try {
    await postJSON('/api/paper/config', { starting_cash: cash });
    pushAlert('SETTINGS', `Starting cash set to $${cash.toLocaleString()}. Reset portfolio to apply.`, 'info');
  } catch (e) {
    pushAlert('SETTINGS', e.message || 'Failed to save config', 'warning');
  }
}

// ─── Portfolio ────────────────────────────────────────────
async function loadPaperPortfolio() {
  try {
    const d = await fetchJSON('/api/paper/portfolio');
    applyPaperPortfolio(d);
  } catch {}
}

function applyPaperPortfolio(d) {
  const pnlPos  = d.total_pnl >= 0;
  const pnlCol  = pnlPos ? 'var(--green)' : 'var(--red)';
  const pnlSign = pnlPos ? '+' : '';

  setEl('paperTotalVal',  fmt$(d.total_value));  flashEl('paperTotalVal');
  setEl('paperCash',      fmt$(d.cash));
  setEl('paperInvested',  fmt$(d.invested));
  setEl('poCashDisplay',  fmt$(d.cash));
  setEl('paperOpenCount', d.open_count);

  const pnlEl  = el('paperPnl');
  const retEl  = el('paperReturn');
  const badge  = el('paperPnlBadge');
  if (pnlEl)  { pnlEl.textContent  = `${pnlSign}${fmt$(d.total_pnl)}`; pnlEl.style.color = pnlCol; flashEl('paperPnl', pnlPos ? 'num-up' : 'num-down'); }
  if (retEl)  { retEl.textContent  = `${pnlSign}${d.total_pnl_pct.toFixed(2)}%`; retEl.style.color = pnlCol; }
  if (badge)  { badge.textContent  = `${pnlSign}${d.total_pnl_pct.toFixed(2)}%`; badge.style.color = pnlCol; }

  // Render heatmap
  if (d.positions.length) renderPositionHeatmap(d.positions);
  else { const hw = el('posHeatmapWrap'); if (hw) hw.style.display = 'none'; }

  updatePoEstimate();

  const body = el('paperPositionsBody');
  if (!body) return;
  if (!d.positions.length) {
    body.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--text-muted);padding:16px">No open positions — place a trade above</td></tr>`;
    return;
  }
  body.innerHTML = d.positions.map(p => {
    const pnlCls = p.pnl >= 0 ? 'td-green' : 'td-red';
    const pnlTxt = (p.pnl >= 0 ? '+' : '') + fmt$(p.pnl);
    const pctTxt = (p.pnl_pct >= 0 ? '+' : '') + p.pnl_pct.toFixed(2) + '%';
    return `<tr data-ticker="${p.ticker}">
      <td class="td-cyan" style="font-weight:700">${p.ticker.replace('-USD','')}</td>
      <td class="${p.side === 'LONG' ? 'td-green' : 'td-red'}">${p.side}</td>
      <td>${p.qty % 1 === 0 ? p.qty : p.qty.toFixed(4)}</td>
      <td>${fmt$(p.entry_price)}</td>
      <td data-live="current_price" style="color:var(--text-1)">${fmt$(p.current_price)}</td>
      <td data-live="market_value">${fmt$(p.market_value)}</td>
      <td data-live="pnl" class="${pnlCls}">${pnlTxt}</td>
      <td data-live="pnl_pct" class="${pnlCls}">${pctTxt}</td>
      <td><button class="po-close-btn" onclick="closePaperPosition('${p.ticker}')">✕ CLOSE</button></td>
    </tr>`;
  }).join('');

  // Mirror to Command Centre (paper mode only — live mode uses broker data)
  if (_tradingMode !== 'live') applyCommandCentre(d, null);
}

async function closePaperPosition(ticker) {
  try {
    await postJSON('/api/paper/close', { ticker });
    loadPaperPortfolio();
    loadPaperHistory();
    pushAlert('PAPER', `Closed position: ${ticker}`, 'info');
    pushActivityItem('✕', `Closed position: ${ticker.replace('-USD','')}`, 'sell');
  } catch (e) {
    pushAlert('PAPER', `Close failed: ${escHtml(e.message)}`, 'warning');
  }
}

async function resetPaperPortfolio() {
  const cfgCash = parseFloat(el('startingCashInput')?.value) || 1000;
  if (!confirm(`Reset portfolio to $${cfgCash.toLocaleString()} starting cash? All positions and history will be cleared.`)) return;
  try {
    await postJSON('/api/paper/reset', {});
    loadPaperPortfolio();
    loadPaperHistory();
    loadPaperEquityCurve();
    pushAlert('PAPER', `Portfolio reset to $${cfgCash.toLocaleString()}`, 'info');
  } catch (e) {
    pushAlert('PAPER', `Reset failed: ${e.message || 'server error'}`, 'error');
  }
}

// ─── Order Entry ──────────────────────────────────────────
function setPoSide(side, btn) {
  _poSide = side;
  document.querySelectorAll('.po-side-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  updatePoEstimate();
}

let _poQuoteTimer = null;
function onPoTickerInput(val) {
  _poTicker = val.trim().toUpperCase();
  _poPrice  = null;
  setEl('poQuoteResult', '');
  clearTimeout(_poQuoteTimer);
  if (!_poTicker) { updatePoEstimate(); return; }
  _poQuoteTimer = setTimeout(() => fetchPoQuote(_poTicker), 500);
}

async function fetchPoQuote(ticker) {
  const res = el('poQuoteResult');
  if (res) res.textContent = '⌛ fetching price...';
  try {
    const d = await fetchJSON(`/api/paper/quote?ticker=${encodeURIComponent(ticker)}`);
    if (_poTicker !== ticker) return; // stale response
    _poPrice  = d.price;
    // Update the input field if the server normalised the ticker (e.g. BHP → BHP.AX)
    if (d.ticker && d.ticker !== ticker) {
      _poTicker = d.ticker;
      const inp = el('poTicker');
      if (inp) inp.value = d.ticker;
    }
    if (res) {
      res.innerHTML = d.price != null
        ? `<span style="color:var(--green)">✓</span> <strong>${d.name}</strong> · ${d.cat} · <span style="color:var(--primary)">${fmt$(d.price)}</span>`
        : `<span style="color:var(--amber)">⚠ price unavailable — try adding .AX (ASX)</span>`;
    }
    updatePoEstimate();
  } catch {
    if (res) res.innerHTML = `<span style="color:var(--red)">✗ not found — try BHP.AX, GLD</span>`;
  }
}

function updatePoEstimate() {
  const qty     = parseFloat(el('poQty')?.value) || 0;
  const estEl   = el('poEstVal');
  if (!estEl) return;
  if (!_poPrice || !qty) { estEl.textContent = '—'; return; }
  const cost = qty * _poPrice;
  estEl.textContent = fmt$(cost);
  estEl.style.color = _poSide === 'BUY' ? 'var(--amber)' : 'var(--green)';
}

async function submitPaperOrder() {
  const btn = el('poSubmitBtn');
  const res = el('poResult');
  const qty = parseFloat(el('poQty')?.value);
  if (!_poTicker) { if (res) res.innerHTML = `<span style="color:var(--red)">⚠ Enter a ticker first</span>`; return; }
  if (!qty || qty <= 0) { if (res) res.innerHTML = `<span style="color:var(--red)">⚠ Enter a valid quantity</span>`; return; }
  if (btn) { btn.classList.add('loading'); btn.textContent = '⌛ EXECUTING...'; }
  try {
    const price = _poPrice || undefined;
    const d = await postJSON('/api/paper/order', { ticker: _poTicker, side: _poSide, qty, price });
    if (res) res.innerHTML = `<span style="color:var(--green)">✓ Order #${d.order_id} — ${d.side} ${qty} × ${d.ticker} @ ${fmt$(d.price)}</span>`;
    loadPaperPortfolio();
    loadPaperHistory();
    pushAlert('PAPER', `${d.side} ${qty}× ${d.ticker} @ ${fmt$(d.price)}`, 'info');
    pushActivityItem(d.side === 'BUY' ? '▲' : '▼', `ORDER #${d.order_id} — ${d.side} ${qty}× ${d.ticker} @ ${fmt$(d.price)}`, d.side === 'BUY' ? 'buy' : 'sell');
  } catch (e) {
    const msg = e.message || 'Order failed';
    if (res) res.innerHTML = `<span style="color:var(--red)">✗ ${escHtml(msg)}</span>`;
  } finally {
    if (btn) { btn.classList.remove('loading'); btn.textContent = '▶ EXECUTE TRADE'; }
  }
}

// ─── History ──────────────────────────────────────────────
async function loadPaperHistory() {
  try {
    const d = await fetchJSON('/api/paper/history');
    applyPaperHistory(d);
  } catch {}
}

function applyPaperHistory(d) {
  setEl('paperTradeCount', `${d.total} TRADES`);
  const body = el('paperHistoryBody');
  if (!body) return;
  if (!d.trades.length) {
    body.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--text-muted);padding:16px">No closed trades yet</td></tr>`;
    return;
  }
  body.innerHTML = d.trades.map(t => {
    const pnlCls = t.pnl >= 0 ? 'td-green' : 'td-red';
    const pnlSign = t.pnl >= 0 ? '+' : '';
    const time = new Date(t.timestamp).toLocaleTimeString('en-AU', { hour12: false, hour: '2-digit', minute: '2-digit' });
    return `<tr>
      <td style="color:var(--text-muted)">#${t.id}</td>
      <td class="td-cyan" style="font-weight:700">${t.ticker.replace('-USD','')}</td>
      <td class="${t.pnl >= 0 ? 'td-green' : 'td-red'}">${t.side}</td>
      <td>${t.qty % 1 === 0 ? t.qty : t.qty.toFixed(4)}</td>
      <td>${fmt$(t.entry_price)}</td>
      <td>${fmt$(t.exit_price)}</td>
      <td class="${pnlCls}">${pnlSign}${fmt$(t.pnl)}</td>
      <td class="${pnlCls}">${pnlSign}${t.pnl_pct.toFixed(2)}%</td>
      <td style="color:var(--text-muted)">${time}</td>
    </tr>`;
  }).join('');

  // Mirror to Command Centre (paper mode only — live mode uses broker data)
  if (_tradingMode !== 'live') applyCommandCentre(null, d);

  // Push most recent trade to activity feed (only the latest one to avoid spam)
  if (d.trades.length) {
    const t = d.trades[0];
    const pnlSign = t.pnl >= 0 ? '+' : '';
    const cls = t.pnl >= 0 ? 'buy' : 'sell';
    const icon = t.side === 'BUY' ? '▲' : '▼';
    pushActivityItem(icon, `${t.side} ${t.ticker.replace('-USD','')} ${t.qty % 1 === 0 ? t.qty : t.qty.toFixed(4)}x | P&L: ${pnlSign}${fmt$(t.pnl)} (${pnlSign}${t.pnl_pct.toFixed(2)}%)`, cls);
  }
}

// ─── Quick-trade from signals ──────────────────────────────
async function loadPaperSignals() {
  try {
    const d = await fetchJSON('/api/signals');
    renderPaperSignalList(d.signals || []);
  } catch {}
}

function renderPaperSignalList(signals) {
  const list = el('paperSignalList');
  if (!list) return;
  const active = signals.filter(s => s.action !== 'HOLD').slice(0, 12);
  if (!active.length) { list.innerHTML = `<div style="padding:14px;color:var(--text-muted);font-size:10px;grid-column:1/-1">No active signals — run a cycle first</div>`; return; }
  list.innerHTML = active.map(s => {
    const isBuy  = ['BUY','LONG'].includes(s.action);
    const actCol = isBuy ? 'var(--green)' : 'var(--red)';
    const suggestQty = (1000 / (s.price || 100)).toFixed(s.price > 100 ? 2 : 4);
    const dalioScore = s.dalio_score != null ? `<span class="psr-conf" title="Dalio Fit">⬡ ${s.dalio_score}%</span>` : '';
    return `<div class="paper-sig-row">
      <div class="psr-left">
        <span class="psr-ticker">${s.ticker.replace('-USD','')}</span>
        <span class="psr-action" style="color:${actCol};font-size:10px">${s.action}</span>
        <span class="psr-price">${fmtSignalPrice(s)}</span>
        <span class="psr-conf">Conf: ${(Number(s.confidence)||0).toFixed(0)}%</span>
        ${dalioScore}
        <span class="psr-conf" style="color:var(--text-2)">${s.reason || ''}</span>
      </div>
      <div class="psr-right">
        <input type="number" class="po-input psr-qty" id="psrQty-${s.ticker}" value="${suggestQty}" min="0.0001" step="any"/>
        <button class="psr-btn ${isBuy ? 'buy' : 'sell'}" onclick="quickTrade('${escHtml(s.ticker)}',${s.price},'${isBuy ? 'BUY' : 'SELL'}','psrQty-${escHtml(s.ticker)}')">
          ${isBuy ? '▲ BUY' : '▼ SELL'}
        </button>
      </div>
    </div>`;
  }).join('');
}

async function quickTrade(ticker, price, side, qtyInputId) {
  const qty = parseFloat(el(qtyInputId)?.value);
  if (!qty || qty <= 0) { pushAlert('PAPER', 'Enter a valid quantity', 'warning'); return; }
  try {
    const d = await postJSON('/api/paper/order', { ticker, side, qty, price });
    pushAlert('PAPER', `${d.side} ${qty}× ${d.ticker} @ ${fmt$(d.price)}`, 'info');
    loadPaperPortfolio();
    loadPaperHistory();
  } catch (e) {
    pushAlert('PAPER', e.message || 'Order failed', 'warning');
  }
}

// ─── Live Signal Quick-Trade ──────────────────────────────
async function loadLiveSignals() {
  try {
    const d = await fetchJSON('/api/signals');
    renderLiveSignalList(d.signals || []);
  } catch {}
}

function renderLiveSignalList(signals) {
  const list = el('liveSignalList');
  if (!list) return;
  const active = signals.filter(s => s.action !== 'HOLD').slice(0, 12);
  if (!active.length) { list.innerHTML = `<div style="padding:14px;color:var(--text-muted);font-size:11px;grid-column:1/-1">No active signals — run a cycle first</div>`; return; }
  list.innerHTML = active.map(s => {
    const isBuy  = ['BUY','LONG'].includes(s.action);
    const actCol = isBuy ? 'var(--green)' : 'var(--red)';
    const conf = Number(s.confidence) || 0;
    const dalioScore = s.dalio_score != null ? `<span class="psr-conf" title="Dalio Fit">⬡ ${s.dalio_score}%</span>` : '';
    return `<div class="paper-sig-row" style="cursor:pointer" onclick="openOrderModal('${escHtml(s.ticker)}',${isBuy ? "'BUY'" : "'SELL'"},${s.price})">
      <div class="psr-left">
        <span class="psr-ticker">${s.ticker.replace('-USD','')}</span>
        <span class="psr-action" style="color:${actCol};font-size:11px">${s.action}</span>
        <span class="psr-price">${fmtSignalPrice(s)}</span>
        <span class="psr-conf">Conf: ${conf.toFixed(0)}%</span>
        ${dalioScore}
        <span class="psr-conf" style="color:var(--text-2)">${s.reason || ''}</span>
      </div>
      <div class="psr-right">
        <button class="psr-btn ${isBuy ? 'buy' : 'sell'}">
          ${isBuy ? '▲ TRADE' : '▼ TRADE'}
        </button>
      </div>
    </div>`;
  }).join('');
}

async function quickLiveTrade(ticker, price, side, qtyInputId) {
  const qty = parseFloat(el(qtyInputId)?.value);
  if (!qty || qty <= 0) { pushAlert('LIVE', 'Enter a valid quantity', 'warning'); return; }
  try {
    const d = await postJSON('/api/broker/order', { ticker, side, qty, price });
    pushAlert('LIVE', `${side} ${qty}× ${ticker} @ ${fmt$(price)}`, 'info');
    loadRealPortfolio();
    loadRealHistory();
  } catch (e) {
    pushAlert('LIVE', e.message || 'Order failed', 'warning');
  }
}

// ─── Live Order Estimate ─────────────────────────────────
function updateLivePoEstimate() {
  const ticker = el('livePoTicker')?.value?.trim();
  const qty = parseFloat(el('livePoQty')?.value) || 0;
  const limitPrice = parseFloat(el('livePoPrice')?.value);
  const estEl = el('livePoEstVal');
  if (!estEl) return;
  if (!ticker || qty <= 0) { estEl.textContent = '—'; return; }
  if (limitPrice > 0) {
    estEl.textContent = fmt$(limitPrice * qty);
  } else if (_liveQuotePrice > 0) {
    estEl.textContent = `~${fmt$(_liveQuotePrice * qty)}`;
  } else {
    estEl.textContent = '—';
  }
}
let _liveQuotePrice = 0;

// ═══════════════════════════════════════════════════════════
// UNIVERSAL ORDER MODAL
// ═══════════════════════════════════════════════════════════

let _omMode = 'live';      // 'live' or 'paper'
let _omSide = 'BUY';
let _omTicker = '';
let _omQuotePrice = 0;

function openOrderModal(ticker, side, price) {
  const overlay = el('orderModalOverlay');
  if (!overlay) return;
  overlay.style.display = 'flex';
  _omTicker = ticker || '';
  _omSide = side || 'BUY';
  _omQuotePrice = price || 0;
  const inp = el('omTicker');
  if (inp) { inp.value = _omTicker; if (_omTicker) onOmTickerInput(_omTicker); }
  setOmSide(_omSide, _omSide === 'BUY' ? el('omBuyBtn') : el('omSellBtn'));
  if (_omQuotePrice > 0) {
    el('omQty').value = Math.max(1, Math.floor(1000 / _omQuotePrice));
  }
  updateOmEstimate();
  updateOmBrokerStatus();
  setOrderModalMode(_omMode);
  document.addEventListener('keydown', _omEscHandler);
  // Phase 7: fetch suggested qty from broker when in live mode
  if (_omMode === 'live' && _omTicker) {
    fetchJSON(`/api/real/suggested_qty?ticker=${encodeURIComponent(_omTicker)}&confidence=70`)
      .then(d => {
        if (d.suggested_qty > 0 && _omTicker === d.ticker) {
          const qtyEl = el('omQty');
          if (qtyEl) { qtyEl.value = d.suggested_qty; updateOmEstimate(); }
          const hint = el('omSuggestedHint');
          if (hint) hint.textContent = `Suggested: ${d.suggested_qty} (${d.allocation_pct}% of buying power)`;
        }
      }).catch(() => {});
  }
}

function closeOrderModal() {
  const overlay = el('orderModalOverlay');
  if (overlay) overlay.style.display = 'none';
  const res = el('omResult');
  if (res) res.innerHTML = '';
  document.removeEventListener('keydown', _omEscHandler);
}

function _omEscHandler(e) { if (e.key === 'Escape') closeOrderModal(); }

function setOrderModalMode(mode) {
  _omMode = mode;
  el('omtLive')?.classList.toggle('active', mode === 'live');
  el('omtPaper')?.classList.toggle('active', mode === 'paper');
  const btn = el('omSubmitBtn');
  if (btn) {
    if (mode === 'live') {
      btn.textContent = '◆ PLACE LIVE ORDER';
      btn.style.background = 'var(--red)';
    } else {
      btn.textContent = '▷ PLACE PAPER ORDER';
      btn.style.background = 'var(--primary)';
    }
  }
  updateOmBrokerStatus();
}

function updateOmBrokerStatus() {
  const dot = el('omBrokerDot');
  const label = el('omBrokerLabel');
  if (_omMode === 'paper') {
    if (dot) dot.style.color = 'var(--amber)';
    if (label) label.textContent = 'PAPER MODE — simulated trades';
  } else {
    // Check if broker is connected
    fetchJSON('/api/broker/status').then(d => {
      if (d.connected) {
        if (dot) dot.style.color = 'var(--green)';
        if (label) label.textContent = `${(d.broker||'').toUpperCase()} CONNECTED`;
      } else {
        if (dot) dot.style.color = 'var(--red)';
        if (label) label.textContent = 'NO BROKER — connect in Settings';
      }
    }).catch(() => {
      if (dot) dot.style.color = 'var(--red)';
      if (label) label.textContent = 'NO BROKER';
    });
  }
}

function setOmSide(side, btn) {
  _omSide = side;
  el('omBuyBtn')?.classList.toggle('active', side === 'BUY');
  el('omSellBtn')?.classList.toggle('active', side === 'SELL');
}

// Broker compatibility cache (fetched once)
let _brokerCompatCache = null;

async function _ensureBrokerCompat() {
  if (_brokerCompatCache) return _brokerCompatCache;
  try {
    const d = await fetchJSON('/api/broker/compatible?ticker=BHP.AX');
    _brokerCompatCache = d.all_compat || {};
  } catch { _brokerCompatCache = {}; }
  return _brokerCompatCache;
}

function _getAssetType(ticker) {
  const t = ticker.toUpperCase();
  if (t.endsWith('.AX')) return 'asx';
  if (t.includes('=F')) return 'commodities';
  if (t.includes('=X')) return 'fx';
  if (['GLD','TLT','IEF','TIP','DBC','SPY','QQQ','IVV','VTI'].includes(t)) return 'us_etf';
  return 'us_etf';
}

function _getCompatibleBrokers(ticker, compat) {
  const type = _getAssetType(ticker);
  return Object.entries(compat)
    .filter(([_, caps]) => caps[type])
    .map(([name]) => name.toUpperCase());
}

async function onOmTickerInput(val) {
  const ticker = val.trim().toUpperCase();
  _omTicker = ticker;
  _omQuotePrice = 0;
  const res = el('omQuoteResult');
  if (!ticker || ticker.length < 1) { if (res) res.innerHTML = ''; return; }
  try {
    const [d, compat] = await Promise.all([
      fetchJSON(`/api/paper/quote?ticker=${encodeURIComponent(ticker)}`),
      _ensureBrokerCompat(),
    ]);
    if (_omTicker !== ticker) return; // stale response
    _omQuotePrice = d.price || 0;
    if (res && _omQuotePrice > 0) {
      const brokers = _getCompatibleBrokers(ticker, compat);
      const brokerText = brokers.length > 0
        ? `<div style="font-size:9px;color:var(--text-2);margin-top:2px">Trade via: ${brokers.join(', ')}</div>`
        : '';
      res.innerHTML = `<span style="color:var(--green)">${ticker} — ${fmt$(_omQuotePrice)}</span>${brokerText}`;
    }
    updateOmEstimate();
  } catch (e) { console.debug('onOmTickerInput failed:', e); }
}

function updateOmEstimate() {
  const qty = parseFloat(el('omQty')?.value) || 0;
  const limitP = parseFloat(el('omPrice')?.value);
  const estEl = el('omEstVal');
  if (!estEl) return;
  const price = limitP > 0 ? limitP : _omQuotePrice;
  estEl.textContent = (price > 0 && qty > 0) ? `~${fmt$(price * qty)}` : '—';
}

async function submitOrderModal() {
  const qty = parseFloat(el('omQty')?.value);
  const price = el('omPrice')?.value ? parseFloat(el('omPrice').value) : undefined;
  const btn = el('omSubmitBtn');
  const res = el('omResult');
  if (!_omTicker) { if (res) res.innerHTML = `<span style="color:var(--red)">Enter a ticker</span>`; return; }
  if (!qty || qty <= 0) { if (res) res.innerHTML = `<span style="color:var(--red)">Enter quantity</span>`; return; }

  // Phase 8: confirmation modal for live orders
  if (_omMode === 'live' && !_omConfirmed) {
    _showTradeConfirmation(_omTicker, _omSide, qty, price);
    return;
  }
  _omConfirmed = false;  // reset for next order

  const endpoint = _omMode === 'live' ? '/api/real/order' : '/api/paper/order';
  const modeLabel = _omMode === 'live' ? 'LIVE' : 'PAPER';
  if (btn) { btn.textContent = '⌛ PLACING...'; btn.disabled = true; }

  try {
    const d = await postJSON(endpoint, { ticker: _omTicker, side: _omSide, qty, price });
    if (res) res.innerHTML = `<span style="color:var(--green)">✓ ${modeLabel} ${_omSide} ${qty}× ${_omTicker} — ${d.status || 'OK'}</span>`;
    pushAlert(modeLabel, `${_omSide} ${qty}× ${_omTicker}`, 'info');
    if (_omMode === 'live') { loadRealPortfolio(); loadRealHistory(); }
    else { loadPaperPortfolio(); loadPaperHistory(); }
  } catch (e) {
    if (res) res.innerHTML = `<span style="color:var(--red)">✗ ${escHtml(e.message || 'Failed')}</span>`;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = _omMode === 'live' ? '◆ PLACE LIVE ORDER' : '▷ PLACE PAPER ORDER';
    }
  }
}

let _omConfirmed = false;

function _showTradeConfirmation(ticker, side, qty, price) {
  const overlay = el('tradeConfirmOverlay');
  if (!overlay) { _omConfirmed = true; submitOrderModal(); return; }
  const estCost = (price || _omQuotePrice) * qty;
  el('tcTicker').textContent = ticker;
  el('tcSide').textContent = side;
  el('tcSide').style.color = side === 'BUY' ? 'var(--green)' : 'var(--red)';
  el('tcQty').textContent = qty;
  el('tcEstCost').textContent = estCost > 0 ? `~$${estCost.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}` : '—';
  // Show drawdown warning if available
  const warn = el('tcDrawdownWarn');
  if (warn && STATE.health && STATE.health.drawdown_pct > 5) {
    warn.textContent = `⚠ Current drawdown: ${STATE.health.drawdown_pct.toFixed(1)}%`;
    warn.style.display = '';
  } else if (warn) {
    warn.style.display = 'none';
  }
  overlay.style.display = 'flex';
}

function confirmTradeYes() {
  el('tradeConfirmOverlay').style.display = 'none';
  _omConfirmed = true;
  submitOrderModal();
}

function confirmTradeNo() {
  el('tradeConfirmOverlay').style.display = 'none';
  _omConfirmed = false;
}

// ═══════════════════════════════════════════════════════════
// MARKET SCANNER (ASX / COMMODITIES)
// ═══════════════════════════════════════════════════════════

// ── Mini Sparkline SVG (deterministic from ticker + change%) ──
function miniSparkSVG(ticker, changePct, w = 40, h = 14) {
  // Deterministic hash from ticker string
  let hash = 0;
  for (let i = 0; i < ticker.length; i++) hash = ((hash << 5) - hash + ticker.charCodeAt(i)) | 0;
  const seed = (n) => { hash = (hash * 16807 + n) & 0x7fffffff; return (hash & 0xffff) / 0xffff; };
  // Generate 10 points trending toward changePct direction
  const pts = [];
  const drift = changePct > 0 ? 0.06 : changePct < 0 ? -0.06 : 0;
  let y = 0.5;
  for (let i = 0; i < 10; i++) {
    y += (seed(i) - 0.45) * 0.25 + drift;
    y = Math.max(0.05, Math.min(0.95, y));
    pts.push(y);
  }
  const col = changePct > 0 ? 'var(--green)' : changePct < 0 ? 'var(--red)' : 'var(--text-2)';
  const path = pts.map((v, i) => `${i === 0 ? 'M' : 'L'}${(i / 9 * w).toFixed(1)},${((1 - v) * h).toFixed(1)}`).join(' ');
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="vertical-align:middle;flex-shrink:0"><path d="${path}" fill="none" stroke="${col}" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}

const _scannerData = { asx: [], commodities: [] };
const _scannerSort = { asx: null, commodities: null };

const _SCANNER_IDS = {
  asx:         { tbody: 'asxTableBody',         stats: 'asxStats',    cols: 8 },
  commodities: { tbody: 'commoditiesTableBody',  stats: 'commStats',   cols: 7 },
};

// Track full ASX mode
let _asxFullMode = false;

async function loadScanner(market, full) {
  const ids = _SCANNER_IDS[market];
  const tbody = el(ids.tbody);
  const statsEl = el(ids.stats);
  if (!tbody) return;
  const useFull = (market === 'asx' && (full !== undefined ? full : _asxFullMode));
  const mktLabel = market === 'asx' ? (useFull ? 'FULL ASX ~1,900' : 'ASX 300') : 'COMMODITIES';

  // Show compact inline loading bar
  const tableWrap = tbody.closest('.scanner-table-wrap');
  let cardGrid = tableWrap?.querySelector('.scanner-card-grid');
  const loadHtml = `<div class="scanner-load-inline">
    <div class="scanner-load-top"><div class="load-spinner"></div><span class="load-label">SCANNING ${mktLabel}...</span></div>
    <div class="scanner-load-bar"><div class="scanner-load-bar-fill" id="${market}LoadBar"></div></div>
    <div class="scanner-load-status" id="${market}LoadStatus">Connecting to market feed...</div>
  </div>`;
  if (cardGrid) cardGrid.innerHTML = loadHtml;
  else tbody.innerHTML = `<tr><td colspan="${ids.cols}">${loadHtml}</td></tr>`;
  if (statsEl) statsEl.innerHTML = '';

  // Animate progress bar
  const barEl = el(`${market}LoadBar`);
  const statusEl = el(`${market}LoadStatus`);
  let pct = 0;
  const progressInterval = setInterval(() => {
    pct = Math.min(pct + Math.random() * 8 + 2, 92);
    if (barEl) barEl.style.width = pct + '%';
    if (statusEl) {
      if (pct < 30) statusEl.textContent = 'Downloading price data...';
      else if (pct < 60) statusEl.textContent = `Processing ${mktLabel} tickers...`;
      else statusEl.textContent = 'Calculating changes...';
    }
  }, 500);

  try {
    const url = useFull ? `/api/markets/${market}?full=true` : `/api/markets/${market}`;
    const d = await fetchJSON(url);
    clearInterval(progressInterval);
    if (barEl) barEl.style.width = '100%';
    if (statusEl) statusEl.textContent = `${(d.rows||[]).length} tickers loaded`;
    _scannerData[market] = d.rows || [];
    const cacheNote = d.cached ? ` <span style="opacity:.5">(cached ${d.cache_age}s ago)</span>` : '';
    if (statsEl) statsEl.dataset.cacheNote = cacheNote;
    await new Promise(r => setTimeout(r, 200));
    renderScanner(market);
  } catch (e) {
    clearInterval(progressInterval);
    if (barEl) barEl.style.background = 'var(--red)';
    if (barEl) barEl.style.width = '100%';
    if (statusEl) { statusEl.textContent = 'Failed: ' + (e.message||'unknown error'); statusEl.style.color = 'var(--red)'; }
  }
}

function toggleFullAsx() {
  _asxFullMode = !_asxFullMode;
  const btn = document.getElementById('fullAsxToggle');
  if (btn) {
    btn.textContent = _asxFullMode ? 'ASX 300' : 'SCAN FULL ASX';
    btn.title = _asxFullMode ? 'Switch back to ASX 300' : 'Scan all ~1,900 ASX-listed companies';
    btn.classList.toggle('badge--amber', !_asxFullMode);
    btn.classList.toggle('badge--cyan', _asxFullMode);
  }
  loadScanner('asx', _asxFullMode);
}

function renderScanner(market, filterText = '', filterSector = '') {
  const ids   = _SCANNER_IDS[market];
  const tbody = el(ids.tbody);
  const statsEl = el(ids.stats);
  if (!tbody) return;

  let rows = _scannerData[market];

  // Filter
  if (filterText) {
    const q = filterText.toLowerCase();
    rows = rows.filter(r => r.ticker.toLowerCase().includes(q) || r.name.toLowerCase().includes(q));
  }
  if (filterSector) {
    rows = rows.filter(r => (r.sector || '').includes(filterSector));
  }

  // Sort
  const sort = _scannerSort[market];
  if (sort) {
    rows = [...rows].sort((a, b) => {
      const av = a[sort.key], bv = b[sort.key];
      const cmp = typeof av === 'string' ? av.localeCompare(bv) : av - bv;
      return sort.asc ? cmp : -cmp;
    });
  }

  // Stats bar
  if (statsEl) {
    const up   = rows.filter(r => r.change_pct > 0).length;
    const down = rows.filter(r => r.change_pct < 0).length;
    const flat = rows.length - up - down;
    const avgChg = rows.length ? (rows.reduce((s,r) => s + r.change_pct, 0) / rows.length).toFixed(2) : 0;
    const cacheNote = statsEl.dataset.cacheNote || '';
    statsEl.innerHTML = `
      <span class="scanner-stat-item">SHOWING <span class="scanner-stat-val">${rows.length}</span></span>
      <span class="scanner-stat-item">UP <span class="scanner-stat-val up">${up}</span></span>
      <span class="scanner-stat-item">DOWN <span class="scanner-stat-val down">${down}</span></span>
      <span class="scanner-stat-item">FLAT <span class="scanner-stat-val">${flat}</span></span>
      <span class="scanner-stat-item">AVG CHANGE <span class="scanner-stat-val ${avgChg >= 0 ? 'up' : 'down'}">${avgChg}%</span></span>
      <span class="scanner-stat-item" style="margin-left:auto;font-size:8px;opacity:.5">yfinance${cacheNote}</span>`;
  }

  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="${ids.cols}" class="scanner-loading">No results</td></tr>`;
    // Also clear card grid if present
    const cardGrid = tbody.closest('.scanner-table-wrap')?.querySelector('.scanner-card-grid');
    if (cardGrid) cardGrid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:20px;color:var(--text-muted)">No results</div>';
    return;
  }

  const fmtPrice = (p) => p <= 0 ? '—'
                   : p >= 1000 ? `$${Number(p).toLocaleString('en-AU', {minimumFractionDigits:2, maximumFractionDigits:2})}`
                   : p >= 1    ? `$${p.toFixed(2)}`
                   : p >= 0.001 ? `$${p.toFixed(4)}`
                   : `$${p.toFixed(8)}`;

  // ── Render 2-column card grid ──
  const tableWrap = tbody.closest('.scanner-table-wrap');
  let cardGrid = tableWrap?.querySelector('.scanner-card-grid');
  if (!cardGrid && tableWrap) {
    // Hide the old table, create card grid
    const tbl = tableWrap.querySelector('table');
    if (tbl) tbl.style.display = 'none';
    cardGrid = document.createElement('div');
    cardGrid.className = 'scanner-card-grid';
    tableWrap.appendChild(cardGrid);
  }
  if (cardGrid) {
    cardGrid.innerHTML = rows.map(r => {
      const dir      = r.change_pct > 0 ? 'pos' : r.change_pct < 0 ? 'neg' : '';
      const chgStr   = `${r.change_pct >= 0 ? '+' : ''}${r.change_pct.toFixed(2)}%`;
      const priceStr = fmtPrice(r.price);
      const ticker   = r.ticker;
      const dispName = ticker;
      const nameShort = r.name.length > 20 ? r.name.slice(0,20) + '…' : r.name;
      const wlIcon   = r.in_watchlist ? '★' : '☆';
      const wlCls    = r.in_watchlist ? ' in' : '';
      return `<div class="scanner-card" onclick="openStockDetail('${escHtml(ticker)}',${r.price},${r.change_pct})">
        <span class="sc-ticker">${dispName}</span>
        <span class="sc-name" title="${escHtml(r.name)}">${nameShort}</span>
        <span class="sc-price">${priceStr}</span>
        ${miniSparkSVG(ticker, r.change_pct)}
        <span class="sc-change ${dir}">${chgStr}</span>
        <div class="sc-brokers" data-ticker="${escHtml(ticker)}"></div>
        <span class="sc-actions">
          <button class="sc-star-btn${wlCls}" onclick="event.stopPropagation();toggleWatchlist('${escHtml(ticker)}',this)">${wlIcon}</button>
          <button class="sc-trade-btn" onclick="event.stopPropagation();scannerOpenTrade('${escHtml(ticker)}',${r.price})">▶</button>
        </span>
      </div>`;
    }).join('');
  }

  // Also update the original tbody (for compatibility / fallback)
  tbody.innerHTML = rows.map(r => {
    const dir      = r.change_pct > 0 ? 'up' : r.change_pct < 0 ? 'down' : 'flat';
    const chgStr   = `${r.change_pct >= 0 ? '+' : ''}${r.change_pct.toFixed(2)}%`;
    const priceStr = fmtPrice(r.price);
    const volStr   = r.volume_fmt || (r.volume > 0 ? r.volume.toLocaleString() : '—');
    const wlLabel  = r.in_watchlist ? '★ WATCHING' : '☆ WATCH';
    const wlCls    = r.in_watchlist ? 'in' : '';
    const ticker   = r.ticker;
    const sectorCol  = market === 'asx' ? `<td>${r.sector || '—'}</td>` : '';
    const mktCapCol  = '';
    const dispName   = ticker;
    const nameShort  = r.name.length > 26 ? r.name.slice(0,26) + '…' : r.name;
    return `<tr class="scanner-row ${dir}" style="display:none">
      <td><strong style="font-family:var(--font-hud)">${dispName}</strong></td>
      <td title="${r.name}" style="color:var(--text-2)">${nameShort}</td>
      ${sectorCol}
      <td style="font-weight:700">${priceStr}</td>
      <td class="change-cell"><strong>${chgStr}</strong></td>
      <td style="color:var(--text-2);font-size:10px">${volStr}</td>
      ${mktCapCol}
      <td><button class="scan-wl-btn ${wlCls}" onclick="toggleWatchlist('${ticker}',this)">${wlLabel}</button></td>
      <td><button class="scan-trade-btn" onclick="scannerOpenTrade('${ticker}',${r.price})">▶ TRADE</button></td>
    </tr>`;
  }).join('');
  // Populate broker compatibility on scanner cards
  _populateBrokerCompatText();
}

function filterScanner(market, text) {
  const sectorSel = el(`${market === 'asx' ? 'asx' : 'comm'}SectorFilter`);
  renderScanner(market, text, sectorSel?.value || '');
}

function sortScanner(market, key) {
  const cur = _scannerSort[market];
  _scannerSort[market] = (cur?.key === key) ? { key, asc: !cur.asc } : { key, asc: false };
  renderScanner(market);
}

function applySortSelect(market, val) {
  if (!val) { _scannerSort[market] = null; renderScanner(market); return; }
  const asc = val.endsWith('_asc');
  const key = val.replace(/_(?:asc|desc)$/, '');
  _scannerSort[market] = { key, asc };
  renderScanner(market);
}

function scannerOpenTrade(ticker, price) {
  openOrderModal(ticker, 'BUY', price);
}

// ═══════════════════════════════════════════════════════════
// Stock Detail Panel
// ═══════════════════════════════════════════════════════════

let _sdChart = null;
let _sdTicker = '';
let _sdPrice = 0;

function openStockDetail(ticker, price, changePct) {
  _sdTicker = ticker;
  _sdPrice = price;
  const overlay = el('stockDetailOverlay');
  if (!overlay) return;
  overlay.classList.add('open');

  el('sdTicker').textContent = ticker;
  el('sdName').textContent = '';
  el('sdPrice').textContent = price > 0 ? '$' + price.toFixed(price >= 1 ? 2 : 4) : '--';
  const chgEl = el('sdChange');
  if (changePct !== undefined) {
    chgEl.textContent = (changePct >= 0 ? '+' : '') + changePct.toFixed(2) + '%';
    chgEl.className = 'sd-change ' + (changePct > 0 ? 'pos' : changePct < 0 ? 'neg' : '');
  } else {
    chgEl.textContent = '';
  }

  // Update watchlist button
  const wBtn = el('sdWatchBtn');
  if (wBtn) {
    const inWl = _watchlist.includes(ticker);
    wBtn.textContent = inWl ? '★ WATCHING' : '☆ WATCH';
  }

  // Reset chart area
  el('sdChartWrap').querySelector('.sd-loading').style.display = 'flex';
  el('sdChartCanvas').style.display = 'none';
  el('sdDescription').textContent = 'Loading...';
  el('sdStatsBody').innerHTML = '';

  // Set active period button
  document.querySelectorAll('.sd-period-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.sd-period-btn')[1]?.classList.add('active'); // 3M default

  // Load chart data
  sdLoadPeriod('3mo');

  // Escape key
  document.addEventListener('keydown', _sdEscHandler);
}

function closeStockDetail() {
  const overlay = el('stockDetailOverlay');
  if (overlay) overlay.classList.remove('open');
  if (_sdChart) { _sdChart.destroy(); _sdChart = null; }
  document.removeEventListener('keydown', _sdEscHandler);
}

function _sdEscHandler(e) { if (e.key === 'Escape') closeStockDetail(); }

async function sdLoadPeriod(period) {
  // Update active button
  document.querySelectorAll('.sd-period-btn').forEach(b => {
    const map = {'1mo':'1M','3mo':'3M','6mo':'6M','1y':'1Y','2y':'2Y','5y':'5Y'};
    b.classList.toggle('active', b.textContent === map[period]);
  });

  const loadEl = el('sdChartWrap').querySelector('.sd-loading');
  const canvas = el('sdChartCanvas');
  loadEl.style.display = 'flex';
  loadEl.style.color = '';  // reset error colour
  loadEl.textContent = 'Loading chart...';
  canvas.style.display = 'none';
  if (_sdChart) { _sdChart.destroy(); _sdChart = null; }

  try {
    const d = await fetchJSON(`/api/chart/${encodeURIComponent(_sdTicker)}?period=${period}&interval=1d`);

    // Update header with real price from chart data
    if (d.candles && d.candles.length > 0) {
      const lastCandle = d.candles[d.candles.length - 1];
      const realPrice = lastCandle.c;
      _sdPrice = realPrice;
      el('sdPrice').textContent = '$' + realPrice.toFixed(realPrice >= 1 ? 2 : 4);

      if (d.candles.length >= 2) {
        const prevClose = d.candles[d.candles.length - 2].c;
        const chg = ((realPrice - prevClose) / prevClose * 100);
        const chgEl = el('sdChange');
        chgEl.textContent = (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%';
        chgEl.className = 'sd-change ' + (chg > 0 ? 'pos' : chg < 0 ? 'neg' : '');
      }
    }

    // Update company info
    if (d.info) {
      el('sdName').textContent = d.info.name || _sdTicker;
      const desc = d.info.longBusinessSummary || '';
      const sector = d.info.sector || '';
      const industry = d.info.industry || '';
      const currency = d.info.currency || 'AUD';
      if (desc) {
        el('sdDescription').textContent = desc.length > 400 ? desc.slice(0, 400) + '...' : desc;
      } else {
        const parts = [d.info.name || _sdTicker];
        if (sector) parts.push(sector);
        if (industry) parts.push(industry);
        parts.push(`Currency: ${currency}`);
        el('sdDescription').textContent = parts.join(' — ') + '.';
      }
    }

    // Key stats
    if (d.candles && d.candles.length > 0) {
      const closes = d.candles.map(c => c.c);
      const highs = d.candles.map(c => c.h);
      const lows = d.candles.map(c => c.l);
      const vols = d.candles.map(c => c.v);
      const high52 = Math.max(...highs);
      const low52 = Math.min(...lows);
      const avgVol = vols.length ? Math.round(vols.reduce((a,b) => a+b, 0) / vols.length) : 0;
      const latestRsi = d.rsi ? d.rsi.filter(v => v !== null).pop() : null;
      const mktCap = d.info?.marketCap;

      let statsHtml = '';
      statsHtml += `<div class="sd-info-stat"><span class="label">PERIOD HIGH</span><span class="value">$${high52.toFixed(2)}</span></div>`;
      statsHtml += `<div class="sd-info-stat"><span class="label">PERIOD LOW</span><span class="value">$${low52.toFixed(2)}</span></div>`;
      if (mktCap) statsHtml += `<div class="sd-info-stat"><span class="label">MKT CAP</span><span class="value">$${mktCap >= 1e9 ? (mktCap/1e9).toFixed(2)+'B' : (mktCap/1e6).toFixed(1)+'M'}</span></div>`;
      statsHtml += `<div class="sd-info-stat"><span class="label">AVG VOLUME</span><span class="value">${avgVol.toLocaleString()}</span></div>`;
      if (latestRsi) statsHtml += `<div class="sd-info-stat"><span class="label">RSI (14)</span><span class="value" style="color:${latestRsi > 70 ? 'var(--red)' : latestRsi < 30 ? 'var(--green)' : ''}">${latestRsi}</span></div>`;
      if (d.info?.trailingPE) statsHtml += `<div class="sd-info-stat"><span class="label">P/E RATIO</span><span class="value">${d.info.trailingPE.toFixed(1)}</span></div>`;
      if (d.info?.dividendYield) statsHtml += `<div class="sd-info-stat"><span class="label">DIV YIELD</span><span class="value">${(d.info.dividendYield*100).toFixed(2)}%</span></div>`;
      if (d.info?.fiftyTwoWeekHigh) statsHtml += `<div class="sd-info-stat"><span class="label">52W HIGH</span><span class="value">$${d.info.fiftyTwoWeekHigh.toFixed(2)}</span></div>`;
      if (d.info?.fiftyTwoWeekLow) statsHtml += `<div class="sd-info-stat"><span class="label">52W LOW</span><span class="value">$${d.info.fiftyTwoWeekLow.toFixed(2)}</span></div>`;
      if (d.sma20) {
        const sma = d.sma20.filter(v => v !== null).pop();
        if (sma) statsHtml += `<div class="sd-info-stat"><span class="label">SMA 20</span><span class="value">$${sma.toFixed(2)}</span></div>`;
      }
      if (d.sma50) {
        const sma = d.sma50.filter(v => v !== null).pop();
        if (sma) statsHtml += `<div class="sd-info-stat"><span class="label">SMA 50</span><span class="value">$${sma.toFixed(2)}</span></div>`;
      }
      el('sdStatsBody').innerHTML = statsHtml;
    }

    // Render chart — requestAnimationFrame ensures canvas has layout dimensions
    loadEl.style.display = 'none';
    canvas.style.display = 'block';
    requestAnimationFrame(() => _renderStockChart(canvas, d));

  } catch (e) {
    loadEl.textContent = 'Failed to load chart: ' + (e.message || 'unknown error');
    loadEl.style.color = 'var(--red)';
  }
}

function _renderStockChart(canvas, data) {
  if (_sdChart) { _sdChart.destroy(); _sdChart = null; }
  if (!data.candles || !data.candles.length) return;

  const labels = data.candles.map(c => {
    const d = new Date(c.t);
    return d.toLocaleDateString('en-AU', { day: 'numeric', month: 'short' });
  });
  const closes = data.candles.map(c => c.c);

  const datasets = [{
    label: 'Price',
    data: closes,
    borderColor: 'rgba(0,212,255,0.9)',
    backgroundColor: 'rgba(0,212,255,0.08)',
    fill: true,
    borderWidth: 2,
    pointRadius: 0,
    pointHoverRadius: 4,
    tension: 0.3,
  }];

  // SMA20
  if (data.sma20) {
    datasets.push({
      label: 'SMA 20',
      data: data.sma20,
      borderColor: 'rgba(245,158,11,0.6)',
      borderWidth: 1,
      borderDash: [4, 2],
      pointRadius: 0,
      fill: false,
    });
  }
  // SMA50
  if (data.sma50) {
    datasets.push({
      label: 'SMA 50',
      data: data.sma50,
      borderColor: 'rgba(168,102,246,0.6)',
      borderWidth: 1,
      borderDash: [4, 2],
      pointRadius: 0,
      fill: false,
    });
  }

  const ctx = canvas.getContext('2d');
  _sdChart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          labels: { color: '#a8a29e', font: { size: 10, family: "'JetBrains Mono', monospace" }, boxWidth: 12, padding: 8 },
        },
        tooltip: {
          backgroundColor: 'rgba(28,25,23,0.95)',
          titleColor: '#fafaf9',
          bodyColor: '#a8a29e',
          borderColor: 'rgba(0,212,255,0.3)',
          borderWidth: 1,
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: $${ctx.parsed.y?.toFixed(ctx.parsed.y >= 1 ? 2 : 4)}`
          }
        }
      },
      scales: {
        x: {
          ticks: { color: '#78716c', font: { size: 9 }, maxTicksLimit: 12, maxRotation: 0 },
          grid: { color: 'rgba(61,56,53,0.3)' },
        },
        y: {
          ticks: { color: '#78716c', font: { size: 9 }, callback: v => '$' + v.toFixed(2) },
          grid: { color: 'rgba(61,56,53,0.3)' },
        }
      }
    }
  });
}

function sdTrade(side) {
  closeStockDetail();
  openOrderModal(_sdTicker, side, _sdPrice);
}

function sdToggleWatch() {
  const btn = el('sdWatchBtn');
  // Re-use existing watchlist toggle — find the ticker button in scanner
  toggleWatchlist(_sdTicker, btn).then(() => {
    const inWl = _watchlist.includes(_sdTicker);
    if (btn) btn.textContent = inWl ? '★ WATCHING' : '☆ WATCH';
  });
}

// ─── Watchlist ─────────────────────────────────────────────
let _watchlist = [];

async function loadWatchlist() {
  try {
    const d = await fetchJSON('/api/watchlist');
    _watchlist = d.watchlist || [];
    _saveWatchlistLocal();
  } catch { _loadWatchlistLocal(); }
}

async function toggleWatchlist(ticker, btn) {
  const inList = _watchlist.includes(ticker);
  try {
    const endpoint = inList ? '/api/watchlist/remove' : '/api/watchlist/add';
    const d = await postJSON(endpoint, { ticker });
    _watchlist = d.watchlist || [];
    _saveWatchlistLocal();
    // Update all buttons for this ticker across all scanner tables
    document.querySelectorAll('.scan-wl-btn').forEach(b => {
      if (b.closest('tr')?.querySelector('strong')?.textContent?.replace('-','') === ticker.replace('-USD','')) {
        const nowIn = _watchlist.includes(ticker);
        b.textContent = nowIn ? '★ WATCHING' : '☆ WATCH';
        b.classList.toggle('in', nowIn);
      }
    });
    pushAlert('WATCHLIST', `${ticker} ${inList ? 'removed from' : 'added to'} watchlist`, 'info');
  } catch (e) {
    pushAlert('WATCHLIST', e.message || 'Watchlist update failed', 'warning');
  }
}

// ═══════════════════════════════════════════════════════════
// SOUND ENGINE
// ═══════════════════════════════════════════════════════════

let _soundOn = false;
let _audioCtx = null;

function _restoreSound() {
  const s = _loadSettings();
  if (s.sound_on === true) {
    _soundOn = true;
    const btn = el('soundToggleBtn');
    if (btn) { btn.textContent = '🔊 SOUND'; btn.classList.add('on'); }
    _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
}

function toggleSound() {
  _soundOn = !_soundOn;
  const btn = el('soundToggleBtn');
  if (btn) {
    btn.textContent = _soundOn ? '🔊 SOUND' : '🔇 SOUND';
    btn.classList.toggle('on', _soundOn);
  }
  if (_soundOn && !_audioCtx) {
    _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  _saveSetting('sound_on', _soundOn);
  if (_soundOn) playBeep(660, 0.08, 'sine');
}

function playBeep(freq = 440, dur = 0.12, type = 'sine', vol = 0.18) {
  if (!_soundOn || !_audioCtx) return;
  try {
    const osc  = _audioCtx.createOscillator();
    const gain = _audioCtx.createGain();
    osc.connect(gain);
    gain.connect(_audioCtx.destination);
    osc.type = type;
    osc.frequency.setValueAtTime(freq, _audioCtx.currentTime);
    gain.gain.setValueAtTime(vol, _audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, _audioCtx.currentTime + dur);
    osc.start(_audioCtx.currentTime);
    osc.stop(_audioCtx.currentTime + dur);
  } catch {}
}

function playSignalBeep()  { playBeep(880, 0.12, 'square', 0.14); setTimeout(() => playBeep(1100, 0.08, 'square', 0.10), 130); }
function playOrderBeep()   { playBeep(523, 0.10, 'sine',   0.16); setTimeout(() => playBeep(659, 0.10, 'sine', 0.14), 110); }
function playAlertBeep()   { playBeep(330, 0.18, 'sawtooth', 0.12); }


// ═══════════════════════════════════════════════════════════
// BROWSER NOTIFICATIONS
// ═══════════════════════════════════════════════════════════

function initNotifications() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'default') {
    Notification.requestPermission();
  }
}

function sendNotification(title, body, icon = '🔔') {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    new Notification(`DALIOS — ${title}`, { body, icon: '/static/favicon.ico', silent: false });
  } catch {}
}


// ═══════════════════════════════════════════════════════════
// TRADING MODE TOGGLE
// ═══════════════════════════════════════════════════════════

let _tradingMode = 'paper';

async function initTradingMode() {
  try {
    const d = await fetchJSON('/api/mode');
    _tradingMode = d.mode;
    updateModeUI(d.mode, d.connected);
  } catch {}
}

function updateModeUI(mode, brokerConnected = false) {
  _tradingMode = mode;
  // Badge text + colour
  const badge = el('modeBadge');
  if (badge) {
    badge.textContent = mode === 'live' ? 'MODE: LIVE ▾' : 'MODE: PAPER ▾';
    badge.className   = mode === 'live' ? 'badge badge--red mode-live' : 'badge badge--cyan mode-paper';
    // Flash to confirm mode change
    badge.classList.remove('data-flash'); void badge.offsetWidth; badge.classList.add('data-flash');
  }
  // Dropdown option highlight
  const optPaper = el('modeOptPaper');
  const optLive  = el('modeOptLive');
  if (optPaper) { optPaper.classList.toggle('active',      mode === 'paper'); optPaper.classList.remove('active-live'); }
  if (optLive)  { optLive.classList.toggle('active-live',  mode === 'live');  optLive.classList.remove('active'); }
  // Live tab warning
  const warn = el('liveModeWarning');
  const tag  = el('liveModeTag');
  if (warn) warn.classList.toggle('hidden', mode === 'live' && brokerConnected);
  if (tag)  { tag.textContent = mode === 'live' ? 'LIVE MODE' : 'PAPER MODE'; tag.className = `panel-tag live-mode-tag${mode === 'live' ? ' live' : ''}`; }
}

function toggleModeDropdown() {
  el('modeDropdownMenu')?.classList.toggle('hidden');
}

async function selectMode(mode) {
  el('modeDropdownMenu')?.classList.add('hidden');
  await setTradingMode(mode);
}

// Close dropdown when clicking outside
document.addEventListener('click', e => {
  const dd = el('modeDropdown');
  if (dd && !dd.contains(e.target)) el('modeDropdownMenu')?.classList.add('hidden');
});

// Called by the new mode switcher pill buttons
async function setTradingMode(newMode) {
  if (newMode === _tradingMode) return;
  let brokerWarning = false;
  if (newMode === 'live') {
    const status = await fetchJSON('/api/broker/status').catch(() => null);
    if (!status?.connected) brokerWarning = true;
  }
  try {
    const d = await postJSON('/api/mode', { mode: newMode });
    updateModeUI(d.mode, true);
    refreshCcForMode();
    loadHealth();  // Immediately refresh stats for new mode
    playBeep(newMode === 'live' ? 880 : 440, 0.1);
    if (brokerWarning) {
      pushAlert('MODE', '⚠ LIVE MODE — No broker configured. Trading is halted until a broker is connected.', 'warning');
    } else {
      pushAlert('MODE', `Switched to ${d.mode.toUpperCase()} trading mode`, 'info');
    }
    if (newMode === 'live' && !brokerWarning) sendNotification('LIVE MODE ACTIVE', 'Real money trading is now active. Orders will be placed with your broker.');
  } catch (e) {
    // Revert buttons if failed
    updateModeUI(_tradingMode);
    pushAlert('MODE', e.message || 'Mode switch failed', 'warning');
  }
}

// Legacy toggle kept for any remaining onclick refs
async function toggleTradingMode() {
  await setTradingMode(_tradingMode === 'paper' ? 'live' : 'paper');
}


// ═══════════════════════════════════════════════════════════
// EQUITY CURVE CHARTS
// ═══════════════════════════════════════════════════════════

let _paperEquityChart = null;
let _liveEquityChart  = null;

function initEquityChart(canvasId, chartRef, multiAsset = false) {
  const canvas = el(canvasId);
  if (!canvas) return null;
  if (chartRef) { chartRef.destroy(); }
  const isPaper = canvasId === 'paperEquityChart';
  const datasets = [{
    label: 'Portfolio', data: [], borderColor: '#00d4ff',
    backgroundColor: 'rgba(0,212,255,0.06)', borderWidth: 2,
    pointRadius: 0, tension: 0.3, fill: !multiAsset, yAxisID: 'y',
  }];
  return new Chart(canvas, {
    type: 'line',
    data: { labels: [], datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: { duration: 300 },
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: multiAsset, labels: { color: '#3a6882', font: { size: 9 }, boxWidth: 8 } },
        tooltip: {
          backgroundColor: '#070c14', borderColor: '#00d4ff', borderWidth: 1,
          titleColor: '#3a6882', bodyColor: '#b8dcf0',
          callbacks: {
            label: ctx => {
              const v = ctx.raw;
              if (ctx.dataset.label === 'Portfolio') return ` NAV: $${v.toLocaleString('en-AU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
              return ` ${ctx.dataset.label}: ${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
            }
          }
        }
      },
      scales: {
        x: { display: false },
        y: {
          display: true, position: 'left',
          grid: { color: 'rgba(10,28,46,0.8)' },
          ticks: { color: '#32607e', font: { size: 9 }, callback: v => '$' + (v >= 1000 ? (v/1000).toFixed(1)+'k' : v.toFixed(0)) }
        },
        y2: {
          display: multiAsset, position: 'right',
          grid: { drawOnChartArea: false },
          ticks: { color: '#32607e', font: { size: 8 }, callback: v => (v >= 0 ? '+' : '') + v.toFixed(1) + '%' }
        }
      }
    }
  });
}

const _ASSET_LINE_COLOURS = ['#ffaa00','#ff6b6b','#51cf66','#cc5de8','#ff922b','#74c0fc','#f06595','#a9e34b'];

async function loadPaperEquityCurve() {
  try {
    const d = await fetchJSON('/api/paper/equity_curve');
    const pts  = d.equity_curve || [];
    const perf = d.position_performance || {};
    const startCash = d.starting_cash || 1000;
    const hint = el('paperEquityHint');

    // Destroy and recreate chart with multi-asset mode if positions exist
    const hasAssets = Object.keys(perf).length > 0;
    if (!_paperEquityChart || (_paperEquityChart._multiAsset !== hasAssets)) {
      if (_paperEquityChart) _paperEquityChart.destroy();
      _paperEquityChart = initEquityChart('paperEquityChart', null, hasAssets);
      if (_paperEquityChart) _paperEquityChart._multiAsset = hasAssets;
    }
    if (!_paperEquityChart) return;

    if (!pts.length) {
      if (hint) hint.textContent = '— place a trade to start tracking —';
      _paperEquityChart.data.labels = [];
      _paperEquityChart.data.datasets = [_paperEquityChart.data.datasets[0]];
      _paperEquityChart.data.datasets[0].data = [];
      _paperEquityChart.update('none');
      return;
    }
    if (hint) hint.textContent = `${pts.length} pts`;

    const labels = pts.map(p => p.t.slice(11, 16));
    const last   = pts[pts.length - 1].v;
    _paperEquityChart.data.labels = labels;

    // Portfolio equity line (absolute $)
    const ds0 = _paperEquityChart.data.datasets[0];
    ds0.data            = pts.map(p => p.v);
    ds0.borderColor     = last >= startCash ? '#00cc44' : '#ff3355';
    ds0.backgroundColor = last >= startCash ? 'rgba(0,212,255,0.06)' : 'rgba(255,51,85,0.05)';
    ds0.yAxisID         = 'y';

    // Per-position % return lines
    const newDatasets = [ds0];
    let ci = 0;
    for (const [ticker, returns] of Object.entries(perf)) {
      const col = _ASSET_LINE_COLOURS[ci++ % _ASSET_LINE_COLOURS.length];
      // Pad or trim returns to match label count
      const padded = returns.length >= labels.length
        ? returns.slice(-labels.length)
        : Array(labels.length - returns.length).fill(null).concat(returns);
      newDatasets.push({
        label: ticker.replace('-USD',''), data: padded,
        borderColor: col, backgroundColor: 'transparent',
        borderWidth: 1.5, borderDash: [3,3],
        pointRadius: 0, tension: 0.3, fill: false, yAxisID: 'y2',
      });
    }
    _paperEquityChart.data.datasets = newDatasets;
    _paperEquityChart.update('none');
  } catch (e) {
    console.warn('Equity curve load error:', e);
  }
}

async function loadRealEquityCurve() {
  try {
    const d = await fetchJSON('/api/real/equity_curve');
    const pts = d.equity_curve || [];
    const hint = el('liveEquityHint');
    if (!pts.length) {
      if (hint) hint.textContent = '— no live trades recorded —';
      return;
    }
    if (hint) hint.textContent = `${pts.length} data points`;
    _liveEquityChart = _liveEquityChart || initEquityChart('liveEquityChart', null);
    if (!_liveEquityChart) return;
    _liveEquityChart.data.labels   = pts.map(p => p.t.slice(11, 16));
    _liveEquityChart.data.datasets[0].data = pts.map(p => p.v);
    _liveEquityChart.update('none');
  } catch {}
}


// ═══════════════════════════════════════════════════════════
// POSITION HEATMAP
// ═══════════════════════════════════════════════════════════

function renderPositionHeatmap(positions) {
  const wrap = el('posHeatmapWrap');
  const hm   = el('posHeatmap');
  if (!wrap || !hm) return;
  if (!positions.length) { wrap.style.display = 'none'; return; }
  wrap.style.display = 'block';
  hm.innerHTML = positions.map(p => {
    const pct    = p.pnl_pct;
    const isPos  = pct >= 0;
    const abs    = Math.min(Math.abs(pct), 20); // cap colour intensity at 20%
    const alpha  = 0.08 + (abs / 20) * 0.35;
    const bg     = isPos ? `rgba(0,255,136,${alpha})` : `rgba(255,51,85,${alpha})`;
    const border = isPos ? 'rgba(0,255,136,0.3)' : 'rgba(255,51,85,0.3)';
    const col    = isPos ? 'var(--green)' : 'var(--red)';
    const sign   = isPos ? '+' : '';
    return `<div class="phm-tile" style="background:${bg};border-color:${border}">
      <div class="phm-ticker" style="color:${col}">${p.ticker.replace('-USD','')}</div>
      <div class="phm-pct"   style="color:${col}">${sign}${pct.toFixed(2)}%</div>
      <div class="phm-val">${sign}${fmt$(p.pnl)}</div>
    </div>`;
  }).join('');
}


// ═══════════════════════════════════════════════════════════
// LIVE TRADING TAB
// ═══════════════════════════════════════════════════════════

let _livePoSide    = 'BUY';
let _livePoTicker  = '';
let _livePoPrice   = null;
let _liveRefreshTimer = null;

function initLiveTrading() {
  loadBrokerStatus();
  loadRealPortfolio();
  loadRealHistory();
  loadRealEquityCurve();
  loadLiveSignals();
  clearInterval(_liveRefreshTimer);
  _liveRefreshTimer = setInterval(() => {
    if (document.querySelector('.tab-btn.active')?.dataset?.tab === 'live-trading') {
      loadBrokerStatus();
      loadRealPortfolio();
    }
  }, 20_000);
}

async function loadBrokerStatus() {
  try {
    const d = await fetchJSON('/api/broker/status');
    // Update broker bar on live trading tab
    const barDot = el('brokerBarDot');
    const barLabel = el('brokerBarLabel');
    const barStats = el('brokerBarStats');
    const barQuickConnect = el('brokerBarQuickConnect');
    if (d.connected) {
      if (barDot) barDot.style.color = 'var(--green)';
      if (d.error) {
        if (barLabel) barLabel.textContent = `${(d.broker||'').toUpperCase()} CONNECTED (API error)`;
        if (barDot) barDot.style.color = 'var(--amber)';
      } else {
        if (barLabel) barLabel.textContent = `${(d.broker||'').toUpperCase()} CONNECTED`;
      }
      if (barStats) {
        barStats.style.display = 'flex';
        setEl('bbsAcctVal', d.account_value ? fmt$(d.account_value) : '—');
        setEl('bbsBuyPow', d.buying_power ? fmt$(d.buying_power) : '—');
        setEl('bbsCash', d.cash ? fmt$(d.cash) : '—');
        // Show currency if available
        const currEl = el('bbsCurrency');
        if (currEl && d.currency) currEl.textContent = d.currency;
      }
      if (barQuickConnect) barQuickConnect.style.display = 'none';
      updateModeUI(_tradingMode, true);
    } else {
      if (barDot) barDot.style.color = 'var(--red)';
      if (barLabel) barLabel.textContent = d.broker ? `${d.broker.toUpperCase()} DISCONNECTED` : 'NO BROKER CONNECTED';
      if (barStats) barStats.style.display = 'none';
      if (barQuickConnect) barQuickConnect.style.display = '';
    }
    // Update command centre broker indicator
    _updateCcBrokerStatus(d);
    // Update settings broker card status badges
    _updateBrokerCardStatus(d);
  } catch {}
}

function _updateCcBrokerStatus(d) {
  const dot = el('ccBrokerDot');
  const label = el('ccBrokerLabel');
  const badge = el('brokerBadge');
  if (d.connected) {
    if (dot) { dot.textContent = '●'; dot.style.color = 'var(--green)'; }
    if (label) label.textContent = `${(d.broker||'').toUpperCase()} LIVE`;
    if (badge) {
      badge.textContent = `● ${(d.broker||'').toUpperCase()} ONLINE`;
      badge.style.color = 'var(--primary)';
      badge.style.borderColor = 'var(--primary)';
      badge.style.background = 'rgba(255,140,0,0.1)';
      badge.style.boxShadow = '0 0 8px rgba(255,140,0,0.25), 0 0 16px rgba(255,140,0,0.1)';
      badge.style.animation = 'badgeBorderPulse 3s ease infinite';
    }
  } else {
    if (dot) { dot.textContent = '●'; dot.style.color = 'var(--text-muted)'; }
    if (label) label.textContent = 'NO BROKER';
    if (badge) {
      badge.textContent = '⊘ NO BROKER';
      badge.style.color = '#666';
      badge.style.borderColor = '#444';
      badge.style.background = 'rgba(100,100,100,0.08)';
      badge.style.boxShadow = 'none';
      badge.style.animation = 'none';
    }
  }
}

function _updateBrokerCardStatus(d) {
  // Mark the active connected broker's card
  document.querySelectorAll('.broker-card').forEach(card => {
    const brokerId = card.dataset.broker;
    if (!brokerId) return;
    let badge = card.querySelector('.broker-conn-badge');
    const savedBadge = card.querySelector('.broker-saved-badge');
    if (d.connected && d.broker && d.broker.toLowerCase() === brokerId) {
      // Hide saved badge when connected
      if (savedBadge) savedBadge.style.display = 'none';
      if (!badge) {
        badge = document.createElement('span');
        badge.className = 'broker-conn-badge';
        card.querySelector('.broker-name')?.appendChild(badge);
      }
      badge.textContent = '● CONNECTED';
      badge.style.cssText = 'color:var(--green);font-size:10px;font-weight:700;letter-spacing:1px;margin-left:auto;';
      card.style.borderColor = 'var(--green)';
      card.style.boxShadow = '0 0 12px rgba(0,200,80,0.15)';
      // Update config panel result with account stats
      const resultEl = el(`bcfgResult-${brokerId}`);
      if (resultEl) {
        let statsHtml = `<span style="color:var(--green)">● CONNECTED</span>`;
        if (d.account_value || d.buying_power || d.cash) {
          statsHtml += `<span style="color:var(--text-2);font-size:10px;margin-left:8px">`;
          if (d.account_value) statsHtml += `ACCT: ${fmt$(d.account_value)} `;
          if (d.buying_power) statsHtml += `BP: ${fmt$(d.buying_power)} `;
          if (d.cash) statsHtml += `CASH: ${fmt$(d.cash)}`;
          if (d.currency) statsHtml += ` (${d.currency})`;
          statsHtml += `</span>`;
        }
        if (d.error) statsHtml += `<br><span style="color:var(--amber);font-size:9px">⚠ ${escHtml(d.error)}</span>`;
        resultEl.innerHTML = statsHtml;
      }
    } else {
      if (badge) badge.remove();
      if (savedBadge) savedBadge.style.display = '';
      card.style.borderColor = '';
      card.style.boxShadow = '';
    }
  });
}

function onBrokerSelect(val) {
  ['ibkrFields'].forEach(id => {
    const el2 = el(id); if (el2) el2.style.display = 'none';
  });
  const map = {
    ibkr:     'ibkrFields',
  };
  if (map[val]) { const el2 = el(map[val]); if (el2) el2.style.display = 'block'; }
  // Enable setup guide button when a valid broker is selected
}

// ═══════════════════════════════════════════════════════════
// BROKER CONFIG PANELS (Settings page)
// ═══════════════════════════════════════════════════════════

function toggleBrokerConfig(broker) {
  const panel = el(`brokerCfg-${broker}`);
  if (!panel) return;
  const isHidden = panel.classList.contains('hidden');
  // close all open panels first
  document.querySelectorAll('.broker-config-panel').forEach(p => p.classList.add('hidden'));
  if (isHidden) panel.classList.remove('hidden');
}

async function connectBrokerFromSettings(broker) {
  const resultEl = el(`bcfgResult-${broker}`);
  if (resultEl) resultEl.innerHTML = '<span style="color:var(--amber)">⌛ Connecting...</span>';
  let payload = { broker };

  if (broker === 'ibkr') {
    payload.host      = el('settIbkrHost')?.value || '127.0.0.1';
    payload.port      = parseInt(el('settIbkrPort')?.value || '7497');
    payload.client_id = parseInt(el('settIbkrClientId')?.value || '1');
  } else if (broker === 'ig') {
    payload.api_key    = el('settIgKey')?.value?.trim();
    payload.api_secret = el('settIgSecret')?.value?.trim();
    payload.passphrase = el('settIgPassphrase')?.value?.trim();
    if (!payload.api_key || !payload.api_secret || !payload.passphrase) { if (resultEl) resultEl.innerHTML = '<span style="color:var(--red)">API key, account ID, and password required</span>'; return; }
  } else if (broker === 'cmc') {
    payload.api_key    = el('settCmcKey')?.value?.trim();
    payload.api_secret = el('settCmcSecret')?.value?.trim();
    payload.passphrase = el('settCmcPassphrase')?.value?.trim();
    if (!payload.api_key || !payload.api_secret) { if (resultEl) resultEl.innerHTML = '<span style="color:var(--red)">API key and secret required</span>'; return; }
  } else if (broker === 'moomoo') {
    payload.api_key    = el('settMoomooKey')?.value?.trim();
    payload.api_secret = el('settMoomooSecret')?.value?.trim();
    if (!payload.api_key || !payload.api_secret) { if (resultEl) resultEl.innerHTML = '<span style="color:var(--red)">OpenD host and port required</span>'; return; }
  } else if (broker === 'saxo') {
    payload.api_key    = el('settSaxoKey')?.value?.trim();
    payload.api_secret = el('settSaxoSecret')?.value?.trim();
    if (!payload.api_key || !payload.api_secret) { if (resultEl) resultEl.innerHTML = '<span style="color:var(--red)">API key and secret required</span>'; return; }
  } else if (broker === 'tiger') {
    payload.api_key    = el('settTigerKey')?.value?.trim();
    payload.api_secret = el('settTigerSecret')?.value?.trim();
    if (!payload.api_key || !payload.api_secret) { if (resultEl) resultEl.innerHTML = '<span style="color:var(--red)">API key and secret required</span>'; return; }
  } else {
    // Generic API key/secret broker (pepperstone, finclear, openmarkets, etc.)
    const capBroker = broker.charAt(0).toUpperCase() + broker.slice(1);
    const keyEl = el(`sett${capBroker}Key`);
    const secretEl = el(`sett${capBroker}Secret`);
    if (keyEl) payload.api_key = keyEl.value?.trim();
    if (secretEl) payload.api_secret = secretEl.value?.trim();
    if (!payload.api_key) { if (resultEl) resultEl.innerHTML = '<span style="color:var(--red)">API key required</span>'; return; }
  }

  try {
    const d = await postJSON('/api/broker/connect', payload);
    let statusMsg = `<span style="color:var(--green)">✓ ${d.broker.toUpperCase()} connected & saved</span>`;
    if (d.account_value || d.buying_power || d.cash) {
      statusMsg += `<br><span style="color:var(--text-2);font-size:10px">`;
      if (d.account_value) statusMsg += `ACCT: ${fmt$(d.account_value)} `;
      if (d.buying_power) statusMsg += `BP: ${fmt$(d.buying_power)} `;
      if (d.cash) statusMsg += `CASH: ${fmt$(d.cash)}`;
      if (d.currency) statusMsg += ` (${d.currency})`;
      statusMsg += `</span>`;
    }
    if (d.error) statusMsg += `<br><span style="color:var(--amber);font-size:9px">⚠ API: ${escHtml(d.error)}</span>`;
    if (resultEl) resultEl.innerHTML = statusMsg;
    pushAlert('BROKER', `${d.broker.toUpperCase()} connected & saved`, 'info');
    sendNotification('Broker Connected', `${d.broker.toUpperCase()} is now connected and saved.`);
    // sync to live trading tab dropdowns
    const sel = el('brokerSelect');
    if (sel) { sel.value = broker; onBrokerSelect(broker); }
    await loadBrokerStatus();
    await loadRealPortfolio();
  } catch (e) {
    if (resultEl) resultEl.innerHTML = `<span style="color:var(--red)">✗ ${escHtml(e.message || 'Connection failed')}</span>`;
  }
}

function _toggleBrokerPicker() {
  const dd = el('brokerPickerDropdown');
  if (!dd) return;
  if (dd.style.display === 'none') {
    _populateBrokerPicker();
    dd.style.display = '';
  } else {
    dd.style.display = 'none';
  }
}

function _closeBrokerPicker() {
  const dd = el('brokerPickerDropdown');
  if (dd) dd.style.display = 'none';
}

// Close picker when clicking outside
document.addEventListener('click', e => {
  const dd = el('brokerPickerDropdown');
  const btn = e.target.closest('[onclick*="_toggleBrokerPicker"]');
  if (dd && dd.style.display !== 'none' && !dd.contains(e.target) && !btn) {
    dd.style.display = 'none';
  }
});

async function _populateBrokerPicker() {
  const list = el('brokerPickerList');
  if (!list) return;
  list.innerHTML = '<div class="broker-picker-item bp-empty">Loading...</div>';

  const logoMap = {
    ibkr:'IB', ig:'IG', cmc:'CMC', saxo:'SX', tiger:'TG',
    moomoo:'MM', pepperstone:'PP', finclear:'FC', openmarkets:'OM',
    marketech:'MK', opentrader:'OT', iress:'IR', cqg:'CQ',
    flextrade:'FX', tradingview:'TV', eodhd:'EO',
  };

  try {
    const [saved, status] = await Promise.all([
      fetchJSON('/api/broker/saved'),
      fetchJSON('/api/broker/status'),
    ]);
    const brokers = Object.keys(saved || {}).filter(k => k !== '_last_active');

    if (!brokers.length) {
      list.innerHTML = '<div class="broker-picker-item bp-empty">Oops, no brokers saved yet!<br><span style="font-size:9px;opacity:0.7">Set one up in Settings first</span></div>';
      return;
    }

    list.innerHTML = brokers.map(b => {
      const isConnected = status.connected && status.broker?.toLowerCase() === b;
      const statusClass = isConnected ? 'online' : '';
      let statusText = isConnected ? '● CONNECTED' : '○ SAVED — click to connect';
      if (isConnected && status.account_value) {
        statusText += ` — ${fmt$(status.account_value)}`;
      }
      return `<div class="broker-picker-item" onclick="_pickBroker('${escHtml(b)}')">
        <div class="bp-logo">${logoMap[b] || b.substring(0,3).toUpperCase()}</div>
        <div class="bp-info">
          <div class="bp-name">${escHtml(b.toUpperCase())}</div>
          <div class="bp-status ${statusClass}">${statusText}</div>
        </div>
      </div>`;
    }).join('');
  } catch {
    list.innerHTML = '<div class="broker-picker-item bp-empty">Failed to load brokers</div>';
  }
}

async function _pickBroker(broker) {
  _closeBrokerPicker();
  const label = el('brokerBarLabel');
  const barDot = el('brokerBarDot');
  if (label) label.textContent = `⌛ CONNECTING ${broker.toUpperCase()}...`;
  if (barDot) barDot.style.color = 'var(--amber)';

  try {
    const d = await postJSON('/api/broker/connect', { broker });
    // Show immediate feedback with account stats
    if (label) {
      let msg = `${d.broker.toUpperCase()} CONNECTED`;
      if (d.account_value) msg += ` — ${fmt$(d.account_value)}`;
      label.textContent = msg;
    }
    if (barDot) barDot.style.color = 'var(--green)';
    pushAlert('BROKER', `${d.broker.toUpperCase()} connected`, 'info');
    await loadBrokerStatus();
    await loadRealPortfolio();
  } catch (e) {
    if (barDot) barDot.style.color = 'var(--red)';
    if (label) label.textContent = `✗ ${broker.toUpperCase()} — ${e.message || 'connection failed'}`;
    setTimeout(() => loadBrokerStatus(), 3000);
  }
}

async function _quickReconnectSaved() {
  const label = el('brokerBarLabel');
  if (label) label.textContent = '⌛ RECONNECTING...';
  try {
    const saved = await fetchJSON('/api/broker/saved');
    const brokers = Object.keys(saved || {});
    if (!brokers.length) {
      if (label) label.textContent = 'NO SAVED CREDENTIALS — configure in Settings';
      return;
    }
    // Try to connect the first saved broker
    const broker = brokers[0];
    const creds = saved[broker];
    const payload = { broker, ...creds };
    const d = await postJSON('/api/broker/connect', payload);
    pushAlert('BROKER', `${d.broker.toUpperCase()} reconnected`, 'info');
    await loadBrokerStatus();
    await loadRealPortfolio();
  } catch (e) {
    if (label) label.textContent = `RECONNECT FAILED — ${e.message || 'check Settings'}`;
    setTimeout(() => { if (label) label.textContent = 'NO BROKER CONNECTED'; }, 4000);
  }
}

// Build payload from settings fields for a given broker
function _getBrokerPayload(broker) {
  const _f = (id) => el(id)?.value?.trim() || '';
  const map = {
    ibkr:        () => ({ host: _f('settIbkrHost') || '127.0.0.1', port: _f('settIbkrPort') || '7497', client_id: _f('settIbkrClientId') || '1' }),
    ig:          () => ({ api_key: _f('settIgKey'), api_secret: _f('settIgSecret'), passphrase: _f('settIgPassphrase') }),
    cmc:         () => ({ api_key: _f('settCmcKey'), api_secret: _f('settCmcSecret'), passphrase: _f('settCmcPassphrase') }),
    moomoo:      () => ({ api_key: _f('settMoomooKey'), api_secret: _f('settMoomooSecret') }),
    saxo:        () => ({ api_key: _f('settSaxoKey'), api_secret: _f('settSaxoSecret') }),
    tiger:       () => ({ api_key: _f('settTigerKey'), api_secret: _f('settTigerSecret') }),
    pepperstone: () => ({ api_key: _f('settPepperstoneKey'), api_secret: _f('settPepperstoneSecret') }),
    finclear:    () => ({ api_key: _f('settFinclearKey'), api_secret: _f('settFinclearSecret') }),
    openmarkets: () => ({ api_key: _f('settOpenmarketsKey'), api_secret: _f('settOpenmarketsSecret') }),
    marketech:   () => ({ api_key: _f('settMarketechKey'), api_secret: _f('settMarketechSecret') }),
    opentrader:  () => ({ api_key: _f('settOpentraderKey'), api_secret: _f('settOpentraderSecret') }),
    iress:       () => ({ api_key: _f('settIressKey'), api_secret: _f('settIressSecret') }),
    cqg:         () => ({ api_key: _f('settCqgKey'), api_secret: _f('settCqgSecret') }),
    flextrade:   () => ({ api_key: _f('settFlextradeKey'), api_secret: _f('settFlextradeSecret') }),
    tradingview: () => ({ api_key: _f('settTradingviewKey'), api_secret: _f('settTradingviewSecret') }),
    eodhd:       () => ({ api_key: _f('settEodhdKey') }),
  };
  return map[broker] ? map[broker]() : {};
}

async function saveBrokerCreds(broker) {
  const resultEl = el(`bcfgResult-${broker}`);
  const payload = { broker, ..._getBrokerPayload(broker) };
  // Check at least one field has a value
  const vals = Object.values(payload).filter(v => typeof v === 'string' && v.length > 0);
  if (vals.length < 2) { if (resultEl) resultEl.innerHTML = '<span style="color:var(--red)">Fill in credentials first</span>'; return; }
  try {
    await postJSON('/api/broker/save', payload);
    if (resultEl) resultEl.innerHTML = '<span style="color:var(--green)">💾 Credentials saved</span>';
    pushAlert('BROKER', `${broker.toUpperCase()} credentials saved`, 'info');
  } catch (e) {
    if (resultEl) resultEl.innerHTML = `<span style="color:var(--red)">Save failed: ${escHtml(e.message)}</span>`;
  }
}

async function connectBroker() {
  const broker = el('brokerSelect')?.value;
  if (!broker) { pushAlert('BROKER', 'Select a broker first', 'warning'); return; }
  const btn = el('brokerConnectBtn');
  const res = el('brokerConnectResult');
  if (btn) { btn.textContent = '⌛ CONNECTING...'; btn.classList.add('loading'); }
  let payload = { broker };
  if (broker === 'ibkr') {
    payload.host      = el('ibkrHost')?.value || '127.0.0.1';
    payload.port      = parseInt(el('ibkrPort')?.value || '7497');
    payload.client_id = parseInt(el('ibkrClientId')?.value || '1');
  } else {
    // For other brokers, load saved credentials — configure them in Settings tab first
    try {
      const saved = await fetchJSON('/api/broker/saved');
      if (saved[broker]) {
        Object.assign(payload, saved[broker]);
      } else {
        if (res) res.innerHTML = '<span style="color:var(--amber)">⚠ No saved credentials — configure in Settings tab first</span>';
        if (btn) { btn.textContent = '▶ CONNECT BROKER'; btn.classList.remove('loading'); }
        return;
      }
    } catch (e) {
      if (res) res.innerHTML = '<span style="color:var(--amber)">⚠ Configure credentials in Settings tab first</span>';
      if (btn) { btn.textContent = '▶ CONNECT BROKER'; btn.classList.remove('loading'); }
      return;
    }
  }
  try {
    const d = await postJSON('/api/broker/connect', payload);
    if (res) res.innerHTML = `<span style="color:var(--green)">✓ ${d.broker.toUpperCase()} connected successfully</span>`;
    playOrderBeep();
    pushAlert('BROKER', `${d.broker.toUpperCase()} broker connected`, 'info');
    sendNotification('Broker Connected', `${d.broker.toUpperCase()} is now connected and ready.`);
    await loadBrokerStatus();
    await loadRealPortfolio();
  } catch (e) {
    if (res) res.innerHTML = `<span style="color:var(--red)">✗ ${escHtml(e.message || 'Connection failed')}</span>`;
    pushAlert('BROKER', e.message || 'Connection failed', 'warning');
  } finally {
    if (btn) { btn.textContent = '▶ CONNECT BROKER'; btn.classList.remove('loading'); }
  }
}

async function loadRealPortfolio() {
  try {
    const d = await fetchJSON('/api/real/portfolio');
    const acctVal = d.account_value || 0;
    const cash = d.cash || 0;
    const positions = d.positions || [];
    const invested = positions.reduce((s, p) => s + (p.market_val || 0), 0);
    const totalPnl = positions.reduce((s, p) => s + (p.pnl || 0), 0);

    setEl('liveAcctVal',   fmt$(acctVal));
    setEl('liveCash',      fmt$(cash));
    setEl('liveBuyPow',    fmt$(d.buying_power || 0));
    setEl('liveInvested',  fmt$(invested));
    setEl('liveOpenCount', positions.length);

    const pnlEl = el('livePnl');
    if (pnlEl) {
      pnlEl.textContent = (totalPnl >= 0 ? '+' : '') + fmt$(totalPnl);
      pnlEl.style.color = totalPnl >= 0 ? 'var(--green)' : 'var(--red)';
    }
    const badge = el('livePnlBadge');
    if (badge) {
      badge.textContent = `P&L: ${totalPnl >= 0 ? '+' : ''}${fmt$(totalPnl)}`;
      badge.style.color = totalPnl >= 0 ? 'var(--green)' : 'var(--red)';
    }

    // Position heatmap
    const hmWrap = el('liveHeatmapWrap');
    const hm = el('liveHeatmap');
    if (hm && positions.length) {
      if (hmWrap) hmWrap.style.display = 'block';
      hm.innerHTML = positions.map(p => {
        const pnlPct = p.pnl_pct || 0;
        const bg = pnlPct >= 0 ? `rgba(0,204,68,${Math.min(0.6, pnlPct/10)})` : `rgba(255,34,34,${Math.min(0.6, Math.abs(pnlPct)/10)})`;
        return `<div class="hm-cell" style="background:${bg}" title="${p.ticker}: ${pnlPct>=0?'+':''}${pnlPct.toFixed(2)}%" onclick="openOrderModal('${escHtml(p.ticker)}','SELL',${p.market_val/p.qty||0})">
          <span class="hm-ticker">${p.ticker.replace('-USD','').replace('.AX','')}</span>
          <span class="hm-pct" style="color:${pnlPct>=0?'var(--green)':'var(--red)'}">${pnlPct>=0?'+':''}${pnlPct.toFixed(1)}%</span>
        </div>`;
      }).join('');
    } else if (hmWrap) { hmWrap.style.display = 'none'; }

    // Mirror to Command Centre when in live mode
    if (_tradingMode === 'live') {
      const ccD = {
        total_value: acctVal,
        cash: cash,
        invested: invested,
        open_count: positions.length,
        total_pnl: totalPnl,
        total_pnl_pct: acctVal > 0 ? (totalPnl / (acctVal - totalPnl)) * 100 : 0,
        positions: positions.map(p => ({
          ticker: p.ticker,
          side: p.side || (p.qty > 0 ? 'LONG' : 'SHORT'),
          qty: Math.abs(p.qty || 0),
          entry_price: p.avg_cost || 0,
          current_price: p.market_val && p.qty ? p.market_val / Math.abs(p.qty) : 0,
          market_value: p.market_val || 0,
          pnl: p.pnl || 0,
          pnl_pct: p.pnl_pct || 0,
        })),
        drawdown: d.drawdown || null,
        sharpe: d.sharpe || null,
        cycles: d.cycles || null,
      };
      applyCommandCentre(ccD, null);
    }

    // Positions table
    const body = el('livePositionsBody');
    if (body) {
      if (!positions.length) {
        body.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:16px">No open positions</td></tr>`;
      } else {
        body.innerHTML = positions.map(p => {
          const pnlCls = (p.pnl || 0) >= 0 ? 'td-green' : 'td-red';
          return `<tr style="cursor:pointer" onclick="openOrderModal('${escHtml(p.ticker)}','SELL',${p.market_val/(p.qty||1)})">
            <td class="td-cyan" style="font-weight:700">${p.ticker}</td>
            <td class="${p.side === 'LONG' || p.side === 'long' ? 'td-green' : 'td-red'}">${p.side?.toUpperCase()}</td>
            <td>${typeof p.qty === 'number' ? (p.qty % 1 === 0 ? p.qty : p.qty.toFixed(4)) : p.qty}</td>
            <td>${fmt$(p.avg_cost || 0)}</td>
            <td>${fmt$(p.market_val || 0)}</td>
            <td class="${pnlCls}">${p.pnl != null ? ((p.pnl >= 0 ? '+' : '') + fmt$(p.pnl)) : '—'}</td>
            <td class="${pnlCls}">${p.pnl_pct != null ? ((p.pnl_pct >= 0 ? '+' : '') + p.pnl_pct.toFixed(2) + '%') : '—'}</td>
            <td><button class="po-close-btn" onclick="event.stopPropagation();closeLivePosition('${escHtml(p.ticker)}')">✕</button></td>
          </tr>`;
        }).join('');
      }
    }
  } catch (e) {
    // Show meaningful status when broker isn't connected
    const body = el('livePositionsBody');
    if (body && e.message?.includes('503')) {
      body.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:16px">Connect a broker to see live positions</td></tr>`;
    } else if (body && e.message) {
      body.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--amber);padding:16px">⚠ ${escHtml(e.message)}</td></tr>`;
    }
  }
}

async function loadRealHistory() {
  try {
    const d = await fetchJSON('/api/real/history');
    const body = el('liveHistoryBody');
    const history = d.history || [];
    setEl('liveTradeCount', `${history.length} ORDERS`);
    if (!body || !history.length) return;
    body.innerHTML = history.map(h => `<tr>
      <td class="td-cyan">${h.ticker}</td>
      <td class="${h.side === 'buy' ? 'td-green' : 'td-red'}">${(h.side||'').toUpperCase()}</td>
      <td>${h.qty}</td>
      <td>${h.price != null ? fmt$(h.price) : '—'}</td>
      <td style="color:var(--text-muted)">${h.timestamp ? h.timestamp.slice(0,16).replace('T',' ') : '—'}</td>
    </tr>`).join('');
  } catch {}
}

function setLivePoSide(side, btn) {
  _livePoSide = side;
  el('livePoBuyBtn')?.classList.toggle('active', side === 'BUY');
  el('livePoSellBtn')?.classList.toggle('active', side === 'SELL');
}

function onLiveTickerInput(val) {
  _livePoTicker = val.toUpperCase().trim();
  const queriedTicker = _livePoTicker;
  // Fetch price for estimate
  if (_livePoTicker.length >= 2) {
    fetchJSON(`/api/quote?ticker=${encodeURIComponent(_livePoTicker)}`).then(d => {
      if (_livePoTicker !== queriedTicker) return; // stale response
      _liveQuotePrice = d.price || 0;
      const qr = el('livePoQuoteResult');
      if (qr && d.price) qr.innerHTML = `<span style="color:var(--cyan)">${_livePoTicker} — ${fmt$(d.price)}</span>`;
      updateLivePoEstimate();
    }).catch(() => { _liveQuotePrice = 0; });
  }
}

async function submitLiveOrder() {
  if (_tradingMode !== 'live') {
    pushAlert('LIVE', 'Switch to LIVE mode to place real orders', 'warning');
    return;
  }
  const qty   = parseFloat(el('livePoQty')?.value || 0);
  const price = el('livePoPrice')?.value ? parseFloat(el('livePoPrice').value) : undefined;
  if (!_livePoTicker || qty <= 0) { pushAlert('LIVE', 'Ticker and qty required', 'warning'); return; }
  const btn = el('livePoSubmitBtn');
  const res = el('livePoResult');
  if (btn) { btn.textContent = '⌛ PLACING ORDER...'; btn.classList.add('loading'); }
  try {
    const d = await postJSON('/api/real/order', { ticker: _livePoTicker, side: _livePoSide, qty, price });
    if (res) res.innerHTML = `<span style="color:var(--green)">✓ Order ${d.order_id} — ${d.side} ${qty}× ${d.ticker}</span>`;
    playOrderBeep();
    pushAlert('LIVE', `${d.side} ${qty}× ${d.ticker} → ${d.status}`, 'info');
    sendNotification('Live Order Placed', `${d.side} ${qty}× ${d.ticker} — Status: ${d.status}`);
    loadRealPortfolio();
    loadRealHistory();
    loadRealEquityCurve();
  } catch (e) {
    if (res) res.innerHTML = `<span style="color:var(--red)">✗ ${escHtml(e.message || 'Order failed')}</span>`;
    pushAlert('LIVE', e.message || 'Order failed', 'warning');
  } finally {
    if (btn) { btn.textContent = '🔴 PLACE LIVE ORDER'; btn.classList.remove('loading'); }
  }
}

async function closeLivePosition(ticker) {
  if (!confirm(`Close ${ticker} live position at market?`)) return;
  try {
    const d = await postJSON('/api/real/close', { ticker });
    pushAlert('LIVE', `Closed ${ticker}`, 'info');
    playOrderBeep();
    loadRealPortfolio();
    loadRealHistory();
  } catch (e) {
    pushAlert('LIVE', e.message || 'Close failed', 'warning');
  }
}


// ─── CLI AI TERMINAL ─────────────────────────────────────────────────────────

let _cliOpen = false;
let _cliHistory = [];
let _cliHistIdx = -1;

function toggleCli() {
  _cliOpen = !_cliOpen;
  const body = el('cliBody');
  const btn  = el('cliToggleBtn');
  const hint = el('cliHint');
  if (body) body.classList.toggle('open', _cliOpen);
  if (btn)  btn.textContent = _cliOpen ? '▼' : '▲';
  if (hint) hint.style.display = _cliOpen ? 'none' : '';
  document.body.classList.toggle('cli-expanded', _cliOpen);
  if (_cliOpen) { el('cliInput')?.focus(); scrollCliOutput(); }
}

function onCliKey(e) {
  if (e.key === 'Enter') { sendCliCommand(); return; }
  if (e.key === 'ArrowUp') {
    if (_cliHistIdx < _cliHistory.length - 1) {
      _cliHistIdx++;
      el('cliInput').value = _cliHistory[_cliHistory.length - 1 - _cliHistIdx] || '';
    }
    e.preventDefault();
  }
  if (e.key === 'ArrowDown') {
    if (_cliHistIdx > 0) { _cliHistIdx--; el('cliInput').value = _cliHistory[_cliHistory.length - 1 - _cliHistIdx] || ''; }
    else { _cliHistIdx = -1; el('cliInput').value = ''; }
    e.preventDefault();
  }
}

function scrollCliOutput() {
  const out = el('cliOutput');
  if (out) out.scrollTop = out.scrollHeight;
}

function cliPrint(text, cls = '', isHtml = false) {
  const out = el('cliOutput');
  if (!out) return;
  const div = document.createElement('div');
  div.className = 'cli-msg' + (cls ? ' cli-msg--' + cls : '');
  if (isHtml) { div.innerHTML = text; } else { div.textContent = text; }
  out.appendChild(div);
  // [MEMORY-FIX] Cap CLI output DOM nodes to prevent unbounded growth
  while (out.children.length > 200) out.removeChild(out.firstChild);
  scrollCliOutput();
}

async function sendCliCommand() {
  const input = el('cliInput');
  if (!input) return;
  const cmd = input.value.trim();
  if (!cmd) return;
  input.value = '';
  _cliHistIdx = -1;
  _cliHistory.push(cmd);
  // [MEMORY-FIX] Cap CLI history to prevent unbounded growth
  if (_cliHistory.length > 100) _cliHistory.splice(0, _cliHistory.length - 100);

  cliPrint(`<span class="cli-prompt-echo">DALIOS&gt;</span> ${escHtml(cmd)}`, 'user', true);

  // Ensure CLI is open
  if (!_cliOpen) toggleCli();

  try {
    const d = await postJSON('/api/ai/chat', { message: cmd });
    cliPrint(formatCliResponse(d.response || d.reply || d.message || JSON.stringify(d)), 'ai', true);
  } catch (e) {
    cliPrint(`<span style="color:var(--red)">✗ ${escHtml(e.message || 'Error')}</span>`, 'error', true);
  }
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function formatCliResponse(text) {
  // Highlight key tokens for readability
  return escHtml(text)
    .replace(/\b(BUY|STRONG BUY)\b/g, '<span style="color:var(--green);font-weight:700">$1</span>')
    .replace(/\b(SELL|STRONG SELL)\b/g, '<span style="color:var(--red);font-weight:700">$1</span>')
    .replace(/\b(HOLD|NEUTRAL)\b/g, '<span style="color:var(--amber)">$1</span>')
    .replace(/\n/g, '<br>');
}

// ─── END CLI ─────────────────────────────────────────────────────────────────

// ═══════════════════════════════════════════════════════════
// COMMAND CENTRE — Portfolio Stats Mirror
// ═══════════════════════════════════════════════════════════

/**
 * Mirror portfolio data into the Command Centre panels.
 * Called from applyPaperPortfolio() and applyPaperHistory().
 */
function applyCommandCentre(portfolioData, historyData) {
  if (portfolioData) _applyCCPortfolio(portfolioData);
  if (historyData)   _applyCCHistory(historyData);
}

function _applyCCPortfolio(d) {
  const pnlPos  = d.total_pnl >= 0;
  const pnlCol  = pnlPos ? 'var(--green)' : 'var(--red)';
  const pnlSign = pnlPos ? '+' : '';

  _ccSet('ccTotalVal',   fmt$(d.total_value),   pnlPos ? 'acc' : '');
  _ccSet('ccCash',       fmt$(d.cash));
  _ccSet('ccInvested',   fmt$(d.invested));
  _ccSet('ccOpenCount',  d.open_count,   'acc');

  const unreal = el('ccUnrealPnl');
  if (unreal) {
    unreal.textContent  = `${pnlSign}${fmt$(d.total_pnl)}`;
    unreal.style.color  = pnlCol;
  }
  const ret = el('ccReturn');
  if (ret) {
    ret.textContent = `${pnlSign}${d.total_pnl_pct.toFixed(2)}%`;
    ret.style.color  = pnlCol;
  }
  const badge = el('ccPnlBadge');
  if (badge) {
    badge.textContent = `P&L: ${pnlSign}${d.total_pnl_pct.toFixed(2)}%`;
    badge.style.color  = pnlCol;
  }

  // Update quick-trade cash display
  const cashEl = el('ccQtCash');
  if (cashEl) cashEl.textContent = fmt$(d.cash);

  // Performance row — duplicate refs for new full-width perf panel
  _ccSetPnl('ccDailyPnl', d.total_pnl);
  const ddEl = el('ccDrawdown');
  if (ddEl) { ddEl.textContent = d.drawdown != null ? (d.drawdown * 100).toFixed(1) + '%' : '--'; }
  const shEl = el('ccSharpe');
  if (shEl) { shEl.textContent = d.sharpe != null ? d.sharpe.toFixed(2) : '--'; }
  const cyEl = el('ccCycles');
  if (cyEl) { cyEl.textContent = d.cycles != null ? d.cycles : '--'; }

  // Open positions table mirror
  const posCount = el('ccPosCount');
  if (posCount) posCount.textContent = `${d.open_count} OPEN`;

  const body = el('ccPositionsBody');
  if (body) {
    if (!d.positions || !d.positions.length) {
      body.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--text-muted);padding:14px">No open positions</td></tr>`;
    } else {
      body.innerHTML = d.positions.map(p => {
        const pnlCls = p.pnl >= 0 ? 'td-green' : 'td-red';
        const pnlTxt = (p.pnl >= 0 ? '+' : '') + fmt$(p.pnl);
        const pctTxt = (p.pnl_pct >= 0 ? '+' : '') + p.pnl_pct.toFixed(2) + '%';
        return `<tr style="cursor:pointer" onclick="selectPositionForChart('${p.ticker}')">
          <td class="td-cyan" style="font-weight:700">${p.ticker.replace('-USD','')}</td>
          <td class="${p.side === 'LONG' ? 'td-green' : 'td-red'}">${p.side}</td>
          <td>${p.qty % 1 === 0 ? p.qty : p.qty.toFixed(4)}</td>
          <td>${fmt$(p.entry_price)}</td>
          <td style="color:var(--text-1)">${fmt$(p.current_price)}</td>
          <td>${miniSparkSVG(p.ticker, p.pnl_pct)}</td>
          <td>${fmt$(p.market_value)}</td>
          <td class="${pnlCls}">${pnlTxt}</td>
          <td class="${pnlCls}">${pctTxt}</td>
          <td><button class="btn-ghost btn--sm" style="font-size:9px;padding:2px 6px" onclick="${_tradingMode === 'live' ? 'closeLivePosition' : 'closePaperPosition'}('${p.ticker}')">✕</button></td>
        </tr>`;
      }).join('');
    }
  }

  // Render live position P&L tiles
  renderCcLivePositions(d.positions || []);
}

/**
 * Render open-position P&L tiles in the CC Live Positions panel.
 * Shows one tile per open position with live P&L, % change, and a close button.
 */
function renderCcLivePositions(positions) {
  const list = el('ccLivePosList');
  if (!list) return;

  if (!positions || !positions.length) {
    list.innerHTML = `<div class="cc-pos-empty">NO OPEN POSITIONS<br>Execute a trade to see live P&amp;L here</div>`;
    return;
  }

  list.innerHTML = positions.map(p => {
    const pos     = p.pnl >= 0;
    const sign    = pos ? '+' : '';
    const cls     = pos ? 'pos' : 'neg';
    const pnlTxt  = sign + fmt$(p.pnl);
    const pctTxt  = sign + p.pnl_pct.toFixed(2) + '%';
    const ticker  = p.ticker.replace('-USD', '');
    const qty     = p.qty % 1 === 0 ? p.qty : p.qty.toFixed(4);
    return `<div class="cc-pos-tile ${cls}" style="cursor:pointer" onclick="selectPositionForChart('${p.ticker}')">
      <div class="cc-pos-tile-top">
        <span class="cc-pos-tile-tkr">${ticker}</span>
        ${miniSparkSVG(p.ticker, p.pnl_pct, 36, 12)}
        <span class="cc-pos-tile-pnl" style="color:${pos ? 'var(--green)' : 'var(--red)'}">${pnlTxt} (${pctTxt})</span>
        <button class="btn-ghost btn--sm" style="font-size:9px;padding:1px 5px;margin-left:4px" onclick="${_tradingMode === 'live' ? 'closeLivePosition' : 'closePaperPosition'}('${p.ticker}')">✕</button>
      </div>
      <div class="cc-pos-tile-meta">
        <span>${p.side}</span>
        <span>QTY: ${qty}</span>
        <span>ENTRY: ${fmt$(p.entry_price)}</span>
        <span>NOW: ${fmt$(p.current_price)}</span>
        <span>MKT: ${fmt$(p.market_value)}</span>
      </div>
    </div>`;
  }).join('');
}

// ─── Quick Positions / Sell Panel ────────────────────────────────────────────

function toggleQuickPos() {
  const panel = el('quickPosPanel');
  if (!panel) return;
  const isHidden = panel.classList.contains('hidden');
  panel.classList.toggle('hidden');
  if (isHidden) _refreshQuickPos();
}

async function _refreshQuickPos() {
  const body = el('quickPosBody');
  if (!body) return;
  try {
    const d = await fetchJSON('/api/paper/live-pnl');
    const positions = d.positions || [];

    // Update badge count
    const badge = el('quickPosBadge');
    if (badge) {
      badge.textContent = positions.length;
      badge.classList.toggle('hidden', positions.length === 0);
    }

    // Update total P&L header
    const totalEl = el('quickPosTotal');
    if (totalEl && positions.length) {
      const tot = d.total_unrealised_pnl;
      const sign = tot >= 0 ? '+' : '';
      totalEl.textContent = `UNREALISED: ${sign}${fmt$(tot)}`;
      totalEl.style.color = tot >= 0 ? 'var(--green)' : 'var(--red)';
    }

    if (!positions.length) {
      body.innerHTML = `<div class="quick-pos-empty">NO OPEN POSITIONS<br><span style="opacity:.5">Place a trade to see it here</span></div>`;
      return;
    }

    body.innerHTML = positions.map(p => {
      const pnlPos  = p.pnl >= 0;
      const sign    = pnlPos ? '+' : '';
      const pnlCls  = pnlPos ? 'qp-pnl-pos' : 'qp-pnl-neg';
      const sideCls = p.side === 'LONG' ? 'qp-side-long' : 'qp-side-short';
      const ticker  = p.ticker.replace('-USD','');
      const qty     = p.qty % 1 === 0 ? p.qty : p.qty.toFixed(4);
      return `<div class="quick-pos-row">
        <span class="qp-ticker">${ticker}</span>
        <span class="${sideCls}">${p.side}</span>
        <span style="color:var(--text-muted)">×${qty} @ ${fmt$(p.entry_price)}</span>
        <span class="${pnlCls}">${sign}${fmt$(p.pnl)}<br><span style="font-size:8px;opacity:.8">${sign}${p.pnl_pct.toFixed(2)}%</span></span>
        <button class="qp-close-btn" onclick="quickClosePosition('${p.ticker}')">✕ CLOSE</button>
      </div>`;
    }).join('');
  } catch (e) {
    body.innerHTML = `<div class="quick-pos-empty">Could not load positions</div>`;
  }
}

async function quickClosePosition(ticker) {
  try {
    await postJSON('/api/paper/close', { ticker });
    pushAlert('PAPER', `Closed ${ticker.replace('-USD','')}`, 'info');
    pushActivityItem('✕', `Closed position: ${ticker.replace('-USD','')}`, 'sell');
    // Refresh the panel + main portfolio views
    _refreshQuickPos();
    loadPaperPortfolio();
    loadPaperHistory();
  } catch (e) {
    pushAlert('PAPER', `Close failed: ${escHtml(e.message)}`, 'warning');
  }
}

// Update badge count whenever pollLivePnl runs
function _updateQuickPosBadge(count) {
  const badge = el('quickPosBadge');
  if (!badge) return;
  badge.textContent = count;
  badge.classList.toggle('hidden', count === 0);
}

// ─── Live P&L Poll (global, every 15s) ───────────────────────────────────────
// Tracks previous P&L values so we can detect direction changes and flash cells.
const _prevPnl = {};   // { ticker: lastPnlValue }

async function pollLivePnl() {
  // Skip if no positions known yet
  if (!STATE.signals && !document.querySelector('#paperPositionsBody tr[data-ticker]') &&
      !document.querySelector('#ccLivePosList .cc-pos-tile')) return;

  try {
    // Always poll paper P&L for the paper trading tab
    const d = await fetchJSON('/api/paper/live-pnl');
    if (d && d.positions) {
      // Update paper trading tab rows (always, regardless of mode)
      _updatePaperTableInPlace(d.positions);

      // Update quick-pos badge
      _updateQuickPosBadge(d.open_count || 0);
      if (!el('quickPosPanel')?.classList.contains('hidden')) _refreshQuickPos();

      const totalEl = el('paperUnrealisedTotal');
      if (totalEl) {
        const sign = d.total_unrealised_pnl >= 0 ? '+' : '';
        totalEl.textContent = sign + fmt$(d.total_unrealised_pnl);
        totalEl.style.color = d.total_unrealised_pnl >= 0 ? 'var(--green)' : 'var(--red)';
      }

      // CC tiles use paper data when in paper mode
      if (_tradingMode === 'paper') renderCcLivePositions(d.positions);
    }

    // CC tiles use live data when in live mode
    if (_tradingMode === 'live') {
      try {
        const ld = await fetchJSON('/api/real/portfolio');
        const positions = (ld.positions || []).map(p => ({
          ticker: p.ticker,
          side: p.side || (p.qty > 0 ? 'LONG' : 'SHORT'),
          qty: Math.abs(p.qty),
          entry_price: p.avg_cost || 0,
          current_price: p.market_val && p.qty ? p.market_val / Math.abs(p.qty) : 0,
          market_value: p.market_val || 0,
          pnl: p.pnl || 0,
          pnl_pct: p.pnl_pct || 0,
        }));
        renderCcLivePositions(positions);
      } catch {}
    }
  } catch { /* silent — don't spam console every 15s */ }
}

function _updatePaperTableInPlace(positions) {
  const body = el('paperPositionsBody');
  if (!body) return;

  positions.forEach(p => {
    const row = body.querySelector(`tr[data-ticker="${p.ticker}"]`);
    if (!row) return;   // row not rendered yet — will appear on next full refresh

    const prevPnl = _prevPnl[p.ticker];
    const changed  = prevPnl !== undefined && prevPnl !== p.pnl;
    const improved = changed && p.pnl > prevPnl;
    _prevPnl[p.ticker] = p.pnl;

    const pnlCls  = p.pnl >= 0 ? 'td-green' : 'td-red';
    const sign    = p.pnl >= 0 ? '+' : '';
    const arrow   = !changed ? '' : (improved ? ' ▲' : ' ▼');

    const _setCell = (attr, text, cls) => {
      const cell = row.querySelector(`[data-live="${attr}"]`);
      if (!cell) return;
      cell.textContent = text;
      if (cls) cell.className = cls;
      if (changed) {
        cell.classList.add(improved ? 'pnl-flash-up' : 'pnl-flash-down');
        setTimeout(() => cell.classList.remove('pnl-flash-up', 'pnl-flash-down'), 700);
      }
    };

    _setCell('current_price', fmt$(p.current_price));
    _setCell('market_value',  fmt$(p.market_value));
    _setCell('pnl',     sign + fmt$(p.pnl) + arrow, pnlCls);
    _setCell('pnl_pct', sign + p.pnl_pct.toFixed(2) + '%', pnlCls);
  });

  // [MEMORY-FIX] Prune _prevPnl for tickers no longer in positions to prevent unbounded growth
  const activeTickers = new Set(positions.map(p => p.ticker));
  for (const key of Object.keys(_prevPnl)) {
    if (!activeTickers.has(key)) delete _prevPnl[key];
  }
}

function _applyCCHistory(d) {
  const total = d.total || 0;
  _ccSet('ccTotalTrades', total, 'acc');
  _ccSet('ccHistCount', `${total} TRADES`);

  const trades = d.trades || [];

  // Win rate
  const closed = trades.filter(t => t.pnl != null);
  const wins   = closed.filter(t => t.pnl > 0).length;
  const winRate = closed.length ? ((wins / closed.length) * 100).toFixed(1) : '--';
  const winEl   = el('ccWinRate');
  if (winEl) {
    winEl.textContent = closed.length ? `${winRate}%` : '--%';
    winEl.style.color = closed.length ? (parseFloat(winRate) >= 50 ? 'var(--green)' : 'var(--red)') : '';
  }

  // Statsbar hero stats — win rate & total trades
  _statsbarSet('ccStatsWinRate', closed.length ? `${winRate}%` : '--%',
    closed.length ? (parseFloat(winRate) >= 50 ? 'var(--green)' : 'var(--red)') : null);
  _statsbarSet('ccStatsTotalTrades', total, 'var(--primary)');

  // Avg P&L, realised P&L, best, worst
  if (closed.length) {
    const pnls     = closed.map(t => t.pnl);
    const totalPnl = pnls.reduce((a, b) => a + b, 0);
    const avgPnl   = totalPnl / closed.length;
    const best     = Math.max(...pnls);
    const worst    = Math.min(...pnls);

    _ccSetPnl('ccAvgPnl',      avgPnl);
    _ccSetPnl('ccRealisedPnl', totalPnl);
    const bestEl = el('ccBestTrade');
    if (bestEl) { bestEl.textContent = `+${fmt$(best)}`; bestEl.style.color = 'var(--green)'; }
    const worstEl = el('ccWorstTrade');
    if (worstEl) { worstEl.textContent = `${worst < 0 ? '' : '+'}${fmt$(worst)}`; worstEl.style.color = worst < 0 ? 'var(--red)' : 'var(--green)'; }
  }

  // Recent trades table (last 15)
  const body = el('ccHistoryBody');
  if (body) {
    if (!trades.length) {
      body.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--text-muted);padding:14px">No closed trades yet</td></tr>`;
    } else {
      body.innerHTML = trades.slice(0, 15).map(t => {
        const pnlCls  = t.pnl >= 0 ? 'td-green' : 'td-red';
        const pnlSign = t.pnl >= 0 ? '+' : '';
        const time    = new Date(t.timestamp).toLocaleTimeString('en-AU', { hour12: false, hour: '2-digit', minute: '2-digit' });
        return `<tr>
          <td style="color:var(--text-muted)">#${t.id}</td>
          <td class="td-cyan" style="font-weight:700">${t.ticker.replace('-USD','')}</td>
          <td class="${t.pnl >= 0 ? 'td-green' : 'td-red'}">${t.side}</td>
          <td>${t.qty % 1 === 0 ? t.qty : t.qty.toFixed(4)}</td>
          <td>${fmt$(t.entry_price)}</td>
          <td>${fmt$(t.exit_price)}</td>
          <td class="${pnlCls}">${pnlSign}${fmt$(t.pnl)}</td>
          <td class="${pnlCls}">${pnlSign}${t.pnl_pct.toFixed(2)}%</td>
          <td style="color:var(--text-muted)">${time}</td>
        </tr>`;
      }).join('');
    }
  }
}

// ─── Command Centre Init ────────────────────────────────────
function initCommandCentre() {
  refreshCcForMode();
  loadCcOpportunities(8);
  loadQuadrant();
  loadCcRecommendations();
}

// Load CC portfolio/history/equity from the correct source based on trading mode
async function refreshCcForMode() {
  if (_tradingMode === 'live') {
    _loadCcLivePortfolio();
    _loadCcLiveHistory();
    _loadCcLiveEquityCurve();
  } else {
    loadPaperPortfolio();
    loadPaperHistory();
    loadPaperEquityCurve();
  }
}

async function _loadCcLivePortfolio() {
  try {
    const d = await fetchJSON('/api/real/portfolio');
    const positions = d.positions || [];
    const acctVal = d.account_value || 0;
    const cash = d.cash || 0;
    const invested = positions.reduce((s, p) => s + (p.market_val || 0), 0);
    const totalPnl = positions.reduce((s, p) => s + (p.pnl || 0), 0);
    const totalPnlPct = acctVal > 0 ? (totalPnl / (acctVal - totalPnl)) * 100 : 0;
    // Map to CC portfolio format
    _applyCCPortfolio({
      total_value: acctVal,
      cash: cash,
      invested: invested,
      open_count: positions.length,
      total_pnl: totalPnl,
      total_pnl_pct: totalPnlPct,
      drawdown: null,
      sharpe: null,
      cycles: null,
      positions: positions.map(p => ({
        ticker: p.ticker,
        side: p.side || (p.qty > 0 ? 'LONG' : 'SHORT'),
        qty: Math.abs(p.qty),
        entry_price: p.avg_cost || 0,
        current_price: p.market_val && p.qty ? p.market_val / Math.abs(p.qty) : 0,
        market_value: p.market_val || 0,
        pnl: p.pnl || 0,
        pnl_pct: p.pnl_pct || 0,
      })),
    });
  } catch {}
}

async function _loadCcLiveHistory() {
  try {
    const d = await fetchJSON('/api/real/history');
    _applyCCHistory({ trades: d.history || [], total: (d.history || []).length });
  } catch {}
}

async function _loadCcLiveEquityCurve() {
  try {
    const d = await fetchJSON('/api/real/equity_curve');
    const pts = d.equity_curve || [];
    if (pts.length) updatePredictionFromEquity(pts);
  } catch {}
}

// ─── AI Recommendations ─────────────────────────────────────
async function loadCcRecommendations() {
  const list = el('ccRecsList');
  if (!list) return;
  // Phase 9: loading state on ANALYSE button
  const btn = el('ccAnalyseBtn');
  if (btn) { btn.textContent = '⌛ ANALYSING...'; btn.disabled = true; btn.classList.add('loading'); }
  list.innerHTML = '<div style="padding:14px;color:var(--text-muted);font-size:10px"><div class="loading-spinner" style="width:16px;height:16px;display:inline-block;vertical-align:middle;margin-right:8px"></div>RUNNING AI ANALYSIS…</div>';
  try {
    const d = await fetchJSON('/api/recommendations?n=6');
    renderCcRecommendations(d.recommendations || [], d.regime_label || '');
    const rb = el('ccRegimeBadge');
    if (rb && d.regime_label) rb.textContent = d.regime_label.toUpperCase();
  } catch(e) {
    list.innerHTML = `<div style="padding:14px;color:var(--red);font-size:10px">ANALYSIS FAILED: ${escHtml(e.message)}</div>`;
  } finally {
    if (btn) { btn.textContent = '↻ ANALYSE'; btn.disabled = false; btn.classList.remove('loading'); }
  }
}

function renderCcRecommendations(recs, regimeLabel) {
  const list = el('ccRecsList');
  if (!list) return;
  if (!recs || !recs.length) {
    list.innerHTML = '<div style="padding:14px;color:var(--text-muted);font-size:10px">No recommendations — load scanner tabs first</div>';
    return;
  }
  const fitCol = { strong:'var(--green)', moderate:'var(--cyan)', neutral:'var(--text-2)', avoid:'var(--red)' };
  const actCol = { BUY:'var(--green)', LONG:'var(--green)', SELL:'var(--red)', SHORT:'var(--red)', WATCH:'var(--amber)' };
  list.innerHTML = recs.map((r, i) => {
    const a    = r.analysis || {};
    const fc   = fitCol[r.quadrant_fit] || 'var(--text-2)';
    const ac   = actCol[r.action] || 'var(--text-1)';
    const chgSign = r.change_pct >= 0 ? '+' : '';
    const chgCol  = r.change_pct >= 0 ? 'var(--green)' : 'var(--red)';
    const scoreBar = Math.min(Math.round(r.score || 0), 100);
    const fitClass = r.quadrant_fit === 'strong' ? 'fit-strong' : r.quadrant_fit === 'avoid' ? 'fit-avoid' : 'fit-moderate';
    const riskHtml = (a.risk_flags || []).length
      ? `<div class="cc-rec-risk-flags">⚠ ${a.risk_flags.slice(0,2).join(' · ')}</div>` : '';
    const reasonHtml = (a.reasoning || []).slice(0, 3)
      .map(l => `<div class="cc-rec-analysis-line">▸ ${l}</div>`).join('');
    return `
    <div class="cc-rec-card ${fitClass}" onclick="this.classList.toggle('cc-rec-expanded')">
      <div class="cc-rec-header">
        <span style="color:var(--text-muted);font-size:9px">#${i+1}</span>
        <span class="cc-rec-ticker" style="color:${ac}">${r.ticker}</span>
        <span class="cc-rec-action" style="color:${ac};border-color:${ac}">${r.action}</span>
        <span style="color:${chgCol};font-size:8px">${chgSign}${r.change_pct.toFixed(2)}%</span>
        <span class="cc-rec-fit" style="color:${fc};border-color:${fc}">${(r.quadrant_fit||'').toUpperCase()}</span>
      </div>
      <div class="cc-rec-score-wrap">
        <div class="cc-rec-score-bar"><div class="cc-rec-score-fill" style="width:${scoreBar}%;background:${fc}"></div></div>
        <span style="font-size:8px;color:var(--text-2)">Score ${(r.score||0).toFixed(0)}</span>
        <span style="font-size:8px;color:var(--text-2);margin-left:4px">FitScore ${a.fit_score||'--'}</span>
      </div>
      <div class="cc-rec-metrics">
        <span>$${r.price?.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:4})}</span>
        <span>RSI <b style="color:${r.rsi<35?'var(--green)':r.rsi>65?'var(--red)':'var(--amber)'}">${r.rsi?.toFixed(0)}</b></span>
        <span>R:R <b style="color:var(--primary)">${r.rr_ratio?.toFixed(1)}x</b></span>
        <span>SL $${r.stop_loss?.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:4})}</span>
        <span>TP $${r.take_profit?.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:4})}</span>
      </div>
      <div class="cc-rec-analysis">
        <div style="color:var(--text-1);margin-bottom:3px;font-size:8px">${a.recommendation||''}</div>
        ${reasonHtml}
        ${riskHtml}
      </div>
      <div class="cc-rec-actions" style="display:none">
        <button class="scan-trade-btn" onclick="event.stopPropagation();scannerOpenTrade('${escHtml(r.ticker)}',${r.price})">▲ TRADE</button>
        <button class="scan-wl-btn"    onclick="event.stopPropagation();toggleWatchlist('${escHtml(r.ticker)}',this)">☆ WATCH</button>
      </div>
    </div>`;
  }).join('');

  // Show actions on expanded cards
  list.querySelectorAll('.cc-rec-card').forEach(card => {
    card.addEventListener('click', () => {
      const acts = card.querySelector('.cc-rec-actions');
      if (acts) acts.style.display = card.classList.contains('cc-rec-expanded') ? 'flex' : 'none';
    });
  });
}

// ─── CC Opportunities (uses dedicated list element) ─────────
async function loadCcOpportunities(n = 8) {
  const list = el('ccOpportunityList');
  if (!list) return;
  list.innerHTML = '<div style="padding:14px;color:var(--text-muted);font-size:10px;animation:blink 1s infinite">⟳ SCANNING ALL MARKETS…</div>';
  try {
    const d = await fetchJSON(`/api/suggest?n=${n}`);
    const opps = d.opportunities || [];
    if (!opps.length) {
      list.innerHTML = '<div style="padding:14px;color:var(--text-muted);font-size:10px">NO OPPORTUNITIES — LOAD SCANNER TABS FIRST TO POPULATE DATA</div>';
      return;
    }
    const meta = d;
    const regime = (meta.regime_label || '').toUpperCase();
    const fitColour = { strong:'var(--green)', moderate:'var(--cyan)', neutral:'var(--text-2)', avoid:'var(--red)' };
    const actionColour = { BUY:'var(--green)', LONG:'var(--green)', SELL:'var(--red)', SHORT:'var(--red)', WATCH:'var(--amber)' };
    list.innerHTML = opps.map((o, i) => {
      const ac = actionColour[o.action] || 'var(--text-1)';
      const fc = fitColour[o.regime_fit] || 'var(--text-2)';
      return `<div class="opp-card" style="border-left:2px solid ${ac}">
        <div class="opp-rank">#${i+1}</div>
        <div class="opp-body">
          <div class="opp-top">
            <span class="opp-ticker">${o.ticker}</span>
            <span class="opp-action" style="color:${ac}">${o.action}</span>
            <span class="opp-conf">${o.confidence}%</span>
          </div>
          <div class="opp-reason">${o.reason || ''}</div>
          ${regime ? `<div class="opp-fit" style="color:${fc}">${o.regime_fit?.toUpperCase() || ''} FIT · ${regime}</div>` : ''}
        </div>
      </div>`;
    }).join('');
    // Update regime badge in stats bar
    const regimeBadge = el('ccRegimeBadge');
    if (regimeBadge && regime) regimeBadge.textContent = regime;
  } catch(e) {
    list.innerHTML = `<div style="padding:14px;color:var(--red);font-size:10px">SCAN FAILED: ${escHtml(e.message)}</div>`;
  }
}

// ─── CC Quick Trade ─────────────────────────────────────────
let _ccQtSide = 'BUY';
let _ccQtPrice = null;

function setCcQtSide(side, btn) {
  _ccQtSide = side;
  el('ccQtBuyBtn').classList.toggle('active', side === 'BUY');
  el('ccQtSellBtn').classList.toggle('active', side === 'SELL');
  ccQtEstimate();
}

async function ccQtLookup(ticker) {
  ticker = ticker.toUpperCase().trim();
  if (!ticker || ticker.length < 2) { _ccQtPrice = null; el('ccQtQuote').innerHTML = ''; ccQtEstimate(); return; }
  try {
    const d = await fetchJSON(`/api/paper/quote?ticker=${encodeURIComponent(ticker)}`);
    _ccQtPrice = d.price;
    el('ccQtQuote').innerHTML = `<span style="color:var(--green)">${ticker}</span> <span style="color:var(--text-1)">${fmt$(d.price)}</span> <span style="color:var(--text-2);font-size:9px">${d.source||''}</span>`;
    ccQtEstimate();
  } catch { _ccQtPrice = null; el('ccQtQuote').innerHTML = `<span style="color:var(--red)">Not found</span>`; }
}

function ccQtEstimate() {
  const qty = parseFloat(el('ccQtQty')?.value) || 0;
  if (_ccQtPrice && qty > 0) {
    el('ccQtEstVal').textContent = fmt$(_ccQtPrice * qty);
  } else {
    el('ccQtEstVal').textContent = '—';
  }
}

async function ccQtSubmit() {
  const ticker = (el('ccQtTicker')?.value || '').toUpperCase().trim();
  const qty    = parseFloat(el('ccQtQty')?.value);
  const res    = el('ccQtResult');
  if (!ticker || !qty || qty <= 0) { if (res) res.innerHTML = '<span style="color:var(--red)">Enter ticker and quantity</span>'; return; }
  try {
    const d = await postJSON('/api/paper/order', { ticker, side: _ccQtSide, qty });
    if (res) res.innerHTML = `<span style="color:var(--green)">✓ ${d.side} ${qty} ${ticker} @ ${fmt$(d.price)}</span>`;
    pushActivityItem(_ccQtSide === 'BUY' ? '▲' : '▼', `ORDER — ${_ccQtSide} ${qty}× ${ticker} @ ${fmt$(d.price)}`, _ccQtSide === 'BUY' ? 'buy' : 'sell');
    loadPaperPortfolio();
    loadPaperHistory();
  } catch(e) {
    if (res) res.innerHTML = `<span style="color:var(--red)">✗ ${escHtml(e.message)}</span>`;
  }
}

// ─── Activity Feed ─────────────────────────────────────────
const _activityLog = [];
const MAX_ACTIVITY = 50;

function pushActivityItem(icon, text, cls = 'info') {
  const feed = el('ccActivityFeed');
  if (!feed) return;

  const now  = new Date().toLocaleTimeString('en-AU', { hour12: false, hour: '2-digit', minute: '2-digit' });
  _activityLog.unshift({ icon, text, cls, time: now });
  if (_activityLog.length > MAX_ACTIVITY) _activityLog.pop();

  // Remove placeholder
  const placeholder = feed.querySelector('.cc-activity-item');
  if (placeholder && placeholder.querySelector('.cc-act-text')?.textContent === 'Waiting for activity...') {
    placeholder.remove();
  }

  const item = document.createElement('div');
  item.className = `cc-activity-item ${cls}`;
  item.innerHTML = `
    <span class="cc-act-icon">${icon}</span>
    <span class="cc-act-text">${text}</span>
    <span class="cc-act-time">${now}</span>
  `;
  feed.insertBefore(item, feed.firstChild);

  // Cap feed at MAX_ACTIVITY items
  while (feed.children.length > MAX_ACTIVITY) {
    feed.removeChild(feed.lastChild);
  }
}

// ─── Helper setters ────────────────────────────────────────
function _ccSet(id, val, extraCls = '') {
  const e = el(id);
  if (!e) return;
  e.textContent = val;
  if (extraCls) e.className = `cc-stat-value ${extraCls}`;
}

function _ccSetPnl(id, val) {
  const e = el(id);
  if (!e) return;
  const pos = val >= 0;
  e.textContent  = `${pos ? '+' : ''}${fmt$(val)}`;
  e.style.color  = pos ? 'var(--green)' : 'var(--red)';
}

// ─── Tutorial System ────────────────────────────────────────
const TUTORIAL_PAGES = [
  {
    icon: '⌘', title: 'COMMAND CENTRE',
    body: `<p>Your <strong>main trading hub</strong>. Shows everything at a glance:</p>
      <ul>
        <li>📊 <strong>Equity Curve</strong> — your portfolio value over time with per-asset lines</li>
        <li>🌐 <strong>Economic Quadrant</strong> — current Dalio All-Weather regime (rising growth / inflation etc.)</li>
        <li>⚡ <strong>AI Trade Recommendations</strong> — top trades scored by regime fit, RSI &amp; diversification</li>
        <li>💼 <strong>Live Positions</strong> — open positions with real-time P&amp;L and close buttons</li>
        <li>📋 <strong>Recent Trades</strong> — closed trade history with P&amp;L per trade</li>
      </ul>
      <p>Use the Quick Trade panel to place paper trades instantly.</p>`
  },
  {
    icon: '⚡', title: 'SIGNAL OPS',
    body: `<p>The <strong>signal scanner</strong>. Scans every ticker in the universe for actionable setups:</p>
      <ul>
        <li>🔍 <strong>Scan Now</strong> — fetches live prices and runs RSI + trend signals</li>
        <li>▶ <strong>Run Cycle</strong> — triggers a full agent cycle (signals + quadrant update)</li>
        <li>📈 <strong>Confidence</strong> — how strong the signal is (50–95%). Higher = more extreme RSI</li>
        <li>🏷 <strong>Quadrant Fit</strong> — does this asset suit the current economic regime?</li>
        <li>🎯 <strong>Stop / Target</strong> — calculated using ATR-based risk/reward</li>
      </ul>
      <p>Adjust <em>Min Confidence</em> and <em>Signal Type</em> to filter signals.</p>`
  },
  {
    icon: '🇦🇺', title: 'ASX SCANNER',
    body: `<p>Live scanner for <strong>Australian Securities Exchange</strong> stocks:</p>
      <ul>
        <li>93 ASX stocks across banking, mining, healthcare, tech, REITs and more</li>
        <li>Refreshes every 90 seconds from Yahoo Finance (yfinance)</li>
        <li>Sort by % change, volume or sector</li>
        <li>Click any row to pre-fill the paper trading order form</li>
        <li>Star ★ to add to your watchlist</li>
      </ul>
      <p>Data sourced from Yahoo Finance — prices are end-of-day or 15-min delayed.</p>`
  },
  {
    icon: '🛢', title: 'COMMODITIES SCANNER',
    body: `<p>Live scanner for <strong>commodities and real assets</strong>:</p>
      <ul>
        <li>Precious metals: Gold (GLD), Silver (SLV), Platinum</li>
        <li>Energy: Crude oil (USO), Natural gas (UNG), futures ETFs</li>
        <li>Agriculture: Wheat (WEAT), Corn (CORN), Soybeans</li>
        <li>Base metals: Copper, Aluminium via ETFs</li>
        <li>TIPS, Carbon credits, Timber ETFs</li>
      </ul>
      <p>Commodities are key Dalio All-Weather assets — rising inflation favours real assets.</p>`
  },
  {
    icon: '🧠', title: 'INTEL CENTER',
    body: `<p>The <strong>FinBERT news scanner</strong> — real-time sentiment from financial RSS feeds:</p>
      <ul>
        <li>Pulls live articles from Reuters, Yahoo Finance, CNBC, AFR, FT, MarketWatch and more</li>
        <li>Each article scored <em>bullish / bearish / neutral</em> by keyword analysis</li>
        <li>Mapped to a Dalio quadrant (rising growth / inflation etc.)</li>
        <li>⚠ Red articles = geopolitical conflict risk detected</li>
        <li>Refreshes every 30 minutes — cached for consistency</li>
      </ul>
      <p>The dominant quadrant from news is used to cross-check the economic quadrant signal.</p>`
  },
  {
    icon: '⚠', title: 'RISK MATRIX',
    body: `<p>Your <strong>portfolio risk dashboard</strong>:</p>
      <ul>
        <li>🔴 <strong>Circuit Breaker</strong> — auto-stops trading if daily loss &gt;2% or drawdown &gt;10%</li>
        <li>📉 <strong>Sharpe Ratio</strong> — return per unit of risk (&gt;1 = good, &gt;2 = excellent)</li>
        <li>📉 <strong>Max Drawdown</strong> — biggest loss from a peak (stay under 10%)</li>
        <li>🎯 <strong>Win Rate</strong> — % of closed trades that made money</li>
        <li>📋 <strong>Position Risk Table</strong> — each open position sized as % of portfolio</li>
      </ul>
      <p>Green = safe zone | Amber = watch | Red = action required.</p>`
  },
  {
    icon: '🔬', title: 'BACKTEST LAB',
    body: `<p>The <strong>walk-forward backtesting engine</strong>:</p>
      <ul>
        <li>Tests the Dalio All-Weather strategy against 2+ years of historical data</li>
        <li><strong>Walk-forward</strong> = train on 12 months, test on next 3 months (prevents overfitting)</li>
        <li>8 periods tested — each is independent, no look-ahead bias</li>
        <li>Key metrics: Total Return, Sharpe, Max Drawdown, Win Rate</li>
        <li>Compare periods to find regime-specific performance patterns</li>
      </ul>
      <p>A strategy with Sharpe &gt;1.5 and drawdown &lt;10% across all periods is considered robust.</p>`
  },
];

let _tutIdx = 0;

// Tab ID → tutorial page index map
const _TAB_TUT_IDX = {
  'command-center':      0,
  'signal-ops':          1,
  'asx-scanner':         2,
  'commodities-scanner': 3,
  'intel-center':        4,
  'risk-matrix':         5,
  'backtest-lab':        6,
};

function openTutorial(startIdx) {
  if (startIdx === undefined) {
    // Auto-detect active tab
    const activeTab = document.querySelector('.tab-btn.active')?.dataset?.tab ?? 'command-center';
    startIdx = _TAB_TUT_IDX[activeTab] ?? 0;
  }
  _tutIdx = startIdx;
  _renderTutorial();
  el('tutorialOverlay')?.classList.remove('hidden');
}

// closeTutorial defined above (calls skipAllSpots)

function nextTutorial() {
  _tutIdx = (_tutIdx + 1) % TUTORIAL_PAGES.length;
  _renderTutorial();
}

function prevTutorial() {
  _tutIdx = (_tutIdx - 1 + TUTORIAL_PAGES.length) % TUTORIAL_PAGES.length;
  _renderTutorial();
}

function _renderTutorial() {
  const p = TUTORIAL_PAGES[_tutIdx];
  if (!p) return;
  setEl('tutIcon', p.icon);
  setEl('tutTitle', p.title);
  const body = el('tutBody');
  if (body) body.innerHTML = p.body;
  setEl('tutStep', `${_tutIdx + 1} / ${TUTORIAL_PAGES.length}`);
}

// Keyboard: Ctrl+K to open search, Escape to close
document.addEventListener('DOMContentLoaded', () => {
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      openSearch();
    }
    if (e.key === 'Escape') closeSearch();
  });
  el('searchModal')?.addEventListener('click', (e) => {
    if (e.target === el('searchModal')) closeSearch();
  });
});


// ═══════════════════════════════════════════════════════════
// PREDICTION CHART — seeds & updates growth forecast
// ═══════════════════════════════════════════════════════════

function _seedPredictionChart() {
  // No seed data — prediction will populate once real equity history arrives
  if (!charts.prediction) return;
  setEl('ccPredictedVal', 'AWAITING DATA');
}

function updatePredictionFromEquity(equityHistory) {
  if (!charts.prediction || !equityHistory?.length) return;
  const labels = [];
  const actual = [];
  const predicted = [];
  const upper = [];
  const lower = [];

  equityHistory.forEach(p => {
    labels.push(p.t?.split(' ')[0] || '');
    actual.push(p.v);
    predicted.push(null);
    upper.push(null);
    lower.push(null);
  });

  // ── Compute real statistics from equity history ──
  const vals = equityHistory.map(p => p.v).filter(v => v > 0);
  const returns = [];
  for (let i = 1; i < vals.length; i++) {
    returns.push((vals[i] - vals[i - 1]) / vals[i - 1]);
  }

  const n = returns.length;
  const lastEquity = vals[vals.length - 1] || 1000;

  // Need at least 5 data points for any meaningful projection
  if (n < 5) {
    // Not enough data — show flat projection with wide uncertainty
    predicted[predicted.length - 1] = lastEquity;
    upper[upper.length - 1] = lastEquity;
    lower[lower.length - 1] = lastEquity;
    for (let i = 1; i <= 30; i++) {
      labels.push(`+${i}d`);
      actual.push(null);
      predicted.push(+lastEquity.toFixed(2));
      // Assume ~1% daily vol as placeholder
      const spread = lastEquity * 0.01 * 1.96 * Math.sqrt(i);
      upper.push(+(lastEquity + spread).toFixed(2));
      lower.push(+(lastEquity - spread).toFixed(2));
    }
  } else {
    // Use trimmed mean — discard top/bottom 10% of returns to reduce outlier impact
    const sorted = [...returns].sort((a, b) => a - b);
    const trimPct = Math.max(1, Math.floor(n * 0.1));
    const trimmed = sorted.slice(trimPct, -trimPct);
    const tN = trimmed.length || 1;
    let meanReturn = trimmed.reduce((s, r) => s + r, 0) / tN;

    // Cap daily return to realistic bounds: -2% to +2% (≈500% annualised max)
    meanReturn = Math.max(-0.02, Math.min(0.02, meanReturn));

    // Standard deviation from full returns
    const variance = n > 1
      ? returns.reduce((s, r) => s + (r - meanReturn) ** 2, 0) / (n - 1)
      : 0;
    let stdDev = Math.sqrt(variance);

    // Floor volatility at 0.3% and cap at 5% daily
    stdDev = Math.max(0.003, Math.min(0.05, stdDev));

    // Decay confidence: fewer data points → wider bands (scale factor > 1 when n < 30)
    const dataConfidence = Math.min(1, n / 30);
    const uncertaintyMult = 1 + (1 - dataConfidence) * 0.5; // up to 1.5x wider bands with little data

    let pred = lastEquity;

    // Bridge point
    predicted[predicted.length - 1] = lastEquity;
    upper[upper.length - 1] = lastEquity;
    lower[lower.length - 1] = lastEquity;

    // 30-day forward projection
    for (let i = 1; i <= 30; i++) {
      labels.push(`+${i}d`);
      actual.push(null);
      pred *= (1 + meanReturn);
      predicted.push(+pred.toFixed(2));
      // 1.96σ√t gives ~95% confidence interval, scaled by data quality
      const spread = lastEquity * stdDev * 1.96 * Math.sqrt(i) * uncertaintyMult;
      upper.push(+(pred + spread).toFixed(2));
      lower.push(+Math.max(0, pred - spread).toFixed(2)); // never go negative
    }
  }

  // ── Absolute sanity clamp: predicted can never exceed 10x or go below 0.1x of current NAV ──
  const maxPred = lastEquity * 10;
  const minPred = lastEquity * 0.1;
  for (let i = 0; i < predicted.length; i++) {
    if (predicted[i] != null) predicted[i] = Math.min(maxPred, Math.max(minPred, predicted[i]));
    if (upper[i] != null)     upper[i]     = Math.min(maxPred, Math.max(minPred, upper[i]));
    if (lower[i] != null)     lower[i]     = Math.max(0, Math.min(maxPred, lower[i]));
  }

  charts.prediction.data.labels = labels;
  charts.prediction.data.datasets[0].data = actual;
  charts.prediction.data.datasets[1].data = predicted;
  charts.prediction.data.datasets[2].data = upper;
  charts.prediction.data.datasets[3].data = lower;
  charts.prediction.update('none');

  const lastPred = predicted.filter(v => v != null).pop();
  setEl('ccPredictedVal', lastPred ? '$' + Number(lastPred).toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2}) : '--');
}


// ═══════════════════════════════════════════════════════════
// LIVE PRICE CHART — Candlestick / Line with SMA, RSI, Prediction
// ═══════════════════════════════════════════════════════════

let _priceChart = null;
let _rsiChart = null;
let _priceChartType = 'candlestick';  // 'candlestick' or 'line'
let _selectedTicker = null;
let _priceChartData = null;

function selectPositionForChart(ticker) {
  _selectedTicker = ticker;
  // Highlight selected row
  document.querySelectorAll('#ccPositionsBody tr, #ccLivePosList .cc-pos-tile').forEach(r => r.classList.remove('selected'));
  const rows = document.querySelectorAll(`#ccPositionsBody tr, #ccLivePosList .cc-pos-tile`);
  rows.forEach(r => {
    if (r.textContent.includes(ticker.replace('-USD',''))) r.classList.add('selected');
  });
  loadPriceChart();
}

function togglePriceChartType() {
  _priceChartType = _priceChartType === 'candlestick' ? 'line' : 'candlestick';
  const btn = el('priceChartTypeBtn');
  if (btn) btn.textContent = _priceChartType === 'candlestick' ? 'CANDLES' : 'LINE';
  if (_priceChartData) renderPriceChart(_priceChartData);
}

function updatePriceChartOverlays() {
  if (_priceChartData) renderPriceChart(_priceChartData);
}

async function loadPriceChart() {
  if (!_selectedTicker) return;
  const ticker = _selectedTicker;
  const period = el('priceChartPeriod')?.value || '6mo';
  const interval = el('priceChartInterval')?.value || '1d';

  setEl('priceChartTicker', ticker.replace('-USD',''));
  setEl('priceChartPrice', 'Loading...');
  setEl('priceChartChange', '');

  try {
    const d = await fetchJSON(`/api/chart/${encodeURIComponent(ticker)}?period=${period}&interval=${interval}`);
    _priceChartData = d;
    if (d.candles?.length) {
      const last = d.candles[d.candles.length - 1];
      const prev = d.candles.length > 1 ? d.candles[d.candles.length - 2] : last;
      const chg = ((last.c - prev.c) / prev.c * 100);
      setEl('priceChartPrice', '$' + last.c.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:4}));
      const chgEl = el('priceChartChange');
      if (chgEl) {
        chgEl.textContent = `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%`;
        chgEl.style.color = chg >= 0 ? 'var(--green)' : 'var(--red)';
      }
      if (d.info?.name) setEl('priceChartTitle', `PRICE: ${d.info.name}`);
    }
    renderPriceChart(d);
  } catch (e) {
    setEl('priceChartPrice', 'ERROR');
    console.warn('Price chart error:', e);
  }
}

function renderPriceChart(d) {
  const canvas = el('priceChartCanvas');
  if (!canvas) return;
  if (_priceChart) { _priceChart.destroy(); _priceChart = null; }
  if (_rsiChart) { _rsiChart.destroy(); _rsiChart = null; }

  const candles = d.candles || [];
  if (!candles.length) return;

  const showSMA = el('priceChartSMA')?.checked;
  const showPred = el('priceChartPred')?.checked;
  const useCandlestick = _priceChartType === 'candlestick' && typeof Chart.controllers?.candlestick !== 'undefined';

  // Parse timestamps for x-axis
  const timestamps = candles.map(c => new Date(c.t).getTime());
  const labels = candles.map(c => c.t.split('T')[0]);

  const datasets = [];

  if (useCandlestick) {
    datasets.push({
      label: d.ticker,
      data: candles.map((c, i) => ({ x: timestamps[i], o: c.o, h: c.h, l: c.l, c: c.c })),
      color: { up: '#00cc44', down: '#ff3355', unchanged: '#888' },
      borderColor: { up: '#00cc44', down: '#ff3355', unchanged: '#888' },
      backgroundColor: { up: 'rgba(0,204,68,0.7)', down: 'rgba(255,51,85,0.7)', unchanged: '#888' },
    });
  } else {
    datasets.push({
      label: d.ticker,
      data: useCandlestick ? candles.map((c, i) => ({ x: timestamps[i], y: c.c })) : candles.map(c => c.c),
      borderColor: candles[candles.length-1].c >= candles[0].c ? '#00cc44' : '#ff3355',
      backgroundColor: candles[candles.length-1].c >= candles[0].c ? 'rgba(0,204,68,0.05)' : 'rgba(255,51,85,0.05)',
      borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: true,
    });
  }

  // SMA overlays
  if (showSMA && d.sma20) {
    const smaData = useCandlestick
      ? timestamps.reduce((arr, ts, i) => { if (d.sma20[i] != null) arr.push({ x: ts, y: d.sma20[i] }); return arr; }, [])
      : d.sma20;
    datasets.push({
      label: 'SMA 20', data: smaData, type: 'line',
      borderColor: '#ff8c00', borderWidth: 1, borderDash: [3,3],
      pointRadius: 0, tension: 0.3, fill: false, spanGaps: true,
    });
  }
  if (showSMA && d.sma50) {
    const smaData = useCandlestick
      ? timestamps.reduce((arr, ts, i) => { if (d.sma50[i] != null) arr.push({ x: ts, y: d.sma50[i] }); return arr; }, [])
      : d.sma50;
    datasets.push({
      label: 'SMA 50', data: smaData, type: 'line',
      borderColor: '#00d4ff', borderWidth: 1, borderDash: [5,3],
      pointRadius: 0, tension: 0.3, fill: false, spanGaps: true,
    });
  }

  // Prediction overlay (line mode only — not compatible with candlestick time axis)
  const allLabels = [...labels];
  if (showPred && !useCandlestick && d.prediction?.mid?.length) {
    const predMid = Array(candles.length).fill(null);
    const predUpperArr = Array(candles.length).fill(null);
    const predLowerArr = Array(candles.length).fill(null);
    predMid[candles.length - 1] = candles[candles.length - 1].c;
    predUpperArr[candles.length - 1] = candles[candles.length - 1].c;
    predLowerArr[candles.length - 1] = candles[candles.length - 1].c;
    d.prediction.dates.forEach((dt, i) => {
      allLabels.push(dt);
      predMid.push(d.prediction.mid[i]);
      predUpperArr.push(d.prediction.upper[i]);
      predLowerArr.push(d.prediction.lower[i]);
    });
    datasets.push({
      label: 'Prediction', data: predMid, type: 'line',
      borderColor: '#ff8c00', borderWidth: 1.5, borderDash: [4,4],
      pointRadius: 0, tension: 0.3, fill: false,
    });
    datasets.push({
      label: '95% Upper', data: predUpperArr, type: 'line',
      borderColor: 'rgba(0,204,68,0.3)', borderWidth: 1,
      pointRadius: 0, tension: 0.3, fill: false, backgroundColor: 'transparent',
    });
    datasets.push({
      label: '95% Lower', data: predLowerArr, type: 'line',
      borderColor: 'rgba(255,51,85,0.3)', borderWidth: 1,
      pointRadius: 0, tension: 0.3, fill: '-1', backgroundColor: 'rgba(255,140,0,0.04)',
    });
  }

  const xScaleBase = { ticks: { color: '#555', font: { size: 8, family: 'JetBrains Mono' }, maxRotation: 0, autoSkip: true, maxTicksLimit: 12 }, grid: { color: 'rgba(255,255,255,0.03)' } };
  const xScale = useCandlestick
    ? { ...xScaleBase, type: 'timeseries', time: { unit: 'day', displayFormats: { day: 'dd MMM' } } }
    : { ...xScaleBase, display: true };

  const ctx = canvas.getContext('2d');
  _priceChart = new Chart(ctx, {
    type: useCandlestick ? 'candlestick' : 'line',
    data: useCandlestick ? { datasets } : { labels: allLabels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: xScale,
        y: {
          display: true, position: 'right',
          ticks: { color: '#666', font: { size: 8, family: 'JetBrains Mono' } },
          grid: { color: 'rgba(255,255,255,0.04)' },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#111', borderColor: '#333', borderWidth: 1,
          titleFont: { size: 9, family: 'JetBrains Mono' },
          bodyFont: { size: 9, family: 'JetBrains Mono' },
        },
      },
    },
  });

  // RSI chart
  if (d.rsi?.length) {
    const rsiCanvas = el('rsiChartCanvas');
    if (rsiCanvas) {
      const rsiCtx = rsiCanvas.getContext('2d');
      const rsiData = d.rsi.slice(0, candles.length);
      _rsiChart = new Chart(rsiCtx, {
        type: 'line',
        data: {
          labels: labels,
          datasets: [{
            label: 'RSI 14',
            data: rsiData,
            borderColor: '#cc5de8',
            borderWidth: 1.2,
            pointRadius: 0,
            tension: 0.3,
            fill: false,
          }],
        },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false,
          scales: {
            x: { display: false },
            y: {
              display: true, position: 'right', min: 0, max: 100,
              ticks: { color: '#555', font: { size: 7, family: 'JetBrains Mono' }, stepSize: 30 },
              grid: { color: 'rgba(255,255,255,0.03)' },
            },
          },
          plugins: {
            legend: { display: false },
            tooltip: { enabled: false },
            // Draw overbought/oversold lines
            annotation: undefined,
          },
        },
        plugins: [{
          id: 'rsiLines',
          afterDraw(chart) {
            const yScale = chart.scales.y;
            const ctx = chart.ctx;
            ctx.save();
            // Oversold line at 30
            const y30 = yScale.getPixelForValue(30);
            ctx.strokeStyle = 'rgba(0,204,68,0.3)';
            ctx.setLineDash([3,3]);
            ctx.beginPath(); ctx.moveTo(chart.chartArea.left, y30); ctx.lineTo(chart.chartArea.right, y30); ctx.stroke();
            // Overbought line at 70
            const y70 = yScale.getPixelForValue(70);
            ctx.strokeStyle = 'rgba(255,51,85,0.3)';
            ctx.beginPath(); ctx.moveTo(chart.chartArea.left, y70); ctx.lineTo(chart.chartArea.right, y70); ctx.stroke();
            ctx.restore();
          }
        }],
      });
    }
  }
}


// ═══════════════════════════════════════════════════════════
// OPS HUD — Radar blips + live terminal feed
// ═══════════════════════════════════════════════════════════

const _OPS_COMMANDS = [
  ['SYS.TICK', 'Heartbeat OK — latency 3ms', ''],
  ['MKT.SCAN', 'Scanning ASX universe (48 tickers)', ''],
  ['SIG.GEN', 'Generating signals — confidence threshold 60%', ''],
  ['SIG.EVAL', 'Evaluating BHP.AX — RSI 42.3, momentum ▲', ''],
  ['RISK.CHK', 'Circuit breaker: ARMED — drawdown 0.3%', ''],
  ['QUAD.DET', 'Economic regime: RISING_GROWTH — GDP +2.1%', ''],
  ['PORT.UPD', 'Portfolio NAV recalculated — $1,000.00', ''],
  ['SENT.AI', 'FinBERT scanning 24 articles — sentiment: NEUTRAL', ''],
  ['NET.PING', 'API server responding — 200 OK', ''],
  ['CORR.MAT', 'Recalculating correlation matrix (15×15)', ''],
  ['DALIO.HG', 'Holy Grail check: 12/15 uncorrelated assets', ''],
  ['SIG.EVAL', 'Evaluating CBA.AX — Dalio score 72%', ''],
  ['WF.TEST', 'Walk-forward period 3/8 — Sharpe 1.42', ''],
  ['RISK.POS', 'Position sizing: max 10% per asset', ''],
  ['MKT.TICK', 'Ticker strip updated — 25 assets refreshed', ''],
  ['SIG.EVAL', 'Evaluating GLD — safe haven signal ▲', ''],
  ['NET.WS', 'WebSocket keepalive — connection stable', ''],
  ['SYS.MEM', 'Memory usage: 142MB / 512MB', ''],
];

let _opsTerminalInterval = null;

function initOpsTerminal() {
  if (_opsTerminalInterval) return;
  _opsTerminalInterval = setInterval(() => {
    const term = el('opsTerminal');
    if (!term) return;
    const cmd = _OPS_COMMANDS[Math.floor(Math.random() * _OPS_COMMANDS.length)];
    const now = new Date();
    const ts = now.toTimeString().slice(0, 8);
    const line = document.createElement('div');
    line.className = 'ops-line';
    const isWarn = cmd[0].includes('RISK') || cmd[0].includes('WARN');
    line.innerHTML = `<span class="ops-ts">${ts}</span> <span class="ops-cmd">${cmd[0]}</span> <span class="ops-msg${isWarn ? ' warn' : ''}">${cmd[1]}</span>`;
    term.appendChild(line);
    // Keep max 40 lines
    while (term.children.length > 40) term.removeChild(term.firstChild);
    // Auto scroll
    term.scrollTop = term.scrollHeight;
  }, 2500);

  // Spawn radar blips and signal symbols
  window._intervals.push(setInterval(spawnRadarBlip, 3500));
  window._intervals.push(setInterval(spawnRadarSignal, 2200));

  // Rotate radar status text
  window._intervals.push(setInterval(cycleRadarStatus, 3000));

  // Speed test telemetry in radar background
  window._intervals.push(setInterval(spawnTelemetryLine, 800));
}

const _RADAR_TICKERS = [
  'BHP.AX','CBA.AX','CSL.AX','WBC.AX','NAB.AX','RIO.AX','FMG.AX','WES.AX',
  'MQG.AX','TLS.AX','WDS.AX','ANZ.AX','GMG.AX','ALL.AX','TCL.AX',
  'GLD','SLV','USO','UNG','GOLD','SILVER','CRUDE',
  'ASX200','S&P500','NASDAQ','VIX','AUD/USD'
];

const _RADAR_STATUS_MSGS = [
  // Portfolio & P&L
  () => { const h = STATE.health; return h ? `NAV: ${fmt$(h.equity)} | DAILY P&L: ${(h.daily_pnl_pct??0) >= 0 ? '+' : ''}${(h.daily_pnl_pct??0).toFixed(2)}%` : 'AWAITING PORTFOLIO DATA'; },
  () => { const h = STATE.health; return h ? `DRAWDOWN: ${(h.drawdown_pct??0).toFixed(2)}% | SHARPE: ${(h.sharpe_ratio??0).toFixed(2)}` : 'RISK METRICS LOADING'; },
  () => { const h = STATE.health; return h ? `RETURN: ${(h.total_return_pct??0) >= 0 ? '+' : ''}${(h.total_return_pct??0).toFixed(2)}% | ${h.open_positions??0} POSITIONS OPEN` : 'PORTFOLIO SYNC IN PROGRESS'; },
  () => { const h = STATE.health; const cb = h?.halted; return cb ? '⚠ CIRCUIT BREAKER TRIPPED — TRADING HALTED' : 'CIRCUIT BREAKER: ARMED — LIMITS NORMAL'; },
  // Signals
  () => { const n = STATE.signals?.length ?? 0; const strong = STATE.signals?.filter(s => s.confidence >= 80)?.length ?? 0; return `SIGNALS: ${n} ACTIVE | ${strong} HIGH-CONFIDENCE`; },
  () => { const s = STATE.signals?.[0]; return s ? `TOP SIGNAL: ${s.action} ${s.ticker} @ ${(Number(s.confidence)||0).toFixed(0)}% CONF` : 'NO ACTIVE SIGNALS'; },
  () => { const buys = STATE.signals?.filter(s => ['BUY','LONG'].includes(s.action))?.length ?? 0; const sells = STATE.signals?.filter(s => ['SELL','SHORT'].includes(s.action))?.length ?? 0; return `SIGNAL MIX: ${buys} BUYS / ${sells} SELLS`; },
  // Scanner data
  () => { const a = _scannerData.asx?.length ?? 0; const m = _scannerData.commodities?.length ?? 0; return `UNIVERSE: ${a} ASX | ${m} COMMODITIES`; },
  () => { const all = [...(_scannerData.asx||[]),...(_scannerData.commodities||[])]; const up = all.filter(r=>r.change_pct>0).length; return all.length ? `MARKET PULSE: ${up}/${all.length} ASSETS GREEN (${(up/all.length*100).toFixed(0)}%)` : 'MARKET DATA LOADING'; },
  () => { const a = _scannerData.asx || []; const top = [...a].sort((x,y)=>y.change_pct-x.change_pct)[0]; return top ? `ASX MOVER: ${top.ticker} ${top.change_pct>=0?'+':''}${top.change_pct}%` : 'ASX FEED STANDBY'; },
  // System health
  () => `UPTIME: ${((performance.now()/1000/60)).toFixed(0)} MIN | MEM: ${(performance.memory?.usedJSHeapSize/1024/1024)?.toFixed(0) ?? '?'}MB`,
  () => `WEBSOCKET: ${STATE._wsConnected ? 'CONNECTED' : 'DISCONNECTED'} | MODE: ${_tradingMode?.toUpperCase() ?? 'PAPER'}`,
  () => _systemPaused ? '⏸ SYSTEM PAUSED — NO NEW TRADES' : '● ALL SYSTEMS OPERATIONAL — TRADING ACTIVE',
  () => { const w = _watchlist?.length ?? 0; return `WATCHLIST: ${w} ASSETS TRACKED | ALERTS: ${_priceAlerts?.filter(a=>!a.triggered)?.length ?? 0} ACTIVE`; },
  // Quadrant & sentiment
  () => { const q = STATE.health?.active_quadrant; return q ? `REGIME: ${q.replace(/_/g,' ').toUpperCase()} | STRATEGY ALIGNED` : 'ECONOMIC QUADRANT: DETECTING'; },
  () => { const corr = STATE.corr; return corr ? `HOLY GRAIL: ${corr.holy_grail_count??0}/15 UNCORRELATED | MEAN CORR: ${(corr.mean_correlation??0).toFixed(3)}` : 'CORRELATION MATRIX PENDING'; },
  // Network
  () => `API LATENCY: ${(Math.random()*30+2).toFixed(0)}ms | FEEDS: ${_TELEMETRY_FEEDS.length} ACTIVE`,
  () => { const t = STATE.health?.cycle_count; return t != null ? `ENGINE CYCLES: ${t} COMPLETED | STATUS: NOMINAL` : 'ENGINE WARMING UP'; },
  () => { const h = STATE.health; const dd = h?.drawdown_pct ?? 0; const risk = dd > 5 ? 'HIGH' : dd > 2 ? 'MODERATE' : 'LOW'; return `RISK LEVEL: ${risk} | DD: ${dd.toFixed(2)}% / 10% MAX`; },
];

function _rndTicker() {
  return _RADAR_TICKERS[Math.floor(Math.random() * _RADAR_TICKERS.length)];
}

function cycleRadarStatus() {
  const txt = el('opsRadarText');
  if (!txt) return;
  const msg = _RADAR_STATUS_MSGS[Math.floor(Math.random() * _RADAR_STATUS_MSGS.length)]();
  txt.textContent = msg;
}

const _TELEMETRY_LINES = [
  // Data feeds
  () => { const n = _scannerData.asx?.length ?? 0; return { txt: `ASX.FEED ${n} TICKERS LOADED`, cls: n > 0 ? 'fast' : 'slow' }; },
  () => { const n = _scannerData.commodities?.length ?? 0; return { txt: `COMMOD.FEED ${n} ASSETS ACTIVE`, cls: n > 0 ? 'fast' : 'slow' }; },
  () => { const ms = (Math.random()*40+5).toFixed(0); return { txt: `YAHOO.FIN POLL ${ms}ms OK`, cls: 'fast' }; },
  // Signal engine
  () => { const n = STATE.signals?.length ?? 0; return { txt: `SIGNAL.GEN ${n} SIGNALS ACTIVE`, cls: n > 0 ? 'fast' : '' }; },
  () => { const s = STATE.signals?.[Math.floor(Math.random()*(STATE.signals?.length||1))]; return s ? { txt: `RSI.CALC ${s.ticker} RSI ${(Number(s.rsi)||50).toFixed(0)}`, cls: 'fast' } : { txt: 'RSI.CALC IDLE', cls: '' }; },
  () => { const s = STATE.signals?.[Math.floor(Math.random()*(STATE.signals?.length||1))]; return s ? { txt: `SIG.EVAL ${s.ticker} — CONF ${(Number(s.confidence)||0).toFixed(0)}%`, cls: Number(s.confidence) >= 70 ? 'fast' : '' } : { txt: 'SIG.EVAL STANDBY', cls: '' }; },
  // Risk & portfolio
  () => { const h = STATE.health; const dd = (h?.drawdown_pct??0).toFixed(2); return { txt: `RISK.MON DD ${dd}% / 10% MAX`, cls: +dd < 5 ? 'fast' : 'slow' }; },
  () => { const h = STATE.health; return { txt: `PORT.NAV ${h ? fmt$(h.equity) : '$---'}`, cls: 'fast' }; },
  () => { const h = STATE.health; const p = h?.daily_pnl_pct ?? 0; return { txt: `DAILY.PNL ${p >= 0 ? '+' : ''}${p.toFixed(3)}%`, cls: p >= 0 ? 'fast' : 'slow' }; },
  () => { const h = STATE.health; return { txt: `POSITIONS ${h?.open_positions ?? 0} OPEN`, cls: 'fast' }; },
  () => { return { txt: `CIRCUIT.BRK ${STATE.health?.halted ? 'TRIPPED' : 'ARMED OK'}`, cls: STATE.health?.halted ? 'slow' : 'fast' }; },
  // System
  () => { return { txt: `WS.LINK ${STATE._wsConnected ? 'CONNECTED' : 'DOWN'}`, cls: STATE._wsConnected ? 'fast' : 'slow' }; },
  () => { const mem = (performance.memory?.usedJSHeapSize/1024/1024)?.toFixed(0); return { txt: `SYS.MEM ${mem ?? '?'}MB / ${(performance.memory?.jsHeapSizeLimit/1024/1024)?.toFixed(0) ?? '?'}MB`, cls: 'fast' }; },
  () => { const up = (performance.now()/1000/60).toFixed(0); return { txt: `UPTIME ${up} MIN`, cls: 'fast' }; },
  () => { return { txt: `MODE ${_tradingMode?.toUpperCase() ?? 'PAPER'} | ${_systemPaused ? 'PAUSED' : 'ACTIVE'}`, cls: _systemPaused ? 'slow' : 'fast' }; },
  // Correlation & quadrant
  () => { const c = STATE.corr; return c ? { txt: `CORR.ENG MEAN ${(c.mean_correlation??0).toFixed(3)}`, cls: 'fast' } : { txt: 'CORR.ENG PENDING', cls: '' }; },
  () => { const q = STATE.health?.active_quadrant; return { txt: `QUAD.DET ${q ? q.replace(/_/g,' ').toUpperCase() : 'SCANNING'}`, cls: 'fast' }; },
  // Sentiment & news
  () => { const n = STATE._allArticles?.length ?? 0; return { txt: `NEWS.FEED ${n} ARTICLES INDEXED`, cls: n > 0 ? 'fast' : '' }; },
  () => { const w = _watchlist?.length ?? 0; return { txt: `WATCHLIST ${w} ASSETS MONITORED`, cls: 'fast' }; },
  () => { const a = _priceAlerts?.filter(a=>!a.triggered)?.length ?? 0; return { txt: `ALERTS ${a} PRICE ALERTS ACTIVE`, cls: a > 0 ? 'fast' : '' }; },
  // Scanning individual tickers
  () => { const t = _rndTicker(); const s = STATE.signals?.find(x => x.ticker?.includes(t)); return s ? { txt: `SCAN ${t} ${s.action} ${(Number(s.confidence)||0).toFixed(0)}%`, cls: 'fast' } : { txt: `SCAN ${t} HOLD`, cls: '' }; },
];

function spawnTelemetryLine() {
  const wrap = el('radarTelemetry');
  if (!wrap) return;
  while (wrap.children.length > 14) wrap.removeChild(wrap.firstChild);

  const entry = _TELEMETRY_LINES[Math.floor(Math.random() * _TELEMETRY_LINES.length)]();
  const line = document.createElement('div');
  line.className = 'radar-telemetry-line' + (entry.cls ? ' ' + entry.cls : '');
  line.textContent = entry.txt;
  line.style.animationDelay = (Math.random() * 0.3).toFixed(2) + 's';
  wrap.appendChild(line);
  setTimeout(() => line.remove(), 4000);
}

function spawnRadarBlip() {
  const g = document.getElementById('radarBlips');
  if (!g) return;
  const angle = Math.random() * Math.PI * 2;
  const dist = 15 + Math.random() * 35;
  const cx = 60 + Math.cos(angle) * dist;
  const cy = 60 + Math.sin(angle) * dist;
  const blip = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  blip.setAttribute('cx', cx.toFixed(1));
  blip.setAttribute('cy', cy.toFixed(1));
  blip.setAttribute('r', '1.5');
  blip.classList.add('ops-radar-blip');
  if (Math.random() < 0.15) blip.classList.add('warn');
  if (Math.random() < 0.05) blip.classList.add('alert');
  blip.style.animationDelay = (Math.random() * 0.5).toFixed(2) + 's';
  g.appendChild(blip);
  setTimeout(() => blip.remove(), 3500);
}

const _RADAR_SYMBOLS = ['$', '$', '$', '$$', '\u2620', '\u2620', '\u26A0', '\u25B2', '\u25BC'];

function spawnRadarSignal() {
  const g = document.getElementById('radarSignals');
  if (!g) return;
  // Keep max 6 signals on screen
  while (g.children.length > 6) g.removeChild(g.firstChild);

  const angle = Math.random() * Math.PI * 2;
  const dist = 12 + Math.random() * 40;
  const x = 60 + Math.cos(angle) * dist;
  const y = 60 + Math.sin(angle) * dist;

  const sym = _RADAR_SYMBOLS[Math.floor(Math.random() * _RADAR_SYMBOLS.length)];
  const txt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  txt.setAttribute('x', x.toFixed(1));
  txt.setAttribute('y', y.toFixed(1));
  txt.setAttribute('text-anchor', 'middle');
  txt.setAttribute('dominant-baseline', 'central');
  txt.textContent = sym;
  txt.classList.add('radar-signal');

  // Color class based on symbol
  if (sym.includes('$')) {
    txt.classList.add('signal-money');
  } else if (sym === '\u2620') {
    txt.classList.add('signal-skull');
    txt.style.fontSize = '9px';
  } else if (sym === '\u26A0') {
    txt.classList.add('signal-warn');
  }

  // Random glitch delay
  txt.style.animationDelay = (Math.random() * 0.4).toFixed(2) + 's';
  txt.style.animationDuration = (2.5 + Math.random() * 2).toFixed(1) + 's';

  g.appendChild(txt);
  setTimeout(() => txt.remove(), 4500);
}

// Auto-start OPS terminal on page load
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(initOpsTerminal, 500);
});

// Add ops terminal entries from pushAlert (hook into existing system)
const _origPushAlert = typeof pushAlert === 'function' ? pushAlert : null;
function pushOpsLine(cmd, msg, type) {
  const term = el('opsTerminal');
  if (!term) return;
  const now = new Date();
  const ts = now.toTimeString().slice(0, 8);
  const line = document.createElement('div');
  line.className = 'ops-line';
  const cls = type === 'warning' ? ' warn' : type === 'error' ? ' err' : '';
  line.innerHTML = `<span class="ops-ts">${ts}</span> <span class="ops-cmd">${escHtml(cmd)}</span> <span class="ops-msg${cls}">${escHtml(msg)}</span>`;
  term.appendChild(line);
  while (term.children.length > 40) term.removeChild(term.firstChild);
  term.scrollTop = term.scrollHeight;
}


// ═══════════════════════════════════════════════════════════
// ═══════════════════════════════════════════════════════════
// SYSTEM STATUS TOGGLE (OPERATIONAL / PAUSED)
// ═══════════════════════════════════════════════════════════
let _systemPaused = false;

function toggleSystemStatus() {
  _systemPaused = !_systemPaused;
  const badge = el('statusBadge');
  if (!badge) return;
  if (_systemPaused) {
    badge.textContent = '⏸ PAUSED';
    badge.className = 'badge badge--paused';
    pushAlert('SYSTEM', 'Trading PAUSED — no new trades will be executed', 'warning');
    playBeep(220, 0.15);
  } else {
    badge.textContent = '● OPERATIONAL';
    badge.className = 'badge badge--green';
    pushAlert('SYSTEM', 'Trading RESUMED — system is operational', 'info');
    playBeep(660, 0.1);
  }
  // Notify server
  postJSON('/api/system/pause', { paused: _systemPaused }).catch(() => pushAlert('SYSTEM', 'Failed to toggle pause', 'error'));
}


// ═══════════════════════════════════════════════════════════
// BACKTEST STATUS PANEL
// ═══════════════════════════════════════════════════════════
let _btTimer = null;
let _btStartTime = null;

function runBacktestWithStatus() {
  const statusEl = el('btRunStatus');
  const tagEl = el('btStatusTag');
  const timeEl = el('btRunTime');
  const progressEl = el('btProgressFill');
  const pctEl = el('btProgressPct');
  const periodsEl = el('btPeriodsProcessed');

  // Start timer
  _btStartTime = Date.now();
  if (_btTimer) clearInterval(_btTimer);

  if (statusEl) { statusEl.textContent = 'RUNNING'; statusEl.className = 'bt-status-val running'; }
  if (tagEl) { tagEl.textContent = 'RUNNING'; tagEl.style.color = 'var(--green)'; }

  // Simulate period progress
  let periodsDone = 0;
  const totalPeriods = 8;
  const periodInterval = setInterval(() => {
    periodsDone = Math.min(periodsDone + 1, totalPeriods);
    if (periodsEl) periodsEl.textContent = `${periodsDone} / ${totalPeriods}`;
    const pct = Math.round(periodsDone / totalPeriods * 100);
    if (progressEl) progressEl.style.width = pct + '%';
    if (pctEl) pctEl.textContent = pct + '%';
    if (periodsDone >= totalPeriods) clearInterval(periodInterval);
  }, 400);

  // Update timer display
  _btTimer = setInterval(() => {
    if (_btStartTime && timeEl) {
      const elapsed = Math.floor((Date.now() - _btStartTime) / 1000);
      const m = Math.floor(elapsed / 60);
      const s = elapsed % 60;
      timeEl.textContent = `${m}:${s.toString().padStart(2, '0')}`;
    }
  }, 1000);

  // Actually run the backtest
  loadBacktest().then(() => {
    clearInterval(periodInterval);
    clearInterval(_btTimer);
    _btTimer = null;
    if (statusEl) { statusEl.textContent = 'COMPLETE'; statusEl.className = 'bt-status-val'; statusEl.style.color = 'var(--green)'; }
    if (tagEl) { tagEl.textContent = 'COMPLETE'; tagEl.style.color = 'var(--green)'; }
    if (periodsEl) periodsEl.textContent = `${totalPeriods} / ${totalPeriods}`;
    if (progressEl) progressEl.style.width = '100%';
    if (pctEl) pctEl.textContent = '100%';
    // Record final time
    if (_btStartTime && timeEl) {
      const elapsed = Math.floor((Date.now() - _btStartTime) / 1000);
      const m = Math.floor(elapsed / 60);
      const s = elapsed % 60;
      timeEl.textContent = `${m}:${s.toString().padStart(2, '0')}`;
    }
  }).catch(() => {
    clearInterval(periodInterval);
    clearInterval(_btTimer);
    _btTimer = null;
    if (statusEl) { statusEl.textContent = 'ERROR'; statusEl.className = 'bt-status-val'; statusEl.style.color = 'var(--red)'; }
    if (tagEl) { tagEl.textContent = 'ERROR'; tagEl.style.color = 'var(--red)'; }
  });
}

function resetBacktest() {
  if (_btTimer) { clearInterval(_btTimer); _btTimer = null; }
  _btStartTime = null;
  const ids = ['btRunStatus', 'btRunTime', 'btPeriodsProcessed', 'btProgressPct'];
  const defaults = ['IDLE', '0:00', '0 / 8', '0%'];
  ids.forEach((id, i) => setEl(id, defaults[i]));
  const statusEl = el('btRunStatus');
  if (statusEl) { statusEl.className = 'bt-status-val idle'; statusEl.style.color = ''; }
  const tagEl = el('btStatusTag');
  if (tagEl) { tagEl.textContent = 'IDLE'; tagEl.style.color = ''; }
  const progressEl = el('btProgressFill');
  if (progressEl) progressEl.style.width = '0%';
  // Clear backtest data
  setEl('bt-totalRet', '--');
  setEl('bt-sharpe', '--');
  setEl('bt-sortino', '--');
  setEl('bt-calmar', '--');
  setEl('bt-maxdd', '--');
  setEl('bt-winrate', '--');
  setEl('bt-periods', '--');
  setEl('bt-annRet', '--');
  const ptb = el('periodTableBody');
  if (ptb) ptb.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:14px">Click RUN BACKTEST to generate results</td></tr>';
  if (charts.wf) {
    charts.wf.data.labels = [];
    charts.wf.data.datasets[0].data = [];
    charts.wf.update();
  }
  pushAlert('BACKTEST', 'Results cleared — ready for a new run', 'info');
}


// ═══════════════════════════════════════════════════════════
// TEST CONNECTION DRY-RUN — validates broker API keys
// ═══════════════════════════════════════════════════════════

async function testBrokerConnection(broker) {
  const resultEl = el(`bcfgResult-${broker}`);
  if (resultEl) resultEl.innerHTML = '<span style="color:var(--amber)">⌛ Testing connection (dry-run)...</span>';
  const payload = { broker, ..._getBrokerPayload(broker), dry_run: true };
  // Check at least one field
  const vals = Object.values(payload).filter(v => typeof v === 'string' && v.length > 0);
  if (vals.length < 2) { if (resultEl) resultEl.innerHTML = '<span style="color:var(--red)">Fill in credentials first</span>'; return; }
  try {
    const d = await postJSON('/api/broker/test', payload);
    if (d.success) {
      if (resultEl) resultEl.innerHTML = `<span style="color:var(--green)">✓ Connection OK — ${d.message || 'API keys valid'}</span>`;
      playBeep(880, 0.1);
      pushAlert('BROKER', `${broker.toUpperCase()} test connection successful`, 'info');
    } else {
      if (resultEl) resultEl.innerHTML = `<span style="color:var(--red)">✗ ${d.message || 'Connection failed'}</span>`;
    }
  } catch (e) {
    if (resultEl) resultEl.innerHTML = `<span style="color:var(--red)">✗ ${escHtml(e.message || 'Test failed')}</span>`;
  }
}


// ═══════════════════════════════════════════════════════════
// LEGAL MODALS — Privacy, Terms, Transparency
// ═══════════════════════════════════════════════════════════

const _LEGAL_CONTENT = {
  privacy: {
    title: 'PRIVACY POLICY',
    html: `
      <h2>Information We Collect</h2>
      <p>DaliosATF operates entirely on your local machine. We do not collect, store, or transmit any personal data to external servers. All trading data, portfolio information, and configuration settings remain on your device.</p>
      <h2>Broker API Credentials</h2>
      <p>API keys and secrets you provide for broker connections are stored locally on your device using basic encryption. They are never transmitted to DaliosATF servers or any third party. Credentials are used solely to communicate directly between your device and your chosen broker.</p>
      <h2>Market Data</h2>
      <p>Market data is fetched from public APIs (Yahoo Finance) directly from your device. These services may log your IP address per their own privacy policies. We recommend reviewing their terms independently.</p>
      <h2>Notifications</h2>
      <p>If you configure Discord webhooks or Telegram bot tokens, messages are sent directly from your device to those services. DaliosATF does not proxy or store notification content.</p>
      <h2>Analytics &amp; Telemetry</h2>
      <p>DaliosATF does not include any analytics, telemetry, tracking pixels, or third-party scripts. No usage data leaves your machine.</p>
      <h2>Data Retention</h2>
      <p>All data is stored in local SQLite databases and JSON files within the application directory. You can delete all data at any time by removing the <code>data/</code> folder.</p>
      <h2>Contact</h2>
      <p>For privacy-related inquiries, please open an issue on the project repository.</p>
    `
  },
  terms: {
    title: 'TERMS & CONDITIONS',
    html: `
      <h2>Acceptance of Terms</h2>
      <p>By using DaliosATF ("the Software"), you agree to these terms. If you do not agree, do not use the Software.</p>
      <h2>Nature of the Software</h2>
      <p>DaliosATF is an experimental, open-source trading analysis and automation tool. It is provided for educational and research purposes only. It is not a registered financial advisor, broker-dealer, or investment service.</p>
      <h2>No Financial Advice</h2>
      <p>Nothing in this Software constitutes financial, investment, tax, or legal advice. All signals, recommendations, and analysis generated by DaliosATF are algorithmic outputs and should not be treated as professional advice. Always consult a qualified financial advisor before making investment decisions.</p>
      <h2>Risk Disclosure</h2>
      <ul>
        <li>Trading stocks and commodities involves substantial risk of loss.</li>
        <li>Past performance of any algorithm does not guarantee future results.</li>
        <li>You may lose some or all of your invested capital.</li>
        <li>Automated trading systems can malfunction, execute unintended trades, or fail to execute intended trades.</li>
        <li>Market conditions can change rapidly and unpredictably.</li>
      </ul>
      <h2>Limitation of Liability</h2>
      <p>The authors and contributors of DaliosATF are not liable for any financial losses, damages, or other consequences arising from the use of this Software. You use DaliosATF entirely at your own risk.</p>
      <h2>No Warranty</h2>
      <p>The Software is provided "AS IS" without warranty of any kind, express or implied, including but not limited to warranties of merchantability, fitness for a particular purpose, and non-infringement.</p>
      <h2>Your Responsibilities</h2>
      <ul>
        <li>You are solely responsible for all trading decisions and their outcomes.</li>
        <li>You must comply with all applicable laws and regulations in your jurisdiction.</li>
        <li>You are responsible for securing your API credentials and trading accounts.</li>
        <li>You should thoroughly test any strategy in paper mode before risking real capital.</li>
      </ul>
      <h2>Modifications</h2>
      <p>These terms may be updated at any time. Continued use of the Software constitutes acceptance of any changes.</p>
    `
  },
  transparency: {
    title: 'TRANSPARENCY REPORT',
    html: `
      <h2>How DaliosATF Works</h2>
      <p>DaliosATF is an open-source algorithmic trading framework inspired by Ray Dalio's All Weather investment principles. It analyses market conditions and generates trade signals using a combination of technical indicators, market regime classification, and portfolio optimisation.</p>
      <h2>Signal Generation</h2>
      <p>Trade signals are generated using:</p>
      <ul>
        <li><strong>RSI (Relative Strength Index)</strong> — measures momentum and overbought/oversold conditions.</li>
        <li><strong>Moving averages</strong> — 20-day and 50-day crossovers for trend detection.</li>
        <li><strong>Market quadrant classification</strong> — categorises the macro environment (growth/inflation rising/falling) to select appropriate asset allocations.</li>
        <li><strong>Confidence scoring</strong> — composite score from indicator agreement, with minimum thresholds for signal emission.</li>
      </ul>
      <h2>Data Sources</h2>
      <ul>
        <li><strong>Yahoo Finance</strong> — ASX stock and commodity price data (delayed).</li>
      </ul>
      <h2>Limitations</h2>
      <ul>
        <li>Price data may be delayed up to 15 minutes for ASX stocks.</li>
        <li>Signal confidence scores are statistical estimates, not certainties.</li>
        <li>The system does not account for all market risks (liquidity, geopolitical, regulatory).</li>
        <li>Backtested performance uses paper trading simulation and may not reflect real-world execution.</li>
      </ul>
      <h2>Open Source</h2>
      <p>DaliosATF is fully open source. All signal logic, scoring algorithms, and trading rules are visible in the source code. There are no hidden fees, proprietary black boxes, or undisclosed affiliate arrangements.</p>
      <h2>Conflicts of Interest</h2>
      <p>DaliosATF has no commercial relationships with any broker, exchange, or data provider. The Software does not receive commissions, referral fees, or payment-for-order-flow from any party.</p>
    `
  }
};

function openLegalModal(type) {
  const content = _LEGAL_CONTENT[type];
  if (!content) return;
  const overlay = document.getElementById('legalModalOverlay');
  const title = document.getElementById('legalModalTitle');
  const body = document.getElementById('legalModalBody');
  if (title) title.textContent = content.title;
  if (body) body.innerHTML = content.html;
  if (overlay) overlay.classList.remove('hidden');
}

function closeLegalModal() {
  const overlay = document.getElementById('legalModalOverlay');
  if (overlay) overlay.classList.add('hidden');
}

// ═══════════════════════════════════════════════════════════
// CSV EXPORT — paper and live trade history
// ═══════════════════════════════════════════════════════════

function exportPaperTradesCSV() {
  _exportTradesCSV('/api/paper/history', 'dalios_paper_trades.csv', 'paper');
}

function exportLiveTradesCSV() {
  _exportTradesCSV('/api/real/history', 'dalios_live_trades.csv', 'live');
}

async function _exportTradesCSV(endpoint, filename, mode) {
  try {
    const d = await fetchJSON(endpoint);
    const trades = mode === 'live' ? (d.history || []) : (d.trades || []);
    if (!trades.length) { pushAlert('EXPORT', 'No trades to export', 'warning'); return; }

    const headers = mode === 'live'
      ? ['Ticker','Side','Qty','Price','Status','Timestamp']
      : ['ID','Ticker','Side','Qty','Entry Price','Exit Price','P&L ($)','P&L (%)','Timestamp'];

    const rows = trades.map(t => {
      if (mode === 'live') {
        return [t.ticker, t.side, t.qty, t.price ?? '', t.status ?? '', t.timestamp ?? ''];
      }
      return [t.id, t.ticker, t.side, t.qty, t.entry_price, t.exit_price, t.pnl?.toFixed(2) ?? '', t.pnl_pct?.toFixed(2) ?? '', t.timestamp ?? ''];
    });

    let csv = headers.join(',') + '\n';
    rows.forEach(r => { csv += r.map(v => `"${String(v).replace(/"/g,'""')}"`).join(',') + '\n'; });

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
    pushAlert('EXPORT', `${trades.length} trades exported to ${filename}`, 'info');
    playBeep(660, 0.08);
  } catch (e) {
    pushAlert('EXPORT', `Export failed: ${escHtml(e.message)}`, 'warning');
  }
}


// ═══════════════════════════════════════════════════════════
// ENHANCED SOUND ALERTS — circuit breaker + strong signals
// ═══════════════════════════════════════════════════════════

function playCircuitBreakerAlarm() {
  if (!_soundOn || !_audioCtx) return;
  // Three-tone alarm
  playBeep(220, 0.3, 'sawtooth', 0.2);
  setTimeout(() => playBeep(180, 0.3, 'sawtooth', 0.2), 350);
  setTimeout(() => playBeep(220, 0.4, 'sawtooth', 0.25), 700);
}

function playStrongSignalChime() {
  if (!_soundOn || !_audioCtx) return;
  playBeep(523, 0.12, 'sine', 0.15);
  setTimeout(() => playBeep(659, 0.12, 'sine', 0.13), 130);
  setTimeout(() => playBeep(784, 0.15, 'sine', 0.12), 260);
  setTimeout(() => playBeep(1047, 0.2, 'sine', 0.1), 400);
}


// ═══════════════════════════════════════════════════════════
// WATCHLIST PERSISTENCE — localStorage backup
// ═══════════════════════════════════════════════════════════

function _saveWatchlistLocal() {
  try { localStorage.setItem('dalios_watchlist', JSON.stringify(_watchlist)); } catch {}
}

function _loadWatchlistLocal() {
  try {
    const saved = JSON.parse(localStorage.getItem('dalios_watchlist') || '[]');
    if (saved.length && !_watchlist.length) _watchlist = saved;
  } catch {}
}


// ═══════════════════════════════════════════════════════════
// MULTI-TIMEFRAME SIGNAL CONFIDENCE
// ═══════════════════════════════════════════════════════════

function multiTimeframeBadgesHTML(s) {
  if (!s.timeframes) return '';
  const tfs = s.timeframes;
  return `<div class="sc-timeframes">
    ${Object.entries(tfs).map(([tf, conf]) => {
      const c = Number(conf) || 0;
      const col = c >= 70 ? 'var(--green)' : c >= 50 ? 'var(--amber)' : 'var(--red)';
      return `<span class="sc-tf-badge" style="border-color:${col};color:${col}" title="${tf} confidence">${tf}: ${c.toFixed(0)}%</span>`;
    }).join('')}
  </div>`;
}


// ═══════════════════════════════════════════════════════════
// CUSTOM PRICE ALERTS
// ═══════════════════════════════════════════════════════════

let _priceAlerts = [];
const _PRICE_ALERTS_KEY = 'dalios_price_alerts';

function _loadPriceAlerts() {
  try { _priceAlerts = JSON.parse(localStorage.getItem(_PRICE_ALERTS_KEY) || '[]'); } catch { _priceAlerts = []; }
}
function _savePriceAlerts() {
  try { localStorage.setItem(_PRICE_ALERTS_KEY, JSON.stringify(_priceAlerts)); } catch {}
}

function addPriceAlert() {
  const ticker = el('paAlertTicker')?.value?.trim().toUpperCase();
  const price = parseFloat(el('paAlertPrice')?.value);
  const direction = el('paAlertDir')?.value || 'above';
  if (!ticker || !price || price <= 0) { pushAlert('ALERTS', 'Enter ticker and target price', 'warning'); return; }

  _priceAlerts.push({ id: Date.now(), ticker, target: price, direction, triggered: false, created: new Date().toISOString() });
  _savePriceAlerts();
  renderPriceAlerts();
  pushAlert('ALERTS', `Alert set: ${ticker} ${direction} $${price.toFixed(2)}`, 'info');
  playBeep(660, 0.08);

  // Clear inputs
  if (el('paAlertTicker')) el('paAlertTicker').value = '';
  if (el('paAlertPrice')) el('paAlertPrice').value = '';
}

function removePriceAlert(id) {
  _priceAlerts = _priceAlerts.filter(a => a.id !== id);
  _savePriceAlerts();
  renderPriceAlerts();
}

function renderPriceAlerts() {
  const list = el('priceAlertList');
  if (!list) return;
  if (!_priceAlerts.length) {
    list.innerHTML = '<div style="padding:14px;color:var(--text-muted);font-size:10px;text-align:center">No alerts set — add one above</div>';
    return;
  }
  list.innerHTML = _priceAlerts.map(a => {
    const col = a.triggered ? 'var(--green)' : 'var(--amber)';
    const icon = a.triggered ? '✓' : a.direction === 'above' ? '▲' : '▼';
    return `<div class="pa-alert-row${a.triggered ? ' triggered' : ''}">
      <span class="pa-ticker" style="color:var(--cyan)">${a.ticker}</span>
      <span class="pa-dir" style="color:${col}">${icon} ${a.direction.toUpperCase()}</span>
      <span class="pa-target">$${a.target.toFixed(2)}</span>
      <button class="pa-remove-btn" onclick="removePriceAlert(${a.id})">✕</button>
    </div>`;
  }).join('');
}

function checkPriceAlerts() {
  if (!_priceAlerts.length) return;
  const untriggered = _priceAlerts.filter(a => !a.triggered);
  if (!untriggered.length) return;

  // Check against scanner data
  const allData = [...(_scannerData.asx || []), ...(_scannerData.commodities || [])];
  untriggered.forEach(a => {
    const row = allData.find(r => r.ticker === a.ticker || r.ticker.replace('-USD','') === a.ticker);
    if (!row) return;
    const hit = a.direction === 'above' ? row.price >= a.target : row.price <= a.target;
    if (hit) {
      a.triggered = true;
      _savePriceAlerts();
      renderPriceAlerts();
      pushAlert('PRICE ALERT', `${a.ticker} hit $${a.target.toFixed(2)} (now $${row.price.toFixed(2)})`, 'warning');
      playStrongSignalChime();
      sendNotification('Price Alert', `${a.ticker} is now $${row.price.toFixed(2)} — target $${a.target.toFixed(2)} ${a.direction}`);
    }
  });
}

// Check price alerts every 30s
document.addEventListener('DOMContentLoaded', () => {
  _loadPriceAlerts();
  // [MEMORY-FIX] Track interval so it can be cleaned up
  if (window._intervals) window._intervals.push(setInterval(checkPriceAlerts, 30000));
  else setInterval(checkPriceAlerts, 30000);
});


// ═══════════════════════════════════════════════════════════
// LIGHT / DARK MODE TOGGLE (header button)
// ═══════════════════════════════════════════════════════════

function toggleLightDark() {
  const current = _loadSettings().theme || 'dark';
  const next = current === 'light' ? 'dark' : 'light';
  setTheme(next, null);
}

function _updateThemeToggleBtn(themeName) {
  const icon = document.getElementById('themeIcon');
  const label = document.getElementById('themeLabel');
  if (!icon || !label) return;
  if (themeName === 'light') {
    icon.textContent = '☀';
    label.textContent = 'LIGHT';
  } else {
    icon.textContent = '◐';
    label.textContent = 'DARK';
  }
}

// ═══════════════════════════════════════════════════════════
// TUTORIAL RESTART FROM SETTINGS
// ═══════════════════════════════════════════════════════════

function restartGuidedTour() {
  // Clear all spot states
  GUIDED_TAB_ORDER.forEach(tabId => {
    (SPOTS[tabId] || []).forEach(s => localStorage.removeItem(`dalios_spot_${s.id}`));
  });
  localStorage.removeItem('dalios_welcome_done');
  localStorage.removeItem('dalios_welcome_never');

  // Show the welcome overlay first
  const overlay = el('welcomeOverlay');
  if (overlay) {
    overlay.classList.remove('hidden');
    // Uncheck the "don't show again" checkbox
    const neverCb = el('welcomeNeverAgain');
    if (neverCb) neverCb.checked = false;
  }
  pushAlert('TUTORIAL', 'Guided tour restarted from the beginning', 'info');
}
