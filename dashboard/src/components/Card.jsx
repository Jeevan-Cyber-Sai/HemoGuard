import { useEffect, useRef, useState } from "react";

/**
 * Card shell. `accent` paints the top rule, `absent` marks a sensor this node
 * does not carry, and `flashKey` ticks the card whenever the value behind it
 * actually changed.
 */
export function Card({ label, accent, absent, absentLabel = "NOT FITTED",
                       tag, tagColor = "#ffab00",
                       flashKey, className = "", children }) {
  const [flash, setFlash] = useState(0);
  const prev = useRef(flashKey);

  // Comparing first keeps the flash meaningful: a feed repeating the same
  // number every second would otherwise strobe continuously.
  useEffect(() => {
    if (flashKey === undefined || flashKey === prev.current) return;
    prev.current = flashKey;
    setFlash((n) => n + 1);
  }, [flashKey]);

  return (
    <div
      key={undefined}
      className={
        "relative flex min-h-0 flex-col overflow-hidden rounded-md border " +
        "border-edge bg-card px-[1.1vh] py-[1.2vh] transition-colors duration-500 " +
        className
      }
      style={{ borderTopWidth: 2, borderTopColor: absent ? "#1a2035" : accent }}
    >
      {/* Keyed remount is what restarts the CSS animation; without a fresh key
          the browser keeps the finished one and the card never flashes twice. */}
      <span
        key={flash}
        className={flash ? "hg-flash pointer-events-none absolute inset-0" : "hidden"}
        aria-hidden="true"
      />

      {label && (
        <div
          className="relative font-semibold uppercase tracking-[0.16em] text-muted"
          style={{ fontSize: "var(--v-label)" }}
        >
          {label}
        </div>
      )}

      {absent && (
        <div
          className="absolute right-3 top-2 font-mono tracking-[0.14em] text-muted"
          style={{ fontSize: "calc(var(--v-label) * 0.92)" }}
        >
          {absentLabel}
        </div>
      )}

      {/* Provenance warning - a value that is present but not measured. It sits
          where NOT FITTED would, because it answers the same question: how much
          should you trust the number under it. */}
      {!absent && tag && (
        <div
          className="absolute right-3 top-2 rounded border px-1.5 py-0.5 font-mono tracking-[0.14em]"
          style={{
            fontSize: "calc(var(--v-label) * 0.9)",
            borderColor: tagColor,
            color: tagColor,
          }}
        >
          {tag}
        </div>
      )}

      <div className={"relative flex min-h-0 flex-1 flex-col " + (absent ? "opacity-30" : "")}>
        {children}
      </div>
    </div>
  );
}

/**
 * Large numeric readout with its unit.
 *
 * Takes the free space (flex-1) and centres inside it rather than sitting on
 * the card floor. Pinned to the bottom, the numeral left a dead band under the
 * label that made every tile read as mostly empty.
 */
export function Readout({ value, unit, size = "var(--v-hero)" }) {
  return (
    <div className="flex flex-1 items-center gap-2">
      <span
        className="font-mono font-bold leading-none tabular-nums tracking-tighter"
        style={{ fontSize: size }}
      >
        {value}
      </span>
      {unit && (
        <span
          className="font-mono text-muted"
          style={{ fontSize: "calc(var(--v-label) * 1.5)" }}
        >
          {unit}
        </span>
      )}
    </div>
  );
}
