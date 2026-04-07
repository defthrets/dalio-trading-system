import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip } from 'recharts';

export default function EquityChart({ data = [], height = 200 }) {
  if (!data.length) return <div className="empty-state">No equity data</div>;

  const formatted = data.map((d, i) => ({
    idx: i,
    value: typeof d === 'number' ? d : d.value ?? d.equity ?? 0,
    date: d.timestamp ? new Date(d.timestamp).toLocaleDateString() : `#${i}`,
  }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={formatted}>
        <defs>
          <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#ff8c00" stopOpacity={0.3} />
            <stop offset="100%" stopColor="#ff8c00" stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis
          dataKey="date"
          tick={{ fill: '#5a6474', fontSize: 10 }}
          axisLine={{ stroke: '#1a2230' }}
          tickLine={false}
        />
        <YAxis
          tick={{ fill: '#5a6474', fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`}
          width={60}
        />
        <Tooltip
          contentStyle={{
            background: '#111820',
            border: '1px solid rgba(255,255,255,0.06)',
            borderRadius: 6,
            fontSize: 12,
          }}
          formatter={(v) => [`$${v.toLocaleString()}`, 'Equity']}
        />
        <Area
          type="monotone"
          dataKey="value"
          stroke="#ff8c00"
          strokeWidth={2}
          fill="url(#eqGrad)"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
