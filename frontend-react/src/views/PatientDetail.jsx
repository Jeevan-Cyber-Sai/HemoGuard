import { useState } from "react";
import Header from "../components/Header";
import CriticalBanner from "../components/CriticalBanner";
import MetricCard from "../components/MetricCard";
import SparklineChart from "../components/SparklineChart";
import TrendChart from "../components/TrendChart";
import TriageBadge from "../components/TriageBadge";
import AbnormalLog from "../components/AbnormalLog";
import ZRiskGauge from "../components/ZRiskGauge";
import OpticalPanel from "../components/OpticalPanel";
import WeightPanel from "../components/WeightPanel";
import TriageBar from "../components/TriageBar";
import { useHemoGuard, TRIAGE_COLOUR } from "../context/WebSocketContext";

const TABS = ["Live", "Trends", "Abnormal Events"];

export default function PatientDetail() {
  const [tab, setTab] = useState("Live");
  const {
    metrics,
    triage,
    isLive,
    isCalibrated,
    sparklineHistory,
    abnormalEvents,
  } = useHemoGuard();

  const tint = TRIAGE_COLOUR[triage] || TRIAGE_COLOUR.unknown;

  // Beat period from the measured pulse - four beats per animation cycle would
  // be wrong here; one beat per cycle is what reads as a heartbeat.
  const beat =
    metrics.pulse && metrics.pulse > 0 ? `${(60 / metrics.pulse).toFixed(2)}s` : "1.2s";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Header title="Bed 4" subtitle="Patient detail" />
      <CriticalBanner />

      <div style={{ padding: 24, overflow: "auto" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
          <TriageBadge triage={triage} />
          <span
            style={{
              fontSize: 11,
              letterSpacing: "0.06em",
              color: isCalibrated ? "var(--green)" : "var(--faint)",
              border: `1px solid ${isCalibrated ? "#12b76a44" : "var(--edge)"}`,
              borderRadius: 999,
              padding: "4px 10px",
              fontWeight: 700,
            }}
          >
            {isCalibrated ? "CALIBRATED" : "NOT CALIBRATED"}
          </span>

          <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                style={{
                  padding: "7px 14px",
                  borderRadius: 8,
                  fontSize: 13,
                  fontWeight: tab === t ? 600 : 500,
                  color: tab === t ? "var(--primary)" : "var(--muted)",
                  background: tab === t ? "#fdf2f5" : "transparent",
                  transition: "background 160ms ease, color 160ms ease",
                }}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        {tab === "Live" && (
          <div style={{ display: "grid", gap: 16 }}>
            {/* Rows 1 and 2 mirror the ward display: three primary vitals, then
                the two derived measures beside the risk gauge. */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
                gap: 16,
              }}
            >
              <MetricCard
                label="Blood loss"
                value={metrics.bloodLoss}
                unit="mL"
                decimals={1}
                countUp
                note={
                  metrics.bloodLoss === null
                    ? metrics.scaleReady
                      ? "no pads weighed yet"
                      : "load cell not fitted"
                    : `${metrics.padCount} pad${metrics.padCount === 1 ? "" : "s"} weighed`
                }
              />
              <MetricCard
                label="SpO₂"
                value={metrics.spo2}
                unit="%"
                decimals={0}
                tag={metrics.vitalsSimulated ? "SIMULATED" : null}
                note={
                  metrics.spo2 === null
                    ? metrics.finger === false
                      ? "no finger on sensor"
                      : "oximeter not fitted"
                    : ""
                }
              />
              <MetricCard
                label="Pulse"
                value={metrics.pulse}
                unit="bpm"
                decimals={0}
                tag={metrics.vitalsSimulated ? "SIMULATED" : null}
                note={
                  metrics.pulse === null
                    ? metrics.finger === false
                      ? "no finger on sensor"
                      : "oximeter not fitted"
                    : ""
                }
                icon={
                  <span
                    className={metrics.pulse ? "heart-beat" : ""}
                    style={{ "--beat": beat, color: "var(--primary)", fontSize: 13 }}
                  >
                    ♥
                  </span>
                }
              />
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
                gap: 16,
                alignItems: "start",
              }}
            >
              <MetricCard
                label="Bleeding rate"
                value={metrics.bloodLossRate}
                unit="mL/min"
                decimals={2}
                accent={tint}
                note={
                  metrics.bloodLossRate === null
                    ? metrics.padCount
                      ? "needs a second pad to time against"
                      : "load cell not fitted"
                    : "average since previous pad"
                }
              />
              <MetricCard
                label={metrics.hbConcentration !== null ? "Haemoglobin" : "Hb index"}
                value={
                  metrics.hbConcentration !== null
                    ? metrics.hbConcentration
                    : metrics.hbIndex
                }
                unit={metrics.hbConcentration !== null ? "g/dL" : "rel."}
                decimals={metrics.hbConcentration !== null ? 1 : 3}
                countUp
                note={
                  metrics.hbIndex === null
                    ? metrics.hbMode === "chromaticity"
                      ? "not calibrated"
                      : "no optical data"
                    : metrics.hbConcentration === null
                      ? "set a reference for g/dL"
                      : ""
                }
              />

              <div className="card" style={{ padding: 18 }}>
                <div className="label">Risk score</div>
                <div style={{ marginTop: 6 }}>
                  <ZRiskGauge score={metrics.riskScore} triage={triage} />
                </div>
              </div>
            </div>

            <TriageBar />

            {/* Row 3 - optical detail beside the two trends. */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(380px, 1.15fr) 1fr",
                gap: 16,
              }}
            >
              <OpticalPanel />

              <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
                <WeightPanel />
                <TrendTile
                  title="Weight trend"
                  data={sparklineHistory}
                  dataKey="bloodLoss"
                  triage={triage}
                  empty={metrics.bloodLoss === null}
                  emptyNote="load cell not fitted"
                />
                <TrendTile
                  title="Risk trend"
                  data={sparklineHistory}
                  dataKey="risk"
                  triage={triage}
                  domain={[0, 100]}
                  range="0 – 100"
                />
              </div>
            </div>
          </div>
        )}

        {tab === "Trends" && (
          <div className="card" style={{ padding: 18 }}>
            <div className="label" style={{ marginBottom: 10 }}>
              Last {sparklineHistory.length} readings
            </div>
            <TrendChart data={sparklineHistory} />
          </div>
        )}

        {tab === "Abnormal Events" && (
          <div className="card" style={{ padding: 18 }}>
            <div className="label" style={{ marginBottom: 4 }}>
              Abnormal events — last {abnormalEvents.length}
            </div>
            <AbnormalLog events={abnormalEvents} />
          </div>
        )}
      </div>
    </div>
  );
}

/** A titled sparkline. An unfitted channel says so instead of drawing a flat
    line, which would imply a measurement of nothing changing. */
function TrendTile({ title, data, dataKey, triage, domain, range, empty, emptyNote }) {
  return (
    <div className="card" style={{ padding: 16 }}>
      <div style={{ display: "flex", alignItems: "baseline" }}>
        <span className="label">{title} — 60 pts</span>
        <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--faint)" }}>
          {empty ? emptyNote : range || "auto"}
        </span>
      </div>
      {empty ? (
        <div style={{ height: 56, display: "grid", placeItems: "center",
                      fontSize: 11, color: "var(--faint)" }}>
          not available
        </div>
      ) : (
        <SparklineChart data={data} dataKey={dataKey} triage={triage}
                        height={56} domain={domain} />
      )}
    </div>
  );
}
