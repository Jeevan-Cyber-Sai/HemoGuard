/**
 * ECG rhythm strip, pure SVG.
 *
 * One beat lives in <defs> and is stamped eight times; the group scrolls a full
 * half-width so the loop is seamless. The sweep period is four beats at the
 * reported rate, so the strip scrolls at the rate the patient is actually
 * beating. No pulse means no rhythm: it flatlines rather than inventing one.
 */
export function Ecg({ bpm }) {
  const beating = Number.isFinite(bpm) && bpm > 0;
  const period = beating ? (4 * 60) / bpm : 3.2;

  return (
    <div
      className="w-full shrink-0 overflow-hidden"
      style={{ height: "clamp(16px, 3.6vh, 34px)" }}
    >
      <svg viewBox="0 0 400 40" preserveAspectRatio="none" className="block h-full w-full">
        <defs>
          <path
            id="hg-beat"
            d="M0,20 H30 L34,20 L38,7 L44,33 L48,20 H54 L60,15 L66,20 H100"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </defs>

        {beating ? (
          <g className="hg-sweep text-safe" style={{ "--ecg-period": `${period}s` }}>
            {[0, 100, 200, 300, 400, 500, 600, 700].map((x) => (
              <use key={x} href="#hg-beat" x={x} />
            ))}
          </g>
        ) : (
          <line x1="0" y1="20" x2="400" y2="20" stroke="#4a5568" strokeWidth="1.6" />
        )}
      </svg>
    </div>
  );
}
