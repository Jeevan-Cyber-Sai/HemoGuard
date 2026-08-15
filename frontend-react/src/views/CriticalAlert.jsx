import { useNavigate } from "react-router-dom";
import { useHemoGuard, TRIAGE_COLOUR } from "../context/WebSocketContext";
import ZRiskGauge from "../components/ZRiskGauge";

export default function CriticalAlert() {
  const { triage, metrics, acknowledged, acknowledge, alarming, isLive } =
    useHemoGuard();
  const navigate = useNavigate();
  const critical = triage === "red";
  const tint = TRIAGE_COLOUR[triage] || TRIAGE_COLOUR.unknown;

  const fmt = (v, dp, unit) =>
    v === null || v === undefined ? "—" : `${Number(v).toFixed(dp)}${unit || ""}`;

  return (
    <div
      className="view-enter"
      style={{
        height: "100%",
        display: "grid",
        placeItems: "center",
        padding: 32,
        background: critical ? "#fff5f7" : "var(--bg)",
        transition: "background 500ms ease",
      }}
    >
      <div style={{ width: "100%", maxWidth: 620, textAlign: "center" }}>
        <div
          className={alarming ? "breathe" : ""}
          style={{
            width: 72,
            height: 72,
            borderRadius: "50%",
            margin: "0 auto 20px",
            display: "grid",
            placeItems: "center",
            fontSize: 30,
            background: critical ? "var(--primary)" : "#eef0f3",
            color: critical ? "#fff" : "var(--faint)",
            transition: "background 500ms ease",
          }}
        >
          {critical ? "⚠" : "✓"}
        </div>

        <h1
          className="triage-tint"
          style={{
            fontSize: 30,
            fontWeight: 700,
            letterSpacing: "-0.02em",
            color: critical ? "var(--primary)" : "var(--ink)",
          }}
        >
          {critical ? "CRITICAL ALERT" : "No active alert"}
        </h1>

        <p style={{ color: "var(--muted)", fontSize: 14, marginTop: 8 }}>
          {critical
            ? "Bed 4 has crossed the critical threshold and requires immediate attention."
            : isLive
              ? "Bed 4 is being monitored and is below the critical threshold."
              : "No live feed. Nothing is currently being monitored."}
        </p>

        <div className="card" style={{ padding: 22, marginTop: 24 }}>
          <ZRiskGauge score={metrics.riskScore} triage={triage} />

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(3, 1fr)",
              gap: 12,
              marginTop: 14,
              borderTop: "1px solid var(--edge)",
              paddingTop: 16,
            }}
          >
            <Cell label="Blood loss" value={fmt(metrics.bloodLoss, 1, " mL")} />
            <Cell label="Rate" value={fmt(metrics.bloodLossRate, 2, " mL/min")} />
            <Cell label="Pulse" value={fmt(metrics.pulse, 0, " bpm")} tint={tint} />
          </div>
        </div>

        <div
          style={{ display: "flex", gap: 10, justifyContent: "center", marginTop: 22 }}
        >
          {critical && !acknowledged && (
            <button
              onClick={acknowledge}
              style={{
                padding: "12px 30px",
                borderRadius: 10,
                background: "var(--primary)",
                color: "#fff",
                fontSize: 14,
                fontWeight: 700,
                letterSpacing: "0.04em",
              }}
            >
              ACKNOWLEDGE
            </button>
          )}
          {critical && acknowledged && (
            <span
              style={{
                padding: "12px 24px",
                fontSize: 13,
                color: "var(--muted)",
              }}
            >
              Acknowledged — alarm silenced, band stays red until it clears.
            </span>
          )}
          <button
            onClick={() => navigate("/patient")}
            style={{
              padding: "12px 24px",
              borderRadius: 10,
              border: "1px solid var(--edge)",
              background: "#fff",
              fontSize: 14,
              fontWeight: 600,
            }}
          >
            Open patient detail
          </button>
        </div>
      </div>
    </div>
  );
}

function Cell({ label, value, tint }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div
        style={{
          fontSize: 18,
          fontWeight: 700,
          marginTop: 4,
          fontVariantNumeric: "tabular-nums",
          color: tint || "var(--ink)",
        }}
      >
        {value}
      </div>
    </div>
  );
}
