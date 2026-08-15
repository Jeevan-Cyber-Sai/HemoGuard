import { TRIAGE_COLOUR, TRIAGE_LABEL } from "../context/WebSocketContext";

export default function TriageBadge({ triage = "unknown", size = "md" }) {
  const tint = TRIAGE_COLOUR[triage] || TRIAGE_COLOUR.unknown;
  const pad = size === "sm" ? "3px 8px" : "5px 12px";
  const font = size === "sm" ? 10 : 11;

  return (
    <span
      className="triage-tint"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: pad,
        borderRadius: 999,
        background: `${tint}14`,
        color: tint,
        border: `1px solid ${tint}33`,
        fontSize: font,
        fontWeight: 700,
        letterSpacing: "0.08em",
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: tint,
          flex: "none",
        }}
      />
      {TRIAGE_LABEL[triage] || TRIAGE_LABEL.unknown}
    </span>
  );
}
