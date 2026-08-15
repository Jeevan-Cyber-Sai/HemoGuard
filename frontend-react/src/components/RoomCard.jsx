import { useState } from "react";
import { useNavigate } from "react-router-dom";
import SparklineChart from "./SparklineChart";
import TriageBadge from "./TriageBadge";
import { TRIAGE_COLOUR } from "../context/WebSocketContext";

/**
 * One bay on the ward board.
 *
 * A bay without the sensor shows no vitals at all - not zeroes, not placeholder
 * numbers. There is one node, so at most one bay can be live, and filling the
 * rest with plausible figures would put patients on the board who are not being
 * measured.
 */
export default function RoomCard({
  patient,
  triage = "unknown",
  metrics = {},
  history = [],
  isLive = true,
  onAssign,
  onRemove,
  index = 0,
}) {
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const monitored = patient.sensor;
  const tint = TRIAGE_COLOUR[triage] || TRIAGE_COLOUR.unknown;

  const fmt = (v, dp, unit) =>
    v === null || v === undefined ? "—" : `${Number(v).toFixed(dp)}${unit || ""}`;

  const remove = () => {
    setLeaving(true);
    // Let the collapse play out before the row disappears from the roster.
    setTimeout(() => onRemove(patient.id), 240);
  };

  return (
    <article
      className={`card rise ${leaving ? "collapsing" : "lift"}`}
      style={{
        "--i": index,
        position: "relative",
        padding: 0,
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        minHeight: 208,
        cursor: monitored ? "pointer" : "default",
        borderColor: monitored ? "var(--edge)" : "var(--edge)",
        background: monitored
          ? "var(--card)"
          : "linear-gradient(180deg, #fdfdfc 0%, #f9f8f6 100%)",
      }}
      onClick={() => monitored && navigate("/patient")}
    >
      {/* Accent rail - only a monitored bay earns colour. */}
      <span
        className="triage-tint"
        style={{
          position: "absolute",
          insetInline: 0,
          top: 0,
          height: 3,
          background: monitored ? tint : "transparent",
        }}
      />

      <header style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "18px 18px 0" }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <span style={{ fontSize: 15.5, fontWeight: 700, letterSpacing: "-0.015em" }}>
              {patient.bed}
            </span>
            {monitored && <TriageBadge triage={triage} size="sm" />}
          </div>
          <div
            style={{
              fontSize: 12.5,
              color: "var(--muted)",
              marginTop: 3,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {patient.name}
            {patient.note ? ` · ${patient.note}` : ""}
          </div>
        </div>

        <button
          onClick={(e) => {
            e.stopPropagation();
            setConfirming(true);
          }}
          aria-label={`Discharge ${patient.name}`}
          className="press"
          style={{
            marginLeft: "auto",
            width: 26,
            height: 26,
            borderRadius: 7,
            color: "var(--faint)",
            fontSize: 15,
            lineHeight: 1,
            flex: "none",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "var(--primary-wash)";
            e.currentTarget.style.color = "var(--primary)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "transparent";
            e.currentTarget.style.color = "var(--faint)";
          }}
        >
          ×
        </button>
      </header>

      {monitored ? (
        <>
          <div style={{ display: "flex", gap: 20, padding: "16px 18px 10px" }}>
            <Stat label="Risk" value={fmt(metrics.riskScore, 0)} tint={tint} big />
            <Stat label="Pulse" value={fmt(metrics.pulse, 0)} unit="bpm" />
            <Stat label="SpO₂" value={fmt(metrics.spo2, 0)} unit="%" />
          </div>
          <div style={{ marginTop: "auto", padding: "0 6px 6px" }}>
            <SparklineChart
              data={history}
              dataKey="risk"
              triage={triage}
              height={44}
              domain={[0, 100]}
            />
          </div>
        </>
      ) : (
        <div
          style={{
            margin: "auto",
            padding: "24px 18px",
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 11, letterSpacing: "0.1em", color: "var(--faint)" }}>
            NO SENSOR ASSIGNED
          </div>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onAssign(patient.id);
            }}
            className="press"
            style={{
              marginTop: 12,
              padding: "8px 16px",
              borderRadius: 999,
              border: "1px dashed var(--edge-strong)",
              fontSize: 12,
              fontWeight: 600,
              color: "var(--muted)",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = "var(--primary)";
              e.currentTarget.style.color = "var(--primary)";
              e.currentTarget.style.background = "var(--primary-wash)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = "var(--edge-strong)";
              e.currentTarget.style.color = "var(--muted)";
              e.currentTarget.style.background = "transparent";
            }}
          >
            Assign sensor
          </button>
        </div>
      )}

      {monitored && (
        <footer
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 18px 12px",
            borderTop: "1px solid var(--edge)",
          }}
        >
          <span
            style={{
              fontSize: 10,
              letterSpacing: "0.08em",
              color: isLive ? "var(--green)" : "var(--faint)",
              fontWeight: 650,
            }}
          >
            {isLive ? "● SENSOR LIVE" : "○ FEED NOT LIVE"}
          </span>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onAssign(patient.id);
            }}
            className="press"
            style={{ marginLeft: "auto", fontSize: 11, color: "var(--faint)", fontWeight: 600 }}
          >
            Unassign
          </button>
        </footer>
      )}

      {/* Discharge confirmation, in-card rather than a window-level alert -
          it keeps the question next to the thing it is about. */}
      {confirming && (
        <div
          className="scrim-in"
          onClick={(e) => e.stopPropagation()}
          style={{
            position: "absolute",
            inset: 0,
            background: "rgba(255,255,255,.93)",
            backdropFilter: "blur(2px)",
            display: "grid",
            placeItems: "center",
            padding: 18,
            textAlign: "center",
          }}
        >
          <div>
            <div style={{ fontSize: 13.5, fontWeight: 650 }}>
              Discharge {patient.name}?
            </div>
            <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 4 }}>
              {patient.bed} is removed from the board.
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "center", marginTop: 14 }}>
              <button
                onClick={() => setConfirming(false)}
                className="press"
                style={{
                  padding: "7px 14px",
                  borderRadius: "var(--r-sm)",
                  border: "1px solid var(--edge-strong)",
                  fontSize: 12.5,
                  fontWeight: 600,
                  color: "var(--muted)",
                }}
              >
                Keep
              </button>
              <button
                onClick={remove}
                className="press"
                style={{
                  padding: "7px 16px",
                  borderRadius: "var(--r-sm)",
                  background: "var(--primary)",
                  color: "#fff",
                  fontSize: 12.5,
                  fontWeight: 650,
                }}
              >
                Discharge
              </button>
            </div>
          </div>
        </div>
      )}
    </article>
  );
}

function Stat({ label, value, unit, tint, big }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div className="label" style={{ fontSize: 9.5 }}>
        {label}
      </div>
      <div
        className="numeral triage-tint"
        style={{
          fontSize: big ? 26 : 20,
          marginTop: 4,
          color: tint || "var(--ink)",
          display: "flex",
          alignItems: "baseline",
          gap: 3,
        }}
      >
        {value}
        {unit && (
          <span style={{ fontSize: 10.5, fontWeight: 500, color: "var(--faint)", letterSpacing: 0 }}>
            {unit}
          </span>
        )}
      </div>
    </div>
  );
}
