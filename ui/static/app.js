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
  rising_growth:    { label: 'RISING GROWTH',    color: '#00ff88', icon: '▲', cssClass: '' },
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
  loadAll();
  loadMarketSummary();
  setInterval(loadAll, 30_000);           // Refresh all data every 30s
  setInterval(updateClock, 1000);
  setInterval(loadHealth, 10_000);        // Health every 10s
  setInterval(loadMarketSummary, 60_000); // Ticker strip every 60s
  setInterval(pollLivePnl, 15_000);       // Live P&L every 15s (global)
});

// ─── Tab Navigation ───────────────────────────────────────
function initTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.tab;
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(`tab-${id}`).classList.add('active');
      // Lazy-load tab data
      if (id === 'signal-ops')           initSignalOps();
      if (id === 'intel-center')         loadSentiment();
      if (id === 'holy-grail')           loadCorrelation();
      if (id === 'risk-matrix')          loadHealth();
      if (id === 'backtest-lab')         loadBacktest();
      if (id === 'paper-trading')        initPaperTrading();
      if (id === 'live-trading')         initLiveTrading();
      if (id === 'asx-scanner')         loadScanner('asx');
      if (id === 'crypto-scanner')      loadScanner('crypto');
      if (id === 'commodities-scanner') loadScanner('commodities');
      if (id === 'command-center')      initCommandCentre();
      if (id === 'comms-config')        initSettingsTab();
      // Show tutorial on first visit
      showTutorial(id);
    });
  });

  // Show speech bubbles for Command Center on first ever load
  setTimeout(() => showTutorial('command-center'), 1000);
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
  ]);
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
      document.getElementById('sb-ws').textContent = `WS: ${msg.status} #${msg.seq}`;
      document.getElementById('uptimeBadge').textContent = `UPTIME: ${formatUptime(msg.uptime)}`;
      break;
    case 'HEALTH_UPDATE':
      applyHealth(msg.data);
      break;
    case 'CYCLE_UPDATE':
      STATE.cycleCount++;
      document.getElementById('cycleCount').textContent = STATE.cycleCount;
      document.getElementById('sb-cycle').textContent = `CYCLE: ${STATE.cycleCount}`;
      pushAlert('CYCLE', `Cycle #${msg.data.cycle} complete — ${msg.data.signals_found} signals`, 'info');
      if (msg.data.top_signals) {
        renderSignalGrid(msg.data.top_signals);
        // Sound + notification for strong signals
        const strong = (msg.data.top_signals || []).find(s => s.confidence > 0.8);
        if (strong) {
          playSignalBeep();
          sendNotification('Strong Signal', `${strong.action} ${strong.ticker} — ${(strong.confidence * 100).toFixed(0)}% confidence`);
        }
      }
      break;
    case 'MODE_CHANGE':
      updateModeUI(msg.data.mode, true);
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
  dot.className = 'ws-dot ' + (state === 'connected' ? 'connected' : state === 'error' ? 'error' : '');
  document.getElementById('sb-ws').textContent = `WS: ${state.toUpperCase()}`;
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
    document.getElementById('modeBadge').textContent   = `MODE: ${d.mode}`;
    document.getElementById('sb-mode').textContent     = `MODE: ${d.mode}`;
    document.getElementById('statusBadge').textContent = `● ${d.status}`;
    document.getElementById('cycleCount').textContent  = d.cycle_count;
    document.getElementById('sb-cycle').textContent    = `CYCLE: ${d.cycle_count}`;
    document.getElementById('uptimeBadge').textContent = `UPTIME: ${formatUptime(d.uptime_seconds)}`;
    document.getElementById('cfgMode').value = d.mode.toLowerCase();
  } catch {}
}

// ─── Health ───────────────────────────────────────────────
async function loadHealth() {
  try {
    const d = await fetchJSON('/api/portfolio/health');
    applyHealth(d);
    const hist = await fetchJSON('/api/portfolio/equity_history');
    updateEquityChart(hist.history);
  } catch {}
}

function applyHealth(d) {
  STATE.health = d;

  // Command center — plain English labels
  setEl('navValue',     fmt$( d.equity ));
  setEl('sb-equity',    `NAV: ${fmt$(d.equity)}`);
  setEl('openPositions', d.open_positions);
  // Sharpe: plain English
  const sh = d.sharpe_ratio ?? 0;
  const shLabel = sh >= 2 ? 'Excellent' : sh >= 1 ? 'Good' : sh >= 0 ? 'Average' : 'Poor';
  setEl('sharpeVal', `${sh.toFixed(2)} (${shLabel})`);
  setEl('divStatus',    d.dalio_diversification_met ? '✓ DIVERSIFIED' : '✗ CONCENTRATED');

  const dailyPct = d.daily_pnl_pct ?? 0;
  const ddPct    = d.drawdown_pct ?? 0;
  setEl('dailyPnl', (dailyPct >= 0 ? '+' : '') + dailyPct.toFixed(3) + '%');
  el('dailyPnl').style.color = dailyPct >= 0 ? 'var(--green)' : 'var(--red)';
  setWidth('dailyPnlBar', Math.min(Math.abs(dailyPct) / 2 * 100, 100));
  if (dailyPct < 0) el('dailyPnlBar').style.background = 'var(--red)';

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
  if (cbLabel) { cbLabel.textContent = halted ? 'HALTED'   : 'ARMED';           cbLabel.className = halted ? 'cb-label halted' : 'cb-label'; }
  if (cbSub)   cbSub.textContent    = halted ? 'Trading SUSPENDED — limit hit' : 'Trading permitted';
  const cbBadge = el('circuitBreakerBadge');
  if (cbBadge) { cbBadge.textContent = halted ? 'CB: TRIGGERED' : 'CB: ARMED'; cbBadge.className = halted ? 'badge badge--red' : 'badge badge--green'; }
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
}

// ─── Quadrant ─────────────────────────────────────────────
async function loadQuadrant() {
  try {
    const d = await fetchJSON('/api/quadrant');
    STATE.quadrant = d;
    applyQuadrant(d);
  } catch {}
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
  setEl('sb-quadrant', `QUADRANT: ${meta.label}`);

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
  if (oppList) oppList.innerHTML = '<div style="padding:14px;color:var(--text-muted);font-size:10px;line-height:1.8">⟳ WARMING UP SCANNERS…<br><span style="opacity:.6">Fetching ASX, Crypto &amp; Commodities data for opportunity engine…</span></div>';

  // Fire all three market scans in background to seed the cache
  const seedCache = async () => {
    await Promise.allSettled([
      fetchJSON('/api/markets/asx').catch(() => {}),
      fetchJSON('/api/markets/crypto').catch(() => {}),
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
  } catch (e) {
    grid.innerHTML = `<div class="signal-loading"><span>⚠ SCAN ERROR — ${e.message || 'server unreachable'}</span></div>`;
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
      if (filterMkt === 'CRYPTO'       && !s.ticker.endsWith('-USD'))                       return false;
      if (filterMkt === 'COMMODITIES'  && (s.ticker.endsWith('.AX') || s.ticker.endsWith('-USD'))) return false;
    }
    return true;
  });

  setEl('signalCount', `${filtered.length} SIGNALS`);

  if (!filtered.length) {
    el('signalGrid').innerHTML = `<div class="signal-loading"><span>NO SIGNALS MATCH FILTERS</span></div>`;
    return;
  }

  el('signalGrid').innerHTML = filtered.map(s => signalCardHTML(s)).join('');

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
  // Crypto can be fractional; stocks use 2dp
  if (!s.price) return '---';
  if (s.ticker.endsWith('-USD') && s.price > 1000) return '$' + s.price.toLocaleString('en-US', { maximumFractionDigits: 0 });
  return '$' + s.price.toFixed(s.price < 1 ? 4 : 2);
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
  const projCol = isBuy ? '#00ff41' : '#ff2222';

  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" xmlns="http://www.w3.org/2000/svg" style="display:block">
    <line x1="0" y1="${slY}" x2="${W}" y2="${slY}" stroke="rgba(255,34,34,0.25)" stroke-width="1" stroke-dasharray="3,3"/>
    <line x1="0" y1="${tpY}" x2="${W}" y2="${tpY}" stroke="rgba(0,255,65,0.25)" stroke-width="1" stroke-dasharray="3,3"/>
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
  const confPct   = Math.min(s.confidence, 100);
  const confColor = s.confidence >= 80 ? 'var(--green)' : s.confidence >= 65 ? 'var(--amber)' : 'var(--red)';
  const overview  = s.dalio_justification?.ai_overview ?? '';
  const srcBadge  = s.data_source === 'LIVE'
    ? `<span style="font-size:8px;color:var(--green);letter-spacing:1px">● LIVE</span>`
    : `<span style="font-size:8px;color:var(--amber);letter-spacing:1px">● DEMO</span>`;
  const rrNum     = s.rr_ratio ?? 0;
  const rrLabel   = rrNum >= 2.5 ? '★ EXCELLENT' : rrNum >= 1.5 ? '✓ GOOD' : '⚠ LOW';
  const rrColor   = rrNum >= 2.5 ? 'var(--green)' : rrNum >= 1.5 ? 'var(--amber)' : 'var(--red)';

  return `
    <div class="signal-card ${s.action}" data-ticker="${s.ticker}">
      <div class="sc-header">
        <span class="sc-ticker">${s.ticker.replace('-USD','')}</span>
        <span class="sc-action ${s.action}">${actionVerb(s.action)}</span>
        ${srcBadge}
      </div>
      <div class="sc-price">
        <strong style="font-size:13px">${fmtSignalPrice(s)}</strong>
        &nbsp;<span style="color:var(--text-2);font-size:9px">entry price</span>
      </div>
      <div class="sc-price" style="margin-top:2px">
        <span style="color:var(--red);font-size:9px">⬇ Stop Loss ${s.stop_loss ? '$'+s.stop_loss.toFixed(2) : '--'}</span>
        &nbsp;&nbsp;
        <span style="color:var(--green);font-size:9px">⬆ Take Profit ${s.take_profit ? '$'+s.take_profit.toFixed(2) : '--'}</span>
      </div>
      <div class="sc-conf" style="margin-top:6px">
        <span class="sc-conf-label">CONFIDENCE</span>
        <div class="sc-conf-bar"><div class="sc-conf-fill" style="width:${confPct}%;background:${confColor}"></div></div>
        <span class="sc-conf-val" style="color:${confColor}">${s.confidence.toFixed(1)}%</span>
      </div>
      <div class="sc-meta" style="margin-top:5px">
        <span title="Relative Strength Index — measures overbought/oversold">RSI: <strong>${rsiLabel(s.rsi ?? 50)}</strong></span>
        <span title="Trend direction vs 20-day average">Trend: <strong>${trendLabel(s.trend)}</strong></span>
        <span title="Reward:Risk ratio — how much you gain vs risk">R:R <strong style="color:${rrColor}">${rrNum.toFixed(2)} ${rrLabel}</strong></span>
        <span title="Suggested portfolio weight">Size: <strong>${s.position_size_pct}% of portfolio</strong></span>
      </div>
      <span class="sc-fit ${s.quadrant_fit}">${s.quadrant_fit?.toUpperCase()} DALIO FIT</span>
      ${s.options_strategy ? `<div style="font-size:9px;color:var(--cyan);margin-top:4px">⚙ Options: ${s.options_strategy}</div>` : ''}
      <div class="sc-prediction">
        <div class="sc-pred-header">
          <span>◈ PRICE PREDICTION</span>
          <span class="sc-pred-days">~${s.predicted_days ?? '?'}d → ${sellDateStr(s.predicted_days ?? 14)}</span>
        </div>
        ${sparklineSVG(s)}
        <div class="sc-pred-levels">
          <span style="color:var(--red)">SL $${s.stop_loss?.toFixed(2) ?? '--'}</span>
          <span style="color:var(--text-2)">NOW $${fmtSignalPrice(s).replace('$','')}</span>
          <span style="color:var(--green)">TP $${s.take_profit?.toFixed(2) ?? '--'}</span>
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
    list.innerHTML = `<div style="padding:14px;color:var(--red);font-size:10px">SCAN FAILED: ${e.message}</div>`;
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
  const fitColour = { strong:'var(--green)', moderate:'var(--primary)', neutral:'var(--text-2)', avoid:'var(--red)' };
  const actionColour = { BUY:'var(--green)', LONG:'var(--green)', SELL:'var(--red)', SHORT:'var(--red)', WATCH:'var(--amber)' };

  list.innerHTML = opps.map((o, i) => {
    const chgSign  = o.change_pct >= 0 ? '+' : '';
    const chgCol   = o.change_pct >= 0 ? 'var(--green)' : 'var(--red)';
    const fitCol   = fitColour[o.quadrant_fit] || 'var(--text-2)';
    const actCol   = actionColour[o.action]    || 'var(--text-1)';
    const rsiCol   = o.rsi < 35 ? 'var(--green)' : o.rsi > 65 ? 'var(--red)' : 'var(--amber)';
    const scoreBar = Math.min(Math.round(o.score), 100);
    const reasons  = (o.reasoning || []).slice(0, 3);

    return `<div class="opp-card opp-card--rich" onclick="this.classList.toggle('opp-expanded')">
      <div class="opp-header">
        <span class="opp-rank">#${i+1}</span>
        <span class="opp-ticker" style="color:${actCol}">${o.ticker}</span>
        <span class="opp-badge" style="color:${actCol};border-color:${actCol}">${o.action}</span>
        <span class="opp-market">${(o.market||'').toUpperCase()}</span>
        <span class="opp-price">$${o.price.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:4})}</span>
        <span style="color:${chgCol};font-size:9px">${chgSign}${o.change_pct.toFixed(2)}%</span>
        <span class="opp-fit-badge" style="color:${fitCol};border-color:${fitCol}">${(o.quadrant_fit||'').toUpperCase()}</span>
        <div class="opp-score-bar"><div class="opp-score-fill" style="width:${scoreBar}%;background:${fitCol}"></div></div>
        <span class="opp-score-val">${o.score.toFixed(0)}</span>
      </div>
      <div class="opp-metrics">
        <span>RSI <b style="color:${rsiCol}">${o.rsi.toFixed(0)}</b></span>
        <span>TREND <b>${o.trend}</b></span>
        <span>SMA20 <b style="color:${o.above_sma20?'var(--green)':'var(--red)'}">${o.above_sma20?'↑ ABOVE':'↓ BELOW'}</b></span>
        <span>52W <b>${o.pct_from_lo >= 0 ? '+' : ''}${o.pct_from_lo}% FROM LOW</b></span>
        <span>SL <b style="color:var(--red)">$${o.stop_loss.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:4})}</b></span>
        <span>TP <b style="color:var(--green)">$${o.take_profit.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:4})}</b></span>
        <span>R:R <b style="color:var(--primary)">${o.rr_ratio.toFixed(1)}x</b></span>
        <span>VOL <b>${o.volume_fmt||'--'}</b></span>
      </div>
      <div class="opp-reasons">
        ${reasons.map(r => `<div class="opp-reason-line">▸ ${r}</div>`).join('')}
      </div>
      <div class="opp-actions">
        <button class="scan-trade-btn" onclick="event.stopPropagation();scannerOpenTrade('${o.ticker}',${o.price})">▲ TRADE</button>
        <button class="scan-wl-btn"    onclick="event.stopPropagation();toggleWatchlist('${o.ticker}',this)">☆ WATCH</button>
      </div>
    </div>`;
  }).join('');
}

function showJustification(s) {
  const j = s.dalio_justification || {};
  const qMeta = QUADRANT_META[j.quadrant] || {};
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
    </div>`;
}

// ─── Sentiment ────────────────────────────────────────────
async function loadSentiment() {
  el('newsFeed').innerHTML = `<div class="news-loading"><div class="loading-spinner"></div><span>RUNNING FINBERT SCAN...</span></div>`;
  try {
    const d = await fetchJSON('/api/sentiment');
    STATE.sentiment = d;
    applySentiment(d);
  } catch {}
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

  // Stats
  const stats = el('sentimentStats');
  if (stats) {
    stats.innerHTML = Object.entries(d.quadrant_sentiment || {}).map(([q, v]) => {
      const meta = QUADRANT_META[q] || {};
      return `<div class="sq-stat">
        <span class="sq-stat-label" style="color:${meta.color||'var(--text-2)'}">${(meta.label||q).replace(/_/g,' ')}</span>
        <span class="sq-stat-val">${v.article_count} arts · ${v.bullish_pct.toFixed(0)}% bull</span>
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

  setEl('newsArticleCount', `${filtered.length} / ${articles.length} ARTICLES`);
  renderNewsFeed(filtered);
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
    const live = d.data_source === 'LIVE';
    srcEl.textContent = live ? '● LIVE DATA' : '● DEMO DATA';
    srcEl.style.color = live ? 'var(--green)' : 'var(--amber)';
  }

  drawCorrelationHeatmap(d.tickers, d.matrix);
  renderAllocTable(d.tickers, d.matrix);
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
        ctx.fillStyle = Math.abs(v) > 0.5 ? '#030c08' : 'rgba(0,255,65,0.85)';
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
  if (v >= 0.8) return '#00ff41';
  if (v >= 0.5) return '#00cc33';
  if (v >= 0.3) return '#ffb300';
  if (v >= 0.1) return '#1a4028';
  if (v >= -0.1) return '#0a1018';
  if (v >= -0.3) return '#3a1010';
  return '#cc1a1a';
}

function renderAllocTable(tickers, matrix) {
  const n = tickers.length;
  const weights = Object.fromEntries(
    tickers.map((t, i) => [t, (1 / n).toFixed(4)])
  );
  const body = el('allocTableBody');
  if (!body) return;
  const fits = ['strong','moderate','weak'];
  body.innerHTML = tickers.map((t, i) => {
    const rowAvg = matrix[i].reduce((s,v,j) => j!==i ? s+Math.abs(v) : s, 0) / (n-1);
    const fit = rowAvg < 0.2 ? 'strong' : rowAvg < 0.35 ? 'moderate' : 'weak';
    return `<tr>
      <td class="td-green">${t}</td>
      <td class="td-cyan">${(1/n*100).toFixed(2)}%</td>
      <td>${(1/n*100).toFixed(2)}%</td>
      <td><span class="sc-fit ${fit}">${fit.toUpperCase()}</span></td>
    </tr>`;
  }).join('');
}

// ─── Backtest ─────────────────────────────────────────────
async function loadBacktest() {
  try {
    const d = await fetchJSON('/api/backtest/latest');
    STATE.backtest = d;
    applyBacktest(d);
  } catch {}
}

function applyBacktest(d) {
  setEl('bt-totalRet', (d.total_return_pct >= 0 ? '+' : '') + d.total_return_pct?.toFixed(1) + '%');
  setEl('bt-sharpe',   d.sharpe_ratio?.toFixed(2) ?? '--');
  setEl('bt-sortino',  d.sortino_ratio?.toFixed(2) ?? '--');
  setEl('bt-calmar',   d.calmar_ratio?.toFixed(2) ?? '--');
  setEl('bt-maxdd',    d.max_drawdown_pct?.toFixed(2) + '%' ?? '--');
  setEl('bt-winrate',  d.win_rate_pct?.toFixed(1) + '%' ?? '--');
  setEl('bt-periods',  d.periods ?? '--');
  setEl('bt-annRet',   (d.annualised_return_pct >= 0 ? '+' : '') + d.annualised_return_pct?.toFixed(1) + '%');

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
    const retClass = p.return_pct >= 0 ? 'td-green' : 'td-red';
    return `<tr>
      <td>${p.period}</td>
      <td>${p.train_start || '--'}</td>
      <td class="${retClass}">${p.return_pct >= 0 ? '+' : ''}${p.return_pct.toFixed(2)}%</td>
      <td class="td-cyan">${p.sharpe.toFixed(2)}</td>
      <td class="td-red">${p.max_drawdown.toFixed(2)}%</td>
      <td>${p.win_rate.toFixed(1)}%</td>
      <td>${p.trades}</td>
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
    // Crypto: show 2 decimals; indices: commas; fx: 4 decimals
    if (item.category === 'crypto' && p > 1000) return '$' + p.toLocaleString('en-US', { maximumFractionDigits: 0 });
    if (item.category === 'crypto') return '$' + p.toFixed(4);
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
  // Reset animation cleanly
  inner.style.animation = 'none';
  void inner.offsetWidth;          // force reflow so reset registers
  inner.style.animation = '';      // re-enable ticker-scroll from CSS
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
  plugins: { legend: { display: false }, tooltip: { backgroundColor: '#0d1520', borderColor: '#00ff41', borderWidth: 1, titleColor: '#00ff41', bodyColor: '#b0ffc0', padding: 8 } },
  scales: {
    x: { ticks: { color: '#5a8a65', font: { family: 'JetBrains Mono', size: 9 }, maxRotation: 30 }, grid: { color: 'rgba(10,24,16,0.8)' } },
    y: { ticks: { color: '#5a8a65', font: { family: 'JetBrains Mono', size: 9 } }, grid: { color: 'rgba(10,24,16,0.8)' } },
  },
};

function initCharts() {
  Chart.defaults.color = '#5a8a65';
  Chart.defaults.font.family = 'JetBrains Mono, Share Tech Mono, monospace';

  // Equity chart
  const ectx = el('equityChart')?.getContext('2d');
  if (ectx) {
    charts.equity = new Chart(ectx, {
      type: 'line',
      data: { labels: [], datasets: [{ label: 'NAV', data: [], borderColor: '#00ff41', borderWidth: 2, fill: true, backgroundColor: 'rgba(0,255,65,0.06)', tension: 0.3, pointRadius: 0 }] },
      options: { ...CHART_DEFAULTS, maintainAspectRatio: false },
    });
  }

  // Sentiment radar/bar
  const sctx = el('sentimentChart')?.getContext('2d');
  if (sctx) {
    charts.sentiment = new Chart(sctx, {
      type: 'bar',
      data: {
        labels: ['RISING GROWTH','FALLING GROWTH','RISING INFLATION','FALLING INFLATION'],
        datasets: [{
          data: [0,0,0,0],
          backgroundColor: ['rgba(0,255,65,0.5)','rgba(255,34,34,0.5)','rgba(255,179,0,0.5)','rgba(0,229,255,0.5)'],
          borderColor:      ['#00ff41','#ff2222','#ffb300','#00e5ff'],
          borderWidth: 1,
        }],
      },
      options: { ...CHART_DEFAULTS, maintainAspectRatio: false, indexAxis: 'y' },
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

  // PnL chart
  const pnlctx = el('pnlChart')?.getContext('2d');
  if (pnlctx) {
    const pnlData = Array.from({ length: 30 }, () => +(Math.random() * 2 - 0.5).toFixed(3));
    charts.pnl = new Chart(pnlctx, {
      type: 'bar',
      data: {
        labels: pnlData.map((_, i) => `D-${30-i}`),
        datasets: [{
          data: pnlData,
          backgroundColor: pnlData.map(v => v >= 0 ? 'rgba(0,255,65,0.6)' : 'rgba(255,34,34,0.6)'),
          borderColor:      pnlData.map(v => v >= 0 ? '#00ff41' : '#ff2222'),
          borderWidth: 1,
        }],
      },
      options: { ...CHART_DEFAULTS, maintainAspectRatio: false },
    });
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
}

function updateSentimentChart(qs) {
  if (!charts.sentiment || !qs) return;
  const keys   = ['rising_growth','falling_growth','rising_inflation','falling_inflation'];
  charts.sentiment.data.datasets[0].data = keys.map(k => qs[k]?.article_count ?? 0);
  charts.sentiment.update('none');
}

function updateWFChart(periods) {
  if (!charts.wf || !periods.length) return;
  charts.wf.data.labels = periods.map(p => `P${p.period}`);
  charts.wf.data.datasets[0].data = periods.map(p => p.return_pct);
  charts.wf.data.datasets[0].backgroundColor = periods.map(p => p.return_pct >= 0 ? 'rgba(0,255,65,0.6)' : 'rgba(255,34,34,0.6)');
  charts.wf.data.datasets[0].borderColor      = periods.map(p => p.return_pct >= 0 ? '#00ff41' : '#ff2222');
  charts.wf.update('none');
}

function updateWeightsChart(weights) {
  if (!charts.weights) return;
  const keys = Object.keys(weights).slice(0, 15);
  const vals = keys.map(k => +(weights[k] * 100).toFixed(2));
  const palette = ['#00ff41','#00cc33','#00aa27','#008820','#006818','#00e5ff','#00b8cc','#ffb300','#cc8c00','#ff6b00','#ff2222','#cc1a1a','#00ff99','#ff00ff','#8800ff'];
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
    { id:'cmd-quadrant', sel:'#quadrantPanel',      arrow:'right',  title:'📊 ECONOMIC QUADRANT',    text:'The glowing cell shows Ray Dalio\'s current economic regime. It tells you exactly what assets to buy or avoid right now.' },
    { id:'cmd-equity',   sel:'#equityPanel',        arrow:'bottom', title:'📈 EQUITY CURVE',          text:'Your portfolio value over time. A rising line = the strategy is working. Each data point is a live portfolio snapshot.' },
    { id:'cmd-vitals',   sel:'.panel--gauges',      arrow:'left',   title:'❤ PORTFOLIO VITALS',       text:'Daily P&L and drawdown at a glance. If drawdown hits 10%, the system auto-halts all trading to protect your capital.' },
    { id:'cmd-cycle',    sel:'#runCycleBtn',        arrow:'bottom', title:'▶ RUN A SCAN NOW',         text:'Click to trigger an immediate market scan across all ASX, crypto, and commodity assets. New signals appear in seconds.' },
  ],
  'signal-ops': [
    { id:'sig-banner',   sel:'#strongSignalInPage', arrow:'bottom', title:'⚡ STRONG SIGNAL ALERT',   text:'When confidence is 82%+, this bar lights up. It tells you exactly what to do and where to jump in the signal list.' },
    { id:'sig-grid',     sel:'#signalGrid',         arrow:'top',    title:'🃏 SIGNAL CARDS',           text:'Each card = one trade. BUY/LONG in green, SELL/SHORT in red. Cards are sorted by confidence — best signal first.' },
    { id:'sig-rr',       sel:'.signal-controls',    arrow:'bottom', title:'🎚 FILTER SIGNALS',         text:'Raise the confidence slider to 75%+ for high-conviction only. Switch between ASX, Crypto, or all markets.' },
    { id:'sig-just',     sel:'#justificationPanel', arrow:'left',   title:'🧠 AI JUSTIFICATION',       text:'Click any signal card to see the full reason — economic quadrant fit, sentiment score, RSI, and Dalio framework logic.' },
  ],
  'intel-center': [
    { id:'int-risk',     sel:'.panel--conflict',    arrow:'right',  title:'⚠ GEOPOLITICAL RISK',      text:'Counts news articles with conflict keywords. Red ring = elevated risk. In risk periods, shift toward Gold and Bonds.' },
    { id:'int-sent',     sel:'.panel--sentiment-chart', arrow:'left', title:'📰 SENTIMENT DISTRIBUTION', text:'Shows how many articles are positive/negative for each Dalio quadrant. Tells you what the market is most worried about.' },
    { id:'int-news',     sel:'#newsFeed',           arrow:'top',    title:'🔴 NEWS FEED',              text:'Top headlines ranked by AI sentiment score. Red = negative (bearish). Green = positive (bullish). Conflict articles are flagged ⚠.' },
  ],
  'holy-grail': [
    { id:'hg-heatmap',   sel:'.panel--heatmap',     arrow:'right',  title:'🟩 CORRELATION MATRIX',     text:'Each cell shows how two assets move together. Dark = independent (good). Bright green = they move in sync (bad for diversification).' },
    { id:'hg-meter',     sel:'.panel--div-meter',   arrow:'left',   title:'🏆 HOLY GRAIL METER',       text:'Dalio\'s key target: hold 15+ assets with correlation below 0.30. When one falls, others hold up. This meter tracks your score.' },
    { id:'hg-weights',   sel:'.panel--weights',     arrow:'bottom', title:'⚖ RISK-PARITY WEIGHTS',     text:'Suggested portfolio split so each asset contributes EQUAL risk. Not equal dollars — equal risk. This is the Dalio method.' },
  ],
  'risk-matrix': [
    { id:'rm-cb',        sel:'.panel--circuit',     arrow:'right',  title:'🛑 CIRCUIT BREAKER',        text:'Your safety net. If daily loss > 2% OR total drawdown > 10%, trading halts automatically. No manual action needed.' },
    { id:'rm-metrics',   sel:'.panel--risk-metrics',arrow:'left',   title:'📊 RISK METRICS',           text:'Sharpe > 1.0 is good. Sortino > 1.5 is solid. Max drawdown shows the worst loss. Win rate > 55% is strong for a systematic strategy.' },
    { id:'rm-pos',       sel:'.panel--positions',   arrow:'top',    title:'📋 OPEN POSITIONS',         text:'All current trades and their unrealised P&L. Green = profitable. Red = underwater. Watch for positions near their stop-loss.' },
  ],
  'backtest-lab': [
    { id:'bt-summary',   sel:'.panel--bt-summary',  arrow:'right',  title:'📈 BACKTEST RESULTS',       text:'Walk-forward testing: the system trains on 12 months of data, then tests on the next 3 months it\'s never seen. Prevents cheating.' },
    { id:'bt-chart',     sel:'.panel--wf-chart',    arrow:'bottom', title:'📊 PERIOD CHART',            text:'Each bar = one test period. Green = profitable, Red = losing. Look for consistent green bars — that\'s a robust strategy.' },
    { id:'bt-table',     sel:'.panel--bt-table',    arrow:'top',    title:'📋 PERIOD BREAKDOWN',        text:'Drill into each period: return, Sharpe, max drawdown, win rate, and number of trades. Consistent Sharpe > 1.0 is the goal.' },
  ],
  'comms-config': [
    { id:'cfg-brokers',  sel:'.panel--brokers',     arrow:'top',    title:'🔗 BROKER CONNECTIONS',     text:'Connect your broker or exchange. Click "Open →" to go to the platform. For automation, Alpaca offers free paper trading with a full API.' },
    { id:'cfg-discord',  sel:'.panel--discord',     arrow:'right',  title:'📣 DISCORD ALERTS',         text:'Get trade signals sent straight to a Discord channel. Paste your webhook URL and hit Test to verify it works.' },
    { id:'cfg-mode',     sel:'#cfgMode',            arrow:'left',   title:'⚠ PAPER vs LIVE MODE',      text:'ALWAYS start in PAPER mode. It simulates trades with zero real money. Only switch to LIVE when you\'re confident in the signals.' },
  ],
  'paper-trading': [
    { id:'pt-order',    sel:'.panel--paper-order',   arrow:'right',  title:'📄 PLACE AN ORDER',         text:'Type any ticker (ASX, crypto, commodity), choose BUY or SELL, set quantity, and hit Execute. Prices pull from real market data.' },
    { id:'pt-summary',  sel:'.panel--paper-summary', arrow:'left',   title:'💼 PORTFOLIO TRACKER',      text:'Your paper portfolio starts at your configured amount (default $1,000). Total value, P&L, and open positions update live every 15 seconds.' },
    { id:'pt-signals',  sel:'.panel--paper-signals', arrow:'right',  title:'⚡ 1-CLICK SIGNAL TRADES',   text:'The system\'s top signals appear here pre-loaded. Adjust quantity and click BUY or SELL to instantly paper trade the recommendation.' },
    { id:'pt-history',  sel:'.panel--paper-history', arrow:'top',    title:'📋 TRADE HISTORY',           text:'Every closed trade is recorded with entry price, exit price, and P&L. Use this to evaluate which signals perform best over time.' },
  ],
};

let _spotQueue   = [];
let _spotIdx     = 0;
let _spotTabId   = null;
let _spotHighlit = null;

function showTutorial(tabId, force = false) {
  _spotTabId = tabId;
  const spots = SPOTS[tabId] || [];
  // Filter to unseen spots (unless force)
  _spotQueue = spots.filter(s => force || !localStorage.getItem(`dalios_spot_${s.id}`));
  _spotIdx   = 0;
  if (!_spotQueue.length) return;
  _showSpot(_spotIdx);
}

function _showSpot(idx) {
  const bubble = el('spotBubble');
  if (!bubble) return;

  // Remove previous highlight
  if (_spotHighlit) { _spotHighlit.classList.remove('spot-highlight'); _spotHighlit = null; }

  if (idx >= _spotQueue.length) {
    bubble.classList.add('hidden');
    return;
  }

  const spot = _spotQueue[idx];
  el('spotTitle').textContent = spot.title;
  el('spotText').textContent  = spot.text;
  el('spotCount').textContent = `${idx + 1} / ${_spotQueue.length}`;
  el('spotNextBtn').textContent = idx === _spotQueue.length - 1 ? 'Done ✓' : 'Next →';

  // Arrow direction class
  bubble.className = `spot-bubble arrow-${spot.arrow}`;

  // Find target and highlight it
  const target = document.querySelector(spot.sel);
  if (target) {
    target.classList.add('spot-highlight');
    _spotHighlit = target;
    _positionBubble(bubble, target, spot.arrow);
  } else {
    // Fallback: centre of screen
    bubble.style.top  = '50%';
    bubble.style.left = '50%';
    bubble.style.transform = 'translate(-50%,-50%)';
  }
}

function _positionBubble(bubble, target, arrow) {
  const GAP  = 16;
  const tr   = target.getBoundingClientRect();
  const bw   = 240;  // bubble width
  const bh   = 160;  // estimated bubble height
  const vw   = window.innerWidth;
  const vh   = window.innerHeight;

  bubble.style.transform = '';
  let top, left;

  if (arrow === 'right') {
    // Bubble appears to the RIGHT of the target
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
  } else { // top
    left = tr.left + (tr.width / 2) - (bw / 2);
    top  = tr.top - bh - GAP;
    if (top < 8) { top = tr.bottom + GAP; bubble.className = 'spot-bubble arrow-top'; }
  }

  // Clamp to viewport
  top  = Math.max(8, Math.min(top,  vh - bh - 8));
  left = Math.max(8, Math.min(left, vw - bw - 8));

  bubble.style.top  = `${top}px`;
  bubble.style.left = `${left}px`;
}

function nextSpot() {
  if (!_spotQueue.length) return;
  // Mark current as seen
  const spot = _spotQueue[_spotIdx];
  if (spot) localStorage.setItem(`dalios_spot_${spot.id}`, '1');

  _spotIdx++;
  if (_spotIdx >= _spotQueue.length) {
    // Done — hide bubble, remove highlight
    if (_spotHighlit) { _spotHighlit.classList.remove('spot-highlight'); _spotHighlit = null; }
    el('spotBubble').classList.add('hidden');
    return;
  }
  _showSpot(_spotIdx);
}

function skipAllSpots() {
  // Mark all remaining spots as seen
  (_spotQueue || []).forEach(s => localStorage.setItem(`dalios_spot_${s.id}`, '1'));
  if (_spotHighlit) { _spotHighlit.classList.remove('spot-highlight'); _spotHighlit = null; }
  el('spotBubble').classList.add('hidden');
  _spotQueue = [];
}

function openCurrentTutorial() {
  const activeBtn = document.querySelector('.tab-btn.active');
  const tabId = activeBtn?.dataset?.tab ?? 'command-center';
  // Clear seen state for this tab's spots so they all show again
  (SPOTS[tabId] || []).forEach(s => localStorage.removeItem(`dalios_spot_${s.id}`));
  showTutorial(tabId, true);
}

function closeTutorial() { skipAllSpots(); }

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
    return;
  }

  const isBuy  = ['BUY','LONG'].includes(strong.action);
  const verb   = isBuy ? 'BUY' : 'SHORT/SELL';
  const detail = `${strong.ticker.replace('-USD','')}  ·  Entry ${fmtSignalPrice(strong)}  ·  Stop ${strong.stop_loss ? '$'+strong.stop_loss.toFixed(2) : '--'}  ·  Target ${strong.take_profit ? '$'+strong.take_profit.toFixed(2) : '--'}  ·  Confidence ${strong.confidence.toFixed(1)}%  ·  R:R ${strong.rr_ratio?.toFixed(2)}`;

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
    setEl('ssiDetail', `Confidence ${strong.confidence.toFixed(1)}%  ·  Entry ${fmtSignalPrice(strong)}  ·  Stop $${strong.stop_loss?.toFixed(2)}  ·  Target $${strong.take_profit?.toFixed(2)}  ·  R:R ${strong.rr_ratio?.toFixed(2)}`);
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
  ['au','us','crypto'].forEach(c => {
    const g = el(`bcat-${c}`);
    if (g) g.classList.toggle('hidden', c !== cat);
  });
}

async function connectAlpaca() {
  const key    = el('alpacaKey')?.value?.trim();
  const secret = el('alpacaSecret')?.value?.trim();
  const env    = el('alpacaEnv')?.value;
  const result = el('alpacaConnectResult');
  if (!key || !secret) { if (result) result.textContent = '⚠ Enter API key and secret first'; return; }
  if (result) result.textContent = '⌛ Testing connection...';
  try {
    const base = env === 'paper' ? 'https://paper-api.alpaca.markets' : 'https://api.alpaca.markets';
    const r = await fetch(`${base}/v2/account`, {
      headers: { 'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret }
    });
    if (r.ok) {
      const data = await r.json();
      if (result) result.textContent = `✓ Connected! Account: ${data.account_number} · ${env.toUpperCase()}`;
      el('alpacaStatus') && setEl('alpacaStatus', '● Connected');
      el('alpacaStatus') && (el('alpacaStatus').className = 'broker-status online');
      pushAlert('BROKER', `Alpaca ${env} account connected`, 'info');
    } else {
      if (result) result.textContent = `✗ Auth failed (${r.status}) — check your keys`;
    }
  } catch (e) {
    if (result) result.textContent = '✗ Connection failed — check network / CORS';
  }
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
    pushAlert('ERROR', `Cycle failed: ${e.message || 'server unreachable'}`, 'warning');
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
  // Populate cash from server
  fetchJSON('/api/paper/config').then(d => {
    const inp = el('settStartCash'); if (inp) inp.value = d.starting_cash;
    const inp2 = el('startingCashInput'); if (inp2) inp2.value = d.starting_cash;
  }).catch(() => {});
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
  _saveSetting('tutorials_off', false);
  const btn = el('settTutorialBtn'); if (btn) { btn.textContent = 'ON'; btn.classList.add('on'); }
  pushAlert('SETTINGS', 'All tutorials reset — they will show again on next tab visit', 'info');
}

// Patch showTutorial to respect the setting
const _origShowTutorial = showTutorial;
window.showTutorial = function(tabId, force = false) {
  if (!force && _loadSettings().tutorials_off) return;
  _origShowTutorial(tabId, force);
};

function toggleSoundSetting(btn) {
  toggleSound();
  btn.textContent = _soundOn ? 'ON' : 'OFF';
  btn.classList.toggle('on', _soundOn);
  const mainBtn = el('soundToggleBtn');
  if (mainBtn) mainBtn.textContent = _soundOn ? '🔊 SOUND' : '🔇 SOUND';
}

function requestNotificationPermission() {
  initNotifications();
  setTimeout(() => {
    const btn = el('settNotifBtn');
    if (btn) { btn.textContent = Notification.permission === 'granted' ? 'ENABLED' : 'BLOCKED'; btn.classList.toggle('on', Notification.permission === 'granted'); }
  }, 1500);
}

// ─── Theme switcher ────────────────────────────────────────
const _THEMES = {
  cyber:  { primary: '#00d4ff', green: '#00ff88', red: '#ff3355', amber: '#ffb000', bg0: '#04080e' },
  matrix: { primary: '#00ff41', green: '#00ff41', red: '#ff3355', amber: '#ccff00', bg0: '#000d02' },
  void:   { primary: '#c084fc', green: '#a3e635', red: '#f87171', amber: '#fbbf24', bg0: '#06020f' },
  amber:  { primary: '#ffb000', green: '#00d4ff', red: '#ff3355', amber: '#ffb000', bg0: '#0c0700' },
};

function setTheme(name, btn) {
  const t = _THEMES[name]; if (!t) return;
  const root = document.documentElement;
  root.style.setProperty('--primary',      t.primary);
  root.style.setProperty('--green',        t.green);
  root.style.setProperty('--red',          t.red);
  root.style.setProperty('--amber',        t.amber);
  root.style.setProperty('--bg-0',         t.bg0);
  root.style.setProperty('--primary-glow', t.primary + '40');
  root.style.setProperty('--green-glow',   t.green + '40');
  document.querySelectorAll('.sett-theme-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  _saveSetting('theme', name);
  pushAlert('SETTINGS', `Theme set to ${name.toUpperCase()}`, 'info');
}

function _applyStoredTheme() {
  const s = _loadSettings();
  if (s.theme && s.theme !== 'cyber') {
    const btn = document.querySelector(`[data-theme="${s.theme}"]`);
    if (btn) setTheme(s.theme, btn);
  }
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
      pushAlert('SETTINGS', `Failed to save starting cash: ${e.message}`, 'warning');
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
const setEl = (id, val) => { const e = el(id); if (e) e.textContent = val; };
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
  renderSearchResults(val);
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
    list.innerHTML = `<div class="sr-empty">No assets matching "${q}"</div>`;
    return;
  }

  const catOrder = { ASX: 0, Crypto: 1, Commodity: 2, Unknown: 3 };
  results.sort((a, b) => (catOrder[a.cat] ?? 3) - (catOrder[b.cat] ?? 3));

  list.innerHTML = results.map(a => {
    const price = a.price != null
      ? `<span class="sr-price">${a.cat === 'Crypto' && a.price > 1000 ? '$' + Math.round(a.price).toLocaleString() : '$' + (a.price).toFixed(2)}</span>`
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

  // Mirror to Command Centre
  applyCommandCentre(d, null);
}

async function closePaperPosition(ticker) {
  try {
    await postJSON('/api/paper/close', { ticker });
    loadPaperPortfolio();
    loadPaperHistory();
    pushAlert('PAPER', `Closed position: ${ticker}`, 'info');
    pushActivityItem('✕', `Closed position: ${ticker.replace('-USD','')}`, 'sell');
  } catch (e) {
    pushAlert('PAPER', `Close failed: ${e.message}`, 'warning');
  }
}

async function resetPaperPortfolio() {
  const cfgCash = parseFloat(el('startingCashInput')?.value) || 1000;
  if (!confirm(`Reset portfolio to $${cfgCash.toLocaleString()} starting cash? All positions and history will be cleared.`)) return;
  await postJSON('/api/paper/reset', {});
  loadPaperPortfolio();
  loadPaperHistory();
  loadPaperEquityCurve();
  pushAlert('PAPER', `Portfolio reset to $${cfgCash.toLocaleString()}`, 'info');
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
    _poPrice  = d.price;
    // Update the input field if the server normalised the ticker (e.g. BTC → BTC-USD)
    if (d.ticker && d.ticker !== ticker) {
      _poTicker = d.ticker;
      const inp = el('poTicker');
      if (inp) inp.value = d.ticker;
    }
    if (res) {
      res.innerHTML = d.price != null
        ? `<span style="color:var(--green)">✓</span> <strong>${d.name}</strong> · ${d.cat} · <span style="color:var(--primary)">${fmt$(d.price)}</span>`
        : `<span style="color:var(--amber)">⚠ price unavailable — try adding .AX (ASX) or -USD (crypto)</span>`;
    }
    updatePoEstimate();
  } catch {
    if (res) res.innerHTML = `<span style="color:var(--red)">✗ not found — try BTC-USD, BHP.AX, GLD</span>`;
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
    if (res) res.innerHTML = `<span style="color:var(--red)">✗ ${msg}</span>`;
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

  // Mirror to Command Centre
  applyCommandCentre(null, d);

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
        <span class="psr-conf">Conf: ${s.confidence.toFixed(0)}%</span>
        ${dalioScore}
        <span class="psr-conf" style="color:var(--text-2)">${s.reason || ''}</span>
      </div>
      <div class="psr-right">
        <input type="number" class="po-input psr-qty" id="psrQty-${s.ticker}" value="${suggestQty}" min="0.0001" step="any"/>
        <button class="psr-btn ${isBuy ? 'buy' : 'sell'}" onclick="quickTrade('${s.ticker}',${s.price},'${isBuy ? 'BUY' : 'SELL'}','psrQty-${s.ticker}')">
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

// ═══════════════════════════════════════════════════════════
// MARKET SCANNER (ASX / CRYPTO / COMMODITIES)
// ═══════════════════════════════════════════════════════════

const _scannerData = { asx: [], crypto: [], commodities: [] };
const _scannerSort = { asx: null, crypto: null, commodities: null };

const _SCANNER_IDS = {
  asx:         { tbody: 'asxTableBody',         stats: 'asxStats',    cols: 8 },
  crypto:      { tbody: 'cryptoTableBody',       stats: 'cryptoStats', cols: 8 },
  commodities: { tbody: 'commoditiesTableBody',  stats: 'commStats',   cols: 7 },
};

async function loadScanner(market) {
  const ids = _SCANNER_IDS[market];
  const tbody = el(ids.tbody);
  const statsEl = el(ids.stats);
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="${ids.cols}" class="scanner-loading">⌛ Fetching live data… (may take 5–15s for first load)</td></tr>`;
  if (statsEl) statsEl.innerHTML = '';
  try {
    const d = await fetchJSON(`/api/markets/${market}`);
    _scannerData[market] = d.rows || [];
    const cacheNote = d.cached ? ` <span style="opacity:.5">(cached ${d.cache_age}s ago)</span>` : '';
    if (statsEl) statsEl.dataset.cacheNote = cacheNote;
    renderScanner(market);
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="${ids.cols}" class="scanner-loading" style="color:var(--red)">⚠ Failed to load — ${e.message}</td></tr>`;
  }
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
      <span class="scanner-stat-item" style="margin-left:auto;font-size:8px;opacity:.5">CoinGecko${market==='crypto'?' Free API':' / yfinance'}${cacheNote}</span>`;
  }

  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="${ids.cols}" class="scanner-loading">No results</td></tr>`;
    return;
  }

  const isCrypto = market === 'crypto';
  tbody.innerHTML = rows.map(r => {
    const dir      = r.change_pct > 0 ? 'up' : r.change_pct < 0 ? 'down' : 'flat';
    const chgStr   = `${r.change_pct >= 0 ? '+' : ''}${r.change_pct.toFixed(2)}%`;
    const priceStr = r.price <= 0 ? '—'
                   : r.price >= 1000 ? `$${Number(r.price).toLocaleString('en-AU', {minimumFractionDigits:2, maximumFractionDigits:2})}`
                   : r.price >= 1    ? `$${r.price.toFixed(2)}`
                   : r.price >= 0.001 ? `$${r.price.toFixed(4)}`
                   : `$${r.price.toFixed(8)}`;
    // Use pre-formatted volume from server (includes B/M/K suffix)
    const volStr   = r.volume_fmt || (r.volume > 0 ? r.volume.toLocaleString() : '—');
    const wlLabel  = r.in_watchlist ? '★ WATCHING' : '☆ WATCH';
    const wlCls    = r.in_watchlist ? 'in' : '';
    const ticker   = r.ticker;
    const sectorCol  = market === 'asx' ? `<td>${r.sector || '—'}</td>` : '';
    const mktCapCol  = isCrypto ? `<td style="color:var(--text-2);font-size:10px">${r.market_cap_fmt || '—'}</td>` : '';
    const dispName   = isCrypto ? ticker.replace('-USD','') : ticker;
    const nameShort  = r.name.length > 26 ? r.name.slice(0,26) + '…' : r.name;
    return `<tr class="scanner-row ${dir}">
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
}

function filterScanner(market, text) {
  const sectorSel = el(`${market === 'asx' ? 'asx' : market === 'crypto' ? 'crypto' : 'comm'}SectorFilter`);
  renderScanner(market, text, sectorSel?.value || '');
}

function sortScanner(market, key) {
  const cur = _scannerSort[market];
  _scannerSort[market] = (cur?.key === key) ? { key, asc: !cur.asc } : { key, asc: false };
  renderScanner(market);
}

function scannerOpenTrade(ticker, price) {
  // Jump to paper trading tab and pre-fill the ticker
  document.querySelector('[data-tab="paper-trading"]')?.click();
  setTimeout(() => {
    const inp = el('poTicker');
    if (inp) { inp.value = ticker; onPoTickerInput(ticker); }
  }, 200);
}

// ─── Watchlist ─────────────────────────────────────────────
let _watchlist = [];

async function loadWatchlist() {
  try {
    const d = await fetchJSON('/api/watchlist');
    _watchlist = d.watchlist || [];
  } catch {}
}

async function toggleWatchlist(ticker, btn) {
  const inList = _watchlist.includes(ticker);
  try {
    const endpoint = inList ? '/api/watchlist/remove' : '/api/watchlist/add';
    const d = await postJSON(endpoint, { ticker });
    _watchlist = d.watchlist || [];
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
    badge.className   = mode === 'live' ? 'badge badge--red' : 'badge badge--amber';
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
  if (newMode === 'live') {
    const status = await fetchJSON('/api/broker/status').catch(() => null);
    if (!status?.connected) {
      pushAlert('MODE', 'Connect a broker first on the LIVE TRADING tab', 'warning');
      document.querySelector('[data-tab="live-trading"]')?.click();
      return;
    }
  }
  try {
    const d = await postJSON('/api/mode', { mode: newMode });
    updateModeUI(d.mode, true);
    playBeep(newMode === 'live' ? 880 : 440, 0.1);
    pushAlert('MODE', `Switched to ${d.mode.toUpperCase()} trading mode`, 'info');
    if (newMode === 'live') sendNotification('LIVE MODE ACTIVE', 'Real money trading is now active. Orders will be placed with your broker.');
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
      responsive: !isPaper, maintainAspectRatio: false,
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
    ds0.borderColor     = last >= startCash ? '#00d4ff' : '#ff3355';
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
    const badge = el('brokerStatusBadge');
    if (badge) {
      badge.textContent = d.connected ? `✓ ${(d.broker||'').toUpperCase()} CONNECTED` : 'NOT CONNECTED';
      badge.style.color = d.connected ? 'var(--green)' : 'var(--red)';
    }
    if (d.connected) {
      const summ = el('brokerAccountSummary');
      if (summ) summ.style.display = 'block';
      setEl('basValue',   fmt$(d.account_value  || 0));
      setEl('basBuying',  fmt$(d.buying_power    || 0));
      setEl('basCash',    fmt$(d.cash            || 0));
      setEl('basBroker',  (d.broker || '').toUpperCase());
      updateModeUI(_tradingMode, true);
    }
  } catch {}
}

function onBrokerSelect(val) {
  ['alpacaFields','ibkrFields','binanceFields','coinbaseFields','coinspotFields','stakeFields'].forEach(id => {
    const el2 = el(id); if (el2) el2.style.display = 'none';
  });
  const map = {
    alpaca:   'alpacaFields',
    ibkr:     'ibkrFields',
    binance:  'binanceFields',
    coinbase: 'coinbaseFields',
    coinspot: 'coinspotFields',
    stake:    'stakeFields',
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

  if (broker === 'alpaca') {
    payload.api_key    = el('settAlpacaKey')?.value?.trim();
    payload.api_secret = el('settAlpacaSecret')?.value?.trim();
    const env = el('settAlpacaEnv')?.value;
    payload.base_url   = env === 'live' ? 'https://api.alpaca.markets' : 'https://paper-api.alpaca.markets';
    if (!payload.api_key || !payload.api_secret) { if (resultEl) resultEl.innerHTML = '<span style="color:var(--red)">API key and secret required</span>'; return; }
  } else if (broker === 'ibkr') {
    payload.host      = el('settIbkrHost')?.value || '127.0.0.1';
    payload.port      = parseInt(el('settIbkrPort')?.value || '7497');
    payload.client_id = parseInt(el('settIbkrClientId')?.value || '1');
  } else if (broker === 'binance') {
    payload.api_key    = el('settBinanceKey')?.value?.trim();
    payload.api_secret = el('settBinanceSecret')?.value?.trim();
    payload.testnet    = el('settBinanceTestnet')?.value === 'true';
    if (!payload.api_key || !payload.api_secret) { if (resultEl) resultEl.innerHTML = '<span style="color:var(--red)">API key and secret required</span>'; return; }
  } else if (broker === 'coinspot') {
    payload.api_key    = el('settCoinspotKey')?.value?.trim();
    payload.api_secret = el('settCoinspotSecret')?.value?.trim();
    if (!payload.api_key || !payload.api_secret) { if (resultEl) resultEl.innerHTML = '<span style="color:var(--red)">API key and secret required</span>'; return; }
  } else if (broker === 'coinbase') {
    payload.api_key    = el('settCoinbaseKey')?.value?.trim();
    payload.api_secret = el('settCoinbaseSecret')?.value?.trim();
    if (!payload.api_key || !payload.api_secret) { if (resultEl) resultEl.innerHTML = '<span style="color:var(--red)">Key name and private key required</span>'; return; }
  }

  try {
    const d = await postJSON('/api/broker/connect', payload);
    if (resultEl) resultEl.innerHTML = `<span style="color:var(--green)">✓ ${d.broker.toUpperCase()} connected</span>`;
    pushAlert('BROKER', `${d.broker.toUpperCase()} connected from settings`, 'info');
    // sync to live trading tab dropdowns
    const sel = el('brokerSelect');
    if (sel) { sel.value = broker; onBrokerSelect(broker); }
    await loadBrokerStatus();
    await loadRealPortfolio();
  } catch (e) {
    if (resultEl) resultEl.innerHTML = `<span style="color:var(--red)">✗ ${e.message || 'Connection failed'}</span>`;
  }
}

async function connectBroker() {
  const broker = el('brokerSelect')?.value;
  if (!broker) { pushAlert('BROKER', 'Select a broker first', 'warning'); return; }
  const btn = el('brokerConnectBtn');
  const res = el('brokerConnectResult');
  if (btn) { btn.textContent = '⌛ CONNECTING...'; btn.classList.add('loading'); }
  let payload = { broker };
  if (broker === 'alpaca') {
    payload.api_key    = el('alpacaKey')?.value?.trim();
    payload.api_secret = el('alpacaSecret')?.value?.trim();
    payload.base_url   = el('alpacaUrl')?.value;
    if (!payload.api_key || !payload.api_secret) {
      if (res) res.innerHTML = '<span style="color:var(--red)">API key and secret required</span>';
      if (btn) { btn.textContent = '▶ CONNECT BROKER'; btn.classList.remove('loading'); }
      return;
    }
  } else if (broker === 'ibkr') {
    payload.host      = el('ibkrHost')?.value || '127.0.0.1';
    payload.port      = parseInt(el('ibkrPort')?.value || '7497');
    payload.client_id = parseInt(el('ibkrClientId')?.value || '1');
  } else if (broker === 'binance') {
    payload.api_key    = el('binanceKey')?.value?.trim();
    payload.api_secret = el('binanceSecret')?.value?.trim();
    payload.testnet    = el('binanceTestnet')?.value === 'true';
    if (!payload.api_key || !payload.api_secret) {
      if (res) res.innerHTML = '<span style="color:var(--red)">API key and secret required</span>';
      if (btn) { btn.textContent = '▶ CONNECT BROKER'; btn.classList.remove('loading'); }
      return;
    }
  } else if (broker === 'coinbase') {
    payload.api_key    = el('coinbaseKey')?.value?.trim();
    payload.api_secret = el('coinbaseSecret')?.value?.trim();
    if (!payload.api_key || !payload.api_secret) {
      if (res) res.innerHTML = '<span style="color:var(--red)">API key name and private key required</span>';
      if (btn) { btn.textContent = '▶ CONNECT BROKER'; btn.classList.remove('loading'); }
      return;
    }
  } else if (broker === 'coinspot') {
    payload.api_key    = el('coinspotKey')?.value?.trim();
    payload.api_secret = el('coinspotSecret')?.value?.trim();
    if (!payload.api_key || !payload.api_secret) {
      if (res) res.innerHTML = '<span style="color:var(--red)">API key and secret required</span>';
      if (btn) { btn.textContent = '▶ CONNECT BROKER'; btn.classList.remove('loading'); }
      return;
    }
  } else if (broker === 'stake') {
    if (res) res.innerHTML = '<span style="color:var(--amber)">⚠ Stake does not support bot-trading API</span>';
    if (btn) { btn.textContent = '▶ CONNECT BROKER'; btn.classList.remove('loading'); }
    return;
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
    if (res) res.innerHTML = `<span style="color:var(--red)">✗ ${e.message || 'Connection failed'}</span>`;
    pushAlert('BROKER', e.message || 'Connection failed', 'warning');
  } finally {
    if (btn) { btn.textContent = '▶ CONNECT BROKER'; btn.classList.remove('loading'); }
  }
}

async function loadRealPortfolio() {
  try {
    const d = await fetchJSON('/api/real/portfolio');
    const statsEl = el('livePortfolioStats');
    if (statsEl) statsEl.style.display = 'grid';
    setEl('liveAcctVal', fmt$(d.account_value || 0));
    setEl('liveBuyPow',  fmt$(d.buying_power  || 0));
    setEl('liveCash',    fmt$(d.cash          || 0));
    const body = el('livePositionsBody');
    if (body && d.positions) {
      if (!d.positions.length) {
        body.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:16px">No open positions</td></tr>`;
      } else {
        body.innerHTML = d.positions.map(p => {
          const pnlCls = (p.pnl || 0) >= 0 ? 'td-green' : 'td-red';
          return `<tr>
            <td class="td-cyan" style="font-weight:700">${p.ticker}</td>
            <td class="${p.side === 'LONG' || p.side === 'long' ? 'td-green' : 'td-red'}">${p.side?.toUpperCase()}</td>
            <td>${typeof p.qty === 'number' ? (p.qty % 1 === 0 ? p.qty : p.qty.toFixed(4)) : p.qty}</td>
            <td>${fmt$(p.avg_cost || 0)}</td>
            <td>${fmt$(p.market_val || 0)}</td>
            <td class="${pnlCls}">${p.pnl != null ? ((p.pnl >= 0 ? '+' : '') + fmt$(p.pnl)) : '—'}</td>
            <td class="${pnlCls}">${p.pnl_pct != null ? ((p.pnl_pct >= 0 ? '+' : '') + p.pnl_pct.toFixed(2) + '%') : '—'}</td>
            <td><button class="po-close-btn" onclick="closeLivePosition('${p.ticker}')">✕ CLOSE</button></td>
          </tr>`;
        }).join('');
      }
    }
  } catch {}
}

async function loadRealHistory() {
  try {
    const d = await fetchJSON('/api/real/history');
    const body = el('liveHistoryBody');
    if (!body || !d.history?.length) return;
    body.innerHTML = d.history.map(h => `<tr>
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
    if (res) res.innerHTML = `<span style="color:var(--red)">✗ ${e.message || 'Order failed'}</span>`;
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

function cliPrint(text, cls = '') {
  const out = el('cliOutput');
  if (!out) return;
  const div = document.createElement('div');
  div.className = 'cli-msg' + (cls ? ' cli-msg--' + cls : '');
  div.innerHTML = text;
  out.appendChild(div);
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

  cliPrint(`<span class="cli-prompt-echo">DALIOS&gt;</span> ${escHtml(cmd)}`, 'user');

  // Ensure CLI is open
  if (!_cliOpen) toggleCli();

  try {
    const d = await postJSON('/api/ai/chat', { message: cmd });
    cliPrint(formatCliResponse(d.response || d.reply || d.message || JSON.stringify(d)), 'ai');
  } catch (e) {
    cliPrint(`<span style="color:var(--red)">✗ ${escHtml(e.message || 'Error')}</span>`, 'error');
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
        return `<tr>
          <td class="td-cyan" style="font-weight:700">${p.ticker.replace('-USD','')}</td>
          <td class="${p.side === 'LONG' ? 'td-green' : 'td-red'}">${p.side}</td>
          <td>${p.qty % 1 === 0 ? p.qty : p.qty.toFixed(4)}</td>
          <td>${fmt$(p.entry_price)}</td>
          <td style="color:var(--text-1)">${fmt$(p.current_price)}</td>
          <td>${fmt$(p.market_value)}</td>
          <td class="${pnlCls}">${pnlTxt}</td>
          <td class="${pnlCls}">${pctTxt}</td>
          <td><button class="btn-ghost btn--sm" style="font-size:9px;padding:2px 6px" onclick="closePaperPosition('${p.ticker}')">✕</button></td>
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
    return `<div class="cc-pos-tile ${cls}">
      <div class="cc-pos-tile-top">
        <span class="cc-pos-tile-tkr">${ticker}</span>
        <span class="cc-pos-tile-pnl" style="color:${pos ? 'var(--green)' : 'var(--red)'}">${pnlTxt} (${pctTxt})</span>
        <button class="btn-ghost btn--sm" style="font-size:9px;padding:1px 5px;margin-left:4px" onclick="closePaperPosition('${p.ticker}')">✕</button>
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
    pushAlert('PAPER', `Close failed: ${e.message}`, 'warning');
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
    const d = await fetchJSON('/api/paper/live-pnl');
    if (!d || !d.positions) return;

    // 1. Update CC live position tiles (always visible on Command Centre)
    renderCcLivePositions(d.positions);

    // 2. In-place update paper trading table rows (avoids re-render flicker)
    _updatePaperTableInPlace(d.positions);

    // 3. Update quick-pos badge
    _updateQuickPosBadge(d.open_count || 0);
    // If panel is open, refresh its content too
    if (!el('quickPosPanel')?.classList.contains('hidden')) _refreshQuickPos();

    // 3. Update total unrealised P&L summary row if present
    const totalEl = el('paperUnrealisedTotal');
    if (totalEl) {
      const sign = d.total_unrealised_pnl >= 0 ? '+' : '';
      totalEl.textContent = sign + fmt$(d.total_unrealised_pnl);
      totalEl.style.color = d.total_unrealised_pnl >= 0 ? 'var(--green)' : 'var(--red)';
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
  loadPaperPortfolio();
  loadPaperHistory();
  loadPaperEquityCurve();
  loadCcOpportunities(8);
  loadQuadrant();
  loadCcRecommendations();
}

// ─── AI Recommendations ─────────────────────────────────────
async function loadCcRecommendations() {
  const list = el('ccRecsList');
  if (!list) return;
  list.innerHTML = '<div style="padding:14px;color:var(--text-muted);font-size:10px;animation:blink 1s infinite">⟳ RUNNING AI ANALYSIS…</div>';
  try {
    const d = await fetchJSON('/api/recommendations?n=6');
    renderCcRecommendations(d.recommendations || [], d.regime_label || '');
    // Update regime badge in stats bar
    const rb = el('ccRegimeBadge');
    if (rb && d.regime_label) rb.textContent = d.regime_label.toUpperCase();
  } catch(e) {
    list.innerHTML = `<div style="padding:14px;color:var(--red);font-size:10px">ANALYSIS FAILED: ${e.message}</div>`;
  }
}

function renderCcRecommendations(recs, regimeLabel) {
  const list = el('ccRecsList');
  if (!list) return;
  if (!recs || !recs.length) {
    list.innerHTML = '<div style="padding:14px;color:var(--text-muted);font-size:10px">No recommendations — load scanner tabs first</div>';
    return;
  }
  const fitCol = { strong:'var(--green)', moderate:'var(--primary)', neutral:'var(--text-2)', avoid:'var(--red)' };
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
        <button class="scan-trade-btn" onclick="event.stopPropagation();scannerOpenTrade('${r.ticker}',${r.price})">▲ TRADE</button>
        <button class="scan-wl-btn"    onclick="event.stopPropagation();toggleWatchlist('${r.ticker}',this)">☆ WATCH</button>
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
    const fitColour = { strong:'var(--green)', moderate:'var(--primary)', neutral:'var(--text-2)', avoid:'var(--red)' };
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
    list.innerHTML = `<div style="padding:14px;color:var(--red);font-size:10px">SCAN FAILED: ${e.message}</div>`;
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
    if (res) res.innerHTML = `<span style="color:var(--red)">✗ ${e.message}</span>`;
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
    icon: '₿', title: 'CRYPTO SCANNER',
    body: `<p>Live scanner for <strong>99 cryptocurrencies</strong>:</p>
      <ul>
        <li>Prices from CoinGecko free API (falls back to yfinance)</li>
        <li>Shows 24h % change, volume and market cap</li>
        <li>Sorted by trading volume (most liquid first)</li>
        <li>Covers Layer 1, DeFi, Gaming, Meme coins, AI tokens and more</li>
      </ul>
      <p>CoinGecko free tier may rate-limit — yfinance fallback kicks in automatically.</p>`
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
  'crypto-scanner':      3,
  'commodities-scanner': 4,
  'intel-center':        5,
  'risk-matrix':         6,
  'backtest-lab':        7,
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

function closeTutorial() {
  el('tutorialOverlay')?.classList.add('hidden');
}

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
