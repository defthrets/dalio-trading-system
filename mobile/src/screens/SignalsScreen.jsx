import React, { useState, useEffect } from 'react';
import { View, Text, ScrollView, StyleSheet, RefreshControl } from 'react-native';
import { getSignals } from '../lib/api';

const fmt = (n, d = 2) => n != null ? Number(n).toFixed(d) : '--';
const fmtCur = (n) => n != null ? `$${Number(n).toLocaleString('en-AU', { minimumFractionDigits: 2 })}` : '--';

export default function SignalsScreen() {
  const [signals, setSignals] = useState([]);
  const [refreshing, setRefreshing] = useState(false);

  const load = async () => {
    try {
      const data = await getSignals();
      setSignals(data?.signals || data || []);
    } catch (e) { console.warn(e.message); }
  };

  useEffect(() => { load(); }, []);
  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  return (
    <ScrollView
      style={styles.container}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#ff8c00" />}
    >
      <Text style={styles.title}>TRADE SIGNALS ({signals.length})</Text>
      {signals.length === 0 ? (
        <Text style={styles.empty}>No signals. Waiting for next scan cycle.</Text>
      ) : (
        signals.map((s, i) => (
          <View key={i} style={styles.card}>
            <View style={styles.cardHeader}>
              <Text style={styles.ticker}>{s.ticker}</Text>
              <View style={[styles.actionBadge, s.action === 'BUY' ? styles.buyBg : styles.sellBg]}>
                <Text style={[styles.actionText, { color: s.action === 'BUY' ? '#00e676' : '#ff1744' }]}>
                  {s.action}
                </Text>
              </View>
              <Text style={[styles.confidence, {
                color: s.confidence > 70 ? '#00e676' : s.confidence > 50 ? '#ffd600' : '#ff1744',
              }]}>
                {fmt(s.confidence, 0)}%
              </Text>
            </View>
            <View style={styles.details}>
              <Text style={styles.detail}>Price: {fmtCur(s.price)}</Text>
              <Text style={[styles.detail, { color: '#ff1744' }]}>SL: {fmtCur(s.stop_loss)}</Text>
              <Text style={[styles.detail, { color: '#00e676' }]}>TP: {fmtCur(s.take_profit)}</Text>
              <Text style={styles.detail}>RSI: {fmt(s.rsi, 1)}</Text>
            </View>
            {s.reasons && s.reasons.length > 0 && (
              <View style={styles.reasons}>
                {s.reasons.slice(0, 2).map((r, j) => (
                  <Text key={j} style={styles.reason}>{r}</Text>
                ))}
              </View>
            )}
          </View>
        ))
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#050508', padding: 16 },
  title: { fontSize: 11, letterSpacing: 1.5, color: '#5a6474', marginBottom: 12 },
  empty: { color: '#5a6474', textAlign: 'center', padding: 40 },
  card: {
    backgroundColor: '#0d1218', borderWidth: 1, borderColor: 'rgba(255,255,255,0.06)',
    borderRadius: 8, padding: 14, marginBottom: 8,
  },
  cardHeader: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 8 },
  ticker: { fontFamily: 'monospace', fontSize: 16, fontWeight: '700', color: '#e8e8e8', flex: 1 },
  actionBadge: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: 3 },
  buyBg: { backgroundColor: 'rgba(0,230,118,0.15)' },
  sellBg: { backgroundColor: 'rgba(255,23,68,0.15)' },
  actionText: { fontFamily: 'monospace', fontSize: 11, fontWeight: '600' },
  confidence: { fontFamily: 'monospace', fontSize: 14, fontWeight: '700' },
  details: { flexDirection: 'row', flexWrap: 'wrap', gap: 12 },
  detail: { fontFamily: 'monospace', fontSize: 11, color: '#a0a8b4' },
  reasons: { marginTop: 8, paddingTop: 8, borderTopWidth: 1, borderTopColor: 'rgba(255,255,255,0.06)' },
  reason: { fontSize: 11, color: '#5a6474', marginBottom: 2 },
});
