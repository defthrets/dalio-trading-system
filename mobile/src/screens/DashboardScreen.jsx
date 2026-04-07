import React, { useState, useEffect } from 'react';
import { View, Text, ScrollView, StyleSheet, RefreshControl } from 'react-native';
import { getStatus, getPortfolioHealth, getQuadrant } from '../lib/api';

const fmt = (n, d = 2) => n != null ? Number(n).toFixed(d) : '--';
const fmtCur = (n) => n != null ? `$${Number(n).toLocaleString('en-AU', { minimumFractionDigits: 2 })}` : '--';

export default function DashboardScreen() {
  const [status, setStatus] = useState(null);
  const [health, setHealth] = useState(null);
  const [quadrant, setQuadrant] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = async () => {
    try {
      const [s, h, q] = await Promise.all([getStatus(), getPortfolioHealth(), getQuadrant()]);
      setStatus(s);
      setHealth(h);
      setQuadrant(q);
    } catch (e) {
      console.warn('Dashboard load error:', e.message);
    }
  };

  useEffect(() => { load(); const id = setInterval(load, 15000); return () => clearInterval(id); }, []);

  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  const nav = health?.equity ?? health?.nav ?? 0;
  const pnl = health?.daily_pnl_pct ?? 0;
  const dd = health?.drawdown_pct ?? 0;

  return (
    <ScrollView
      style={styles.container}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#ff8c00" />}
    >
      {/* Mode badge */}
      <View style={styles.modeRow}>
        <View style={[styles.modeBadge, status?.mode === 'LIVE' ? styles.modeLive : styles.modePaper]}>
          <Text style={styles.modeText}>{status?.mode || 'PAPER'}</Text>
        </View>
        <View style={[styles.statusDot, { backgroundColor: status ? '#00e676' : '#ff1744' }]} />
        <Text style={styles.statusText}>{status?.status || 'CONNECTING'}</Text>
      </View>

      {/* Stats */}
      <View style={styles.statsGrid}>
        <StatCard label="NAV" value={fmtCur(nav)} />
        <StatCard label="Today P&L" value={`${pnl >= 0 ? '+' : ''}${fmt(pnl)}%`} positive={pnl >= 0} />
        <StatCard label="Drawdown" value={`${fmt(dd)}%`} positive={dd < 5} />
        <StatCard label="Positions" value={health?.open_positions ?? 0} />
      </View>

      {/* Quadrant */}
      <View style={styles.panel}>
        <Text style={styles.panelTitle}>ECONOMIC QUADRANT</Text>
        <Text style={styles.quadrantValue}>
          {(quadrant?.quadrant || 'unknown').replace('_', ' ').toUpperCase()}
        </Text>
        <Text style={styles.quadrantDesc}>
          {quadrant?.description || 'Loading...'}
        </Text>
      </View>
    </ScrollView>
  );
}

function StatCard({ label, value, positive }) {
  return (
    <View style={styles.statCard}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={[
        styles.statValue,
        positive === true && { color: '#00e676' },
        positive === false && { color: '#ff1744' },
      ]}>
        {value}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#050508', padding: 16 },
  modeRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 16 },
  modeBadge: { paddingHorizontal: 12, paddingVertical: 4, borderRadius: 4 },
  modePaper: { backgroundColor: '#40c4ff' },
  modeLive: { backgroundColor: '#ff1744' },
  modeText: { fontFamily: 'monospace', fontSize: 11, fontWeight: '700', color: '#000' },
  statusDot: { width: 8, height: 8, borderRadius: 4 },
  statusText: { color: '#5a6474', fontSize: 12 },
  statsGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 16 },
  statCard: {
    flex: 1, minWidth: '45%', backgroundColor: '#0d1218',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.06)',
    borderRadius: 8, padding: 14,
  },
  statLabel: { fontSize: 10, textTransform: 'uppercase', letterSpacing: 1, color: '#5a6474', marginBottom: 4 },
  statValue: { fontFamily: 'monospace', fontSize: 20, fontWeight: '700', color: '#e8e8e8' },
  panel: {
    backgroundColor: '#0d1218', borderWidth: 1, borderColor: 'rgba(255,255,255,0.06)',
    borderRadius: 8, padding: 16, marginBottom: 16,
  },
  panelTitle: { fontSize: 11, textTransform: 'uppercase', letterSpacing: 1, color: '#5a6474', marginBottom: 8 },
  quadrantValue: { fontFamily: 'monospace', fontSize: 18, fontWeight: '700', color: '#ff8c00', marginBottom: 8 },
  quadrantDesc: { color: '#a0a8b4', fontSize: 13, lineHeight: 20 },
});
