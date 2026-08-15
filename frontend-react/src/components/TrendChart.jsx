import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const SERIES = [
  { key: "bloodLoss", name: "Blood loss (mL)", colour: "#e8365d", axis: "left" },
  { key: "rate", name: "Rate (mL/min)", colour: "#f79009", axis: "left" },
  { key: "pulse", name: "Pulse (bpm)", colour: "#7a5af8", axis: "right" },
  { key: "spo2", name: "SpO₂ (%)", colour: "#2e90fa", axis: "right" },
];

export default function TrendChart({ data = [] }) {
  if (data.length < 2) {
    return (
      <div
        style={{
          height: 320,
          display: "grid",
          placeItems: "center",
          color: "var(--faint)",
          fontSize: 13,
        }}
      >
        Collecting data — the trend needs at least two readings.
      </div>
    );
  }

  // A series with nothing in it belongs to a sensor that is not fitted. Drawing
  // an empty line and a legend entry for it would imply it is being measured.
  const present = SERIES.filter((s) =>
    data.some((d) => d[s.key] !== null && d[s.key] !== undefined),
  );

  return (
    <div style={{ height: 320 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: -8 }}>
          <CartesianGrid stroke="#f0eeea" vertical={false} />
          <XAxis
            dataKey="t"
            tick={{ fontSize: 10, fill: "#98a2b3" }}
            tickLine={false}
            axisLine={{ stroke: "#ece9e4" }}
            minTickGap={40}
          />
          <YAxis
            yAxisId="left"
            tick={{ fontSize: 10, fill: "#98a2b3" }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            yAxisId="right"
            orientation="right"
            tick={{ fontSize: 10, fill: "#98a2b3" }}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip
            contentStyle={{
              borderRadius: 10,
              border: "1px solid #ece9e4",
              boxShadow: "0 4px 12px rgba(16,24,40,.08)",
              fontSize: 12,
            }}
          />
          <Legend wrapperStyle={{ fontSize: 11, paddingTop: 6 }} iconType="plainline" />
          {present.map((s) => (
            <Line
              key={s.key}
              yAxisId={s.axis}
              type="monotone"
              dataKey={s.key}
              name={s.name}
              stroke={s.colour}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
              connectNulls={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
