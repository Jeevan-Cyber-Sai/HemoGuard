import { useNavigate } from "react-router-dom";
import { useHemoGuard } from "../context/WebSocketContext";

/**
 * Ward-wide critical banner. Breathes until acknowledged - motion is what
 * carries across a room, and it is the one thing acknowledgement may stop.
 */
export default function CriticalBanner() {
  const { triage, acknowledged, acknowledge, alarming } = useHemoGuard();
  const navigate = useNavigate();

  if (triage !== "red") return null;

  return (
    <div
      className={`banner-down ${alarming ? "breathe" : ""}`}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "12px 24px",
        background: "var(--primary)",
        color: "#fff",
      }}
    >
      <span style={{ fontSize: 16 }}>⚠</span>
      <div style={{ fontWeight: 700, fontSize: 14, letterSpacing: "0.02em" }}>
        CRITICAL — Bed 4 requires immediate attention
      </div>

      <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
        <button
          onClick={() => navigate("/critical")}
          style={{
            padding: "7px 14px",
            borderRadius: 8,
            border: "1px solid rgba(255,255,255,.5)",
            color: "#fff",
            fontSize: 12,
            fontWeight: 600,
          }}
        >
          View
        </button>
        {!acknowledged && (
          <button
            onClick={acknowledge}
            style={{
              padding: "7px 16px",
              borderRadius: 8,
              background: "#fff",
              color: "var(--primary)",
              fontSize: 12,
              fontWeight: 700,
            }}
          >
            ACKNOWLEDGE
          </button>
        )}
        {acknowledged && (
          <span style={{ fontSize: 12, opacity: 0.85, alignSelf: "center" }}>
            Acknowledged
          </span>
        )}
      </div>
    </div>
  );
}
