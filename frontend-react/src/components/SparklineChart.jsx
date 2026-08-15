import { Line, LineChart, ResponsiveContainer, YAxis } from "recharts";
import { TRIAGE_COLOUR } from "../context/WebSocketContext";

/**
 * 60-point sparkline, no axes, coloured by the current triage band.
 *
 * `connectNulls` is deliberately off: a gap in the data is a gap in the
 * measurement, and bridging it would draw a line through readings that were
 * never taken.
 */
export default function SparklineChart({
  data = [],
  dataKey = "risk",
  triage = "unknown",
  height = 48,
  domain = ["auto", "auto"],
}) {
  const tint = TRIAGE_COLOUR[triage] || TRIAGE_COLOUR.unknown;
  const points = data.filter((d) => d[dataKey] !== null && d[dataKey] !== undefined);

  if (points.length < 2) {
    return (
      <div
        style={{
          height,
          display: "flex",
          alignItems: "center",
          fontSize: 11,
          color: "var(--faint)",
        }}
      >
        collecting…
      </div>
    );
  }

  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 2, bottom: 2, left: 2 }}>
          <YAxis hide domain={domain} />
          <Line
            type="monotone"
            dataKey={dataKey}
            stroke={tint}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
            connectNulls={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
