import {
  useHemoGuard,
  TRIAGE_COLOUR,
  TRIAGE_LABEL,
} from "../context/WebSocketContext";

/**
 * Full-width triage band, mirroring the ward display.
 *
 * Breathes only while alarming, so acknowledgement visibly stops something -
 * the colour stays red until the condition itself clears.
 */
export default function TriageBar() {
  const { triage, alarming, acknowledged, acknowledge } = useHemoGuard();
  const tint = TRIAGE_COLOUR[triage] || TRIAGE_COLOUR.unknown;

  return (
    <div
      className={`card triage-tint ${alarming ? "breathe" : ""}`}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 18,
        padding: "14px 20px",
        borderLeft: `4px solid ${tint}`,
      }}
    >
      <span className="label">Triage band</span>
      <span
        className="triage-tint"
        style={{
          fontSize: 22,
          fontWeight: 700,
          letterSpacing: "0.12em",
          color: tint,
        }}
      >
        {TRIAGE_LABEL[triage] || TRIAGE_LABEL.unknown}
      </span>

      {triage === "red" && !acknowledged && (
        <button
          onClick={acknowledge}
          style={{
            marginLeft: "auto",
            padding: "9px 22px",
            borderRadius: 8,
            background: "var(--primary)",
            color: "#fff",
            fontSize: 12,
            fontWeight: 700,
            letterSpacing: "0.05em",
          }}
        >
          ACKNOWLEDGE
        </button>
      )}
      {triage === "red" && acknowledged && (
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--muted)" }}>
          Acknowledged
        </span>
      )}
    </div>
  );
}
