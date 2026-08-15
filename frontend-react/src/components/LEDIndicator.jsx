const PHASES = ["RED", "GREEN", "IR"];
const TINT = { RED: "#e8365d", GREEN: "#12b76a", IR: "#7a5af8" };

/**
 * Which illumination LED is lit right now.
 *
 * Every optical number on screen belongs to whichever LED was on when it was
 * taken, so this makes the phase visible and a reading can never be read
 * against the wrong illumination.
 */
export default function LEDIndicator({ active, live = true }) {
  const current = String(active || "").toUpperCase();

  return (
    <div>
      <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
        {PHASES.map((name) => {
          const on = name === current;
          const tint = TINT[name];
          return (
            <div key={name} style={{ display: "flex", alignItems: "center", gap: 7 }}>
              <span
                className="triage-tint"
                style={{
                  width: 9,
                  height: 9,
                  borderRadius: "50%",
                  background: on ? tint : "#e4e7ec",
                  boxShadow: on ? `0 0 8px ${tint}` : "none",
                }}
              />
              <span
                className="triage-tint"
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  letterSpacing: "0.06em",
                  color: on ? tint : "var(--faint)",
                }}
              >
                {name}
              </span>
            </div>
          );
        })}
      </div>
      <div style={{ marginTop: 8, fontSize: 10, letterSpacing: "0.1em", color: "var(--faint)" }}>
        LED CYCLE — {live ? "LIVE" : "HELD"}
      </div>
    </div>
  );
}
