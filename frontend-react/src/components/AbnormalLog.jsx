import { TRIAGE_COLOUR } from "../context/WebSocketContext";

export default function AbnormalLog({ events = [] }) {
  if (!events.length) {
    return (
      <div
        style={{
          padding: "48px 0",
          textAlign: "center",
          color: "var(--faint)",
          fontSize: 13,
        }}
      >
        No abnormal events recorded.
        <div style={{ fontSize: 11, marginTop: 6 }}>
          Entries are logged from live readings only.
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {events.map((e, i) => {
        const tint = TRIAGE_COLOUR[e.severity] || TRIAGE_COLOUR.amber;
        return (
          <div
            key={`${e.time}-${e.label}-${i}`}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "12px 4px",
              borderBottom: i === events.length - 1 ? "none" : "1px solid var(--edge)",
            }}
          >
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                background: tint,
                flex: "none",
              }}
            />
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>{e.label}</div>
              <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 1 }}>
                {e.time ? new Date(e.time).toLocaleTimeString("en-GB") : "—"}
              </div>
            </div>
            <div
              style={{
                fontSize: 13,
                fontWeight: 700,
                color: tint,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {e.value}
            </div>
          </div>
        );
      })}
    </div>
  );
}
