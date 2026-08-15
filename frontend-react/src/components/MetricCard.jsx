import { useEffect, useRef, useState } from "react";

/**
 * A single metric tile.
 *
 * `value` of null renders an em dash and the "not available" note rather than a
 * zero - an unfitted or unmeasured channel must never read as a measurement of
 * zero, which on a patient monitor is a clinical statement in itself.
 */
export default function MetricCard({
  label,
  value,
  unit,
  decimals = 0,
  accent,
  icon,
  note,
  tag,
  countUp = false,
}) {
  const [flash, setFlash] = useState(0);
  const [shown, setShown] = useState(value);
  const prev = useRef(value);
  const raf = useRef(null);

  // Value update flash, only when the number actually changed - a feed
  // repeating the same reading each second would otherwise strobe.
  useEffect(() => {
    if (value === prev.current) return;

    if (!countUp || value === null || prev.current === null) {
      setShown(value);
    } else {
      // Count-up: ease from the old figure to the new one over ~420 ms.
      const from = prev.current;
      const to = value;
      const start = performance.now();
      const tick = (now) => {
        const t = Math.min(1, (now - start) / 420);
        const eased = 1 - Math.pow(1 - t, 3);
        setShown(from + (to - from) * eased);
        if (t < 1) raf.current = requestAnimationFrame(tick);
      };
      cancelAnimationFrame(raf.current);
      raf.current = requestAnimationFrame(tick);
    }

    prev.current = value;
    setFlash((n) => n + 1);
  }, [value, countUp]);

  useEffect(() => () => cancelAnimationFrame(raf.current), []);

  const display =
    shown === null || shown === undefined ? "—" : Number(shown).toFixed(decimals);

  return (
    <div className="card" style={{ padding: 18, position: "relative" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {icon}
        <span className="label">{label}</span>
        {tag && (
          <span
            style={{
              marginLeft: "auto",
              fontSize: 9,
              fontWeight: 700,
              letterSpacing: "0.1em",
              color: "var(--amber)",
              border: "1px solid var(--amber)",
              borderRadius: 4,
              padding: "2px 5px",
            }}
          >
            {tag}
          </span>
        )}
      </div>

      <div
        key={flash}
        className={flash ? "value-flash" : ""}
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 7,
          marginTop: 12,
          transformOrigin: "left center",
        }}
      >
        <span
          className="value-lg triage-tint"
          style={{ color: value === null ? "var(--faint)" : accent || "var(--ink)" }}
        >
          {display}
        </span>
        {unit && <span className="unit">{unit}</span>}
      </div>

      <div style={{ marginTop: 6, fontSize: 11, color: "var(--faint)", minHeight: 14 }}>
        {value === null ? note || "not available" : note || ""}
      </div>
    </div>
  );
}
