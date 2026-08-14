import { COLOURS, GAUGE_CAP, DASH } from "../lib/constants";

const CX = 100;
const CY = 96;
const R = 82;
const ARC_LENGTH = Math.PI * R; // radius 82 semicircle

/**
 * Semicircular z-risk gauge.
 *
 * The arc fills to a value capped at GAUGE_CAP, but the numeral prints the true
 * figure uncapped. z_risk is null once the backend stops vouching for held
 * scores: an empty arc reading "—" is honest, where 0.00 would read as a
 * reassuring result.
 *
 * Position is marked by a bead riding the arc rather than a needle pivoting
 * from the centre. A needle long enough to be legible sweeps straight through
 * the middle of the gauge, which is the only place the value itself can sit.
 */
export function Gauge({ z }) {
  const known = z !== null && z !== undefined && !Number.isNaN(Number(z));
  const value = known ? Number(z) : 0;
  const frac = known ? Math.min(1, Math.max(0, value / GAUGE_CAP)) : 0;

  const tint = !known
    ? COLOURS.unknown
    : value >= 2.5
      ? COLOURS.red
      : value >= 1.0
        ? COLOURS.amber
        : COLOURS.green;

  // theta sweeps pi (left end) -> 0 (right end) as frac goes 0 -> 1
  const theta = Math.PI * (1 - frac);
  const beadX = CX + R * Math.cos(theta);
  const beadY = CY - R * Math.sin(theta);

  return (
    <div className="mt-1 grid min-h-0 flex-1 place-items-center">
      <svg
        viewBox="0 0 200 116"
        preserveAspectRatio="xMidYMid meet"
        className="block h-full w-full"
      >
        <path
          d={`M 18 ${CY} A ${R} ${R} 0 0 1 182 ${CY}`}
          fill="none"
          stroke="#1a2035"
          strokeWidth="12"
          strokeLinecap="round"
        />
        <path
          d={`M 18 ${CY} A ${R} ${R} 0 0 1 182 ${CY}`}
          fill="none"
          stroke={tint}
          strokeWidth="12"
          strokeLinecap="round"
          strokeDasharray={ARC_LENGTH}
          strokeDashoffset={ARC_LENGTH * (1 - frac)}
          style={{ transition: "stroke-dashoffset .4s ease, stroke .4s ease" }}
        />

        {known && (
          <circle
            cx={beadX}
            cy={beadY}
            r="6.5"
            fill="#e8edf5"
            stroke="#080c14"
            strokeWidth="2.5"
            style={{ transition: "cx .4s ease, cy .4s ease" }}
          />
        )}

        {/* Value sits inside the arc, where nothing else can reach it. */}
        <text
          x={CX}
          y="86"
          textAnchor="middle"
          fill="#e8edf5"
          fontSize="34"
          fontWeight="700"
          fontFamily="Courier New, Courier, monospace"
        >
          {known ? value.toFixed(2) : DASH}
        </text>

        <text x="14" y="112" fill="#4a5568" fontSize="11" fontFamily="Courier New, monospace">
          0
        </text>
        <text x="174" y="112" fill="#4a5568" fontSize="11" fontFamily="Courier New, monospace">
          {GAUGE_CAP}+
        </text>
      </svg>
    </div>
  );
}
