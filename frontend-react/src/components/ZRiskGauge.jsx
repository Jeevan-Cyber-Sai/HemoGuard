import { TRIAGE_COLOUR } from "../context/WebSocketContext";

const R = 74;
const CX = 100;
const CY = 92;
const ARC = Math.PI * R;

/**
 * Semicircular risk gauge, 0-100.
 *
 * A bead rides the arc rather than a needle pivoting from the centre: a needle
 * long enough to read across a room sweeps straight through the only place the
 * number itself can sit.
 */
export default function ZRiskGauge({ score, triage = "unknown" }) {
  const known = score !== null && score !== undefined && Number.isFinite(score);
  const value = known ? Math.max(0, Math.min(100, score)) : 0;
  const frac = value / 100;
  const tint = known ? TRIAGE_COLOUR[triage] || TRIAGE_COLOUR.unknown : "#d0d5dd";

  const theta = Math.PI * (1 - frac);
  const bx = CX + R * Math.cos(theta);
  const by = CY - R * Math.sin(theta);

  return (
    <div style={{ position: "relative", width: "100%" }}>
      <svg viewBox="0 0 200 112" style={{ width: "100%", display: "block" }}>
        <path
          d={`M ${CX - R} ${CY} A ${R} ${R} 0 0 1 ${CX + R} ${CY}`}
          fill="none"
          stroke="#f0eeea"
          strokeWidth="12"
          strokeLinecap="round"
        />
        <path
          d={`M ${CX - R} ${CY} A ${R} ${R} 0 0 1 ${CX + R} ${CY}`}
          fill="none"
          stroke={tint}
          strokeWidth="12"
          strokeLinecap="round"
          strokeDasharray={ARC}
          strokeDashoffset={ARC * (1 - frac)}
          style={{ transition: "stroke-dashoffset 500ms ease, stroke 500ms ease" }}
        />
        {known && (
          <circle
            cx={bx}
            cy={by}
            r="6"
            fill="#fff"
            stroke={tint}
            strokeWidth="3"
            style={{ transition: "cx 500ms ease, cy 500ms ease" }}
          />
        )}
        <text
          x={CX}
          y="84"
          textAnchor="middle"
          fontSize="30"
          fontWeight="700"
          fill={known ? "#1d2939" : "#98a2b3"}
        >
          {known ? Math.round(value) : "—"}
        </text>
        <text x={CX} y="104" textAnchor="middle" fontSize="10" fill="#98a2b3">
          RISK SCORE / 100
        </text>
      </svg>
    </div>
  );
}
