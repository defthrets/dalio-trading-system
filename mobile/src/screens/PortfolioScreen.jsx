import React, { useState, useEffect } from 'react';
import { View, Text, ScrollView, StyleSheet, RefreshControl } from 'react-native';
import { getPaperPortfolio, getPaperAnalytics } from '../lib/api';

const fmt = (n, d = 2) => n != null ? Number(n).toFixed(d) : '--';
const fmtCur = (n) => n != null ? `$${Number(n).toLocaleString('en-AU', { minimumFractionDigits: 2 })}` : '--';

export default function PortfolioScreen() {
  const [portfolio, setPortfolio] = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = async () => {
    try {
      const [p, a] = await Promise.all([getPaperPortfolio(), getPaperAnalytics()]);
      setPortfolio(p);
      setAnalytics(a);
    } catch (e) { console.warn(e.message); }
  };

  useEffect(() => { load(); }, []);

  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };
  const positions = portfolio?.positions || [];

  return (
    <ScrollView
      style={styles.container}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#ff8c00" />}
    >
      {/* Cash & Stats */}
      <View style={styles.statsRow}>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Cash</Text>
          <Text style={styles.statValue}>{fmtCur(portfolio?.cash)}</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Win Rate</Text>
          <Text style={styles.statValue}>{fmt(analytics?.win_rate, 1)}%</Text>
        </View>
      </View>

      {/* Positions */}
      <Text style={styles.sectionTitle}>OPEN POSITIONS ({positions.length})</Text>
      {positions.length === 0 ? (
        <Text style={styles.empty}>No open positions</Text>
      ) : (
        positions.map((p, i) => {
          const pnl = (p.current_price - p.entry_price) * p.qty;
          const pnlPct = p.entry_price ? ((p.current_price - p.entry_price) / p.entry_price * 100) : 0;
          const isPos = pnl >= 0;
          return (
            <View key={i} style={styles.posCard}>
              <View style={styles.posHeader}>
                <Text style={styles.posTicker}>{p.ticker}</Text>
                <View style={[styles.sideBadge, p.side === 'BUY' ? styles.buyBg : styles.sellBg]}>
                  <Text style={styles.sideText}>{p.side}</Text>
                </View>
              </View>
              <View style={styles.posDetails}>
                <Text style={styles.posDetail}>Qty: {p.qty}</Text>
                <Text style={styles.posDetail}>Entry: {fmtCur(p.entry_price)}</Text>
                <Text style={[styles.posDetail, { color: isPos ? '#00e676' : '#ff1744' }]}>
                  P&L: {fmtCur(pnl)} ({isPos ? '+' : ''}{fmt(pnlPct)}%)
                </Text>
              </View>
            </View>
          );
        })
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#050508', padding: 16 },
  statsRow: { flexDirection: 'row', gap: 8, marginBottom: 16 },
  stat: {
    flex: 1, backgroundColor: '#0d1218', borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.06)', borderRadius: 8, padding: 14,
  },
  statLabel: { fontSize: 10, textTransform: 'uppercase', letterSpacing: 1, color: '#5a6474' },
  statValue: { fontFamily: 'monospace', fontSize: 18, fontWeight: '700', color: '#e8e8e8', marginTop: 4 },
  sectionTitle: { fontSize: 11, letterSpacing: 1.5, color: '#5a6474', marginBottom: 8, marginTop: 8 },
  empty: { color: '#5a6474', textAlign: 'center', padding: 24 },
  posCard: {
    backgroundColor: '#0d1218', borderWidth: 1, borderColor: 'rgba(255,255,255,0.06)',
    borderRadius: 8, padding: 14, marginBottom: 8,
  },
  posHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  posTicker: { fontFamily: 'monospace', fontSize: 16, fontWeight: '700', color: '#e8e8e8' },
  sideBadge: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: 3 },
  buyBg: { backgroundColor: 'rgba(0,230,118,0.15)' },
  sellBg: { backgroundColor: 'rgba(255,23,68,0.15)' },
  sideText: { fontFamily: 'monospace', fontSize: 11, fontWeight: '600', color: '#e8e8e8' },
  posDetails: { gap: 4 },
  posDetail: { fontFamily: 'monospace', fontSize: 12, color: '#a0a8b4' },
});
