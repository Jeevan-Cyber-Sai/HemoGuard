import { LED_TINT } from "../lib/constants";

const PHASES = ["RED", "GREEN", "IR"];

/**
 * Persistent LED rotation indicator.
 *
 * The node cycles every 2 s, and every optical number on screen belongs to
 * whichever LED was lit when it was taken. This makes that visible at a glance
 * so a reading is never read against the wrong illumination.
 */
export function LedCycle({ led, live }) {
  const active = String(led || "").toUpperCase();

  return (
    <div className="shrink-0 rounded-md border border-edge bg-card px-3 py-[0.7vh]">
      <div className="flex items-center justify-center gap-5">
        {PHASES.map((name) => {
          const on = name === active;
          const tint = LED_TINT[name];
          return (
            <div key={name} className="flex items-center gap-1.5">
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-full transition-all duration-500"
                style={{
                  backgroundColor: on ? tint : "#1a2035",
                  boxShadow: on ? `0 0 9px ${tint}` : "none",
                }}
              />
              <span
                className="font-mono font-bold tracking-[0.12em] transition-colors duration-500"
                style={{ fontSize: "var(--v-label)", color: on ? tint : "#4a5568" }}
              >
                {name}
              </span>
            </div>
          );
        })}
      </div>
      <div
        className="mt-1 text-center font-mono tracking-[0.22em] text-muted"
        style={{ fontSize: "calc(var(--v-label) * 0.88)" }}
      >
        LED CYCLE — {live ? "LIVE" : "HELD"}
      </div>
    </div>
  );
}
