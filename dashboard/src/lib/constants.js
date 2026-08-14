// Transport and scoring constants, carried over unchanged from the original
// dashboard. In a production build the URLs resolve exactly as before; in dev
// they go through the Vite proxy so the page stays same-origin.

const DEV = import.meta.env.DEV;
const HOST = location.hostname || "localhost";

export const WS_URL = DEV
  ? `ws://${location.host}/ws`
  : `ws://${HOST}:8000/ws`;

export const API_URL = DEV ? "" : `http://${HOST}:8000`;

export const RETRY_MS = 3000;
export const MAX_POINTS = 60;
// Matches the backend's per-channel Z_CLAMP: every channel is capped at 6
// sigma, so the weighted score cannot exceed 6 either. The old cap of 15 was
// set when z was unbounded, and left the needle in the bottom 40% of the arc
// for every reading the system can actually produce.
export const GAUGE_CAP = 6;

export const COLOURS = {
  green: "#00e676",
  amber: "#ffab00",
  red: "#ff1744",
  unknown: "#4a5568",
};

export const TRIAGE_TEXT = {
  green: "LOW",
  amber: "WATCH",
  red: "CRITICAL",
  unknown: "NO DATA",
};

// Statuses that mean "what is on screen is not a live measurement". The node
// reports sensor_invalid while the backend is still replaying the last good
// scores, and sensor_fault once it has given up on them. "waiting" belongs here
// too: on a freshly started backend no reading has arrived yet, and a LIVE
// badge over a dashboard that has never seen the node is the exact failure this
// table exists to prevent.
export const DEGRADED = {
  stale: "STALE",
  offline: "OFFLINE",
  waiting: "WAITING",
  sensor_invalid: "SENSOR",
  sensor_fault: "SENSOR FAULT",
};

export const LED_TINT = { RED: "#ff1744", GREEN: "#00e676", IR: "#7c4dff" };

export const DASH = "—";

// Formats a number, or returns the dash when the channel reported nothing.
// null is "not fitted" or "withdrawn", never a value to render as zero.
export function num(v, dp, fallback = DASH) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return fallback;
  return Number(v).toFixed(dp);
}
