import { useNavigate } from "react-router-dom";
import { useHemoGuard } from "../context/WebSocketContext";
import TriageBadge from "../components/TriageBadge";

const VIEWS = [
  {
    to: "/nurse",
    title: "Nurse Station",
    body: "Every monitored bay at a glance, with risk trend and triage band per bed.",
    icon: "▦",
  },
  {
    to: "/patient",
    title: "Patient Detail",
    body: "Live vitals, Beer-Lambert haemoglobin, trend charts and the abnormal-event log.",
    icon: "◉",
  },
  {
    to: "/critical",
    title: "Critical Alert",
    body: "Full-screen escalation view with acknowledgement.",
    icon: "⚠",
  },
];

export default function ViewSelection() {
  const navigate = useNavigate();
  const { triage, isLive, degradedLabel } = useHemoGuard();

  return (
    <div style={{ padding: 40, maxWidth: 940, margin: "0 auto" }}>
      <div style={{ marginBottom: 6, display: "flex", alignItems: "center", gap: 12 }}>
        <h1 className="rise" style={{ fontSize: 30, fontWeight: 700, letterSpacing: "-0.035em" }}>
          HemoGuard
        </h1>
        <TriageBadge triage={triage} />
      </div>
      <p style={{ color: "var(--muted)", fontSize: 14, marginBottom: 28 }}>
        Post-operative bleeding monitor · Bed 4 ·{" "}
        <span style={{ color: isLive ? "var(--green)" : "var(--faint)" }}>
          {isLive ? "feed live" : degradedLabel || "no feed"}
        </span>
      </p>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
          gap: 16,
        }}
      >
        {VIEWS.map((v, i) => (
          <button
            key={v.to}
            onClick={() => navigate(v.to)}
            className="card lift rise press"
            style={{
              "--i": i,
              padding: 24,
              textAlign: "left",
              display: "flex",
              flexDirection: "column",
              gap: 11,
            }}
          >
            <span
              style={{
                width: 40,
                height: 40,
                borderRadius: 12,
                background: "var(--primary-wash)",
                color: "var(--primary)",
                display: "grid",
                placeItems: "center",
                fontSize: 17,
              }}
            >
              {v.icon}
            </span>
            <span style={{ fontSize: 16.5, fontWeight: 700, letterSpacing: "-0.02em" }}>{v.title}</span>
            <span style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.5 }}>
              {v.body}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
