import { useState } from "react";
import Header from "../components/Header";
import CriticalBanner from "../components/CriticalBanner";
import RoomCard from "../components/RoomCard";
import AddPatientDialog from "../components/AddPatientDialog";
import { usePatients } from "../hooks/usePatients";
import { useHemoGuard } from "../context/WebSocketContext";

export default function NurseStation() {
  const { triage, metrics, sparklineHistory, isLive, showToast } = useHemoGuard();
  const { patients, addPatient, removePatient, assignSensor } = usePatients();
  const [adding, setAdding] = useState(false);

  const monitored = patients.filter((p) => p.sensor).length;
  const critical = monitored && triage === "red" ? 1 : 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Header title="Nurse Station" subtitle="Ward overview" />
      <CriticalBanner />

      <div style={{ padding: "22px 26px 40px", overflow: "auto" }}>
        {/* Summary strip - reads as a line of facts rather than another row of
            boxes, which is what stops a dashboard looking template-shaped. */}
        <div
          className="rise"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 28,
            paddingBottom: 18,
            marginBottom: 20,
            borderBottom: "1px solid var(--edge)",
            flexWrap: "wrap",
          }}
        >
          <Summary value={patients.length} label="beds on board" />
          <Divider />
          <Summary value={monitored} label="monitored" accent="var(--green)" />
          <Divider />
          <Summary
            value={critical}
            label="critical"
            accent={critical ? "var(--primary)" : undefined}
          />

          <button
            onClick={() => setAdding(true)}
            className="press"
            style={{
              marginLeft: "auto",
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              padding: "10px 18px",
              borderRadius: 999,
              background: "var(--primary)",
              color: "#fff",
              fontSize: 13,
              fontWeight: 650,
              boxShadow: "0 2px 10px -3px rgba(232,54,93,.55)",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "var(--primary-deep)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "var(--primary)";
            }}
          >
            <span style={{ fontSize: 15, lineHeight: 1 }}>+</span>
            Admit patient
          </button>
        </div>

        {patients.length === 0 ? (
          <div
            className="pop"
            style={{
              padding: "72px 20px",
              textAlign: "center",
              border: "1px dashed var(--edge-strong)",
              borderRadius: "var(--r-lg)",
            }}
          >
            <div style={{ fontSize: 15, fontWeight: 650 }}>No patients on the board</div>
            <div style={{ fontSize: 13, color: "var(--muted)", marginTop: 6 }}>
              Admit a patient, then assign the sensor to their bay.
            </div>
          </div>
        ) : (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(268px, 1fr))",
              gap: 18,
            }}
          >
            {patients.map((p, i) => (
              <RoomCard
                key={p.id}
                patient={p}
                index={i}
                triage={p.sensor ? triage : "unknown"}
                metrics={p.sensor ? metrics : {}}
                history={p.sensor ? sparklineHistory : []}
                isLive={isLive}
                onAssign={(id) => {
                  assignSensor(id);
                  const target = patients.find((x) => x.id === id);
                  showToast(
                    target?.sensor
                      ? `Sensor unassigned from ${target.bed}`
                      : `Sensor assigned to ${target?.bed}`,
                    "success",
                  );
                }}
                onRemove={(id) => {
                  const target = patients.find((x) => x.id === id);
                  removePatient(id);
                  showToast(`${target?.bed} discharged`, "info");
                }}
              />
            ))}
          </div>
        )}

        <p style={{ marginTop: 22, fontSize: 11.5, color: "var(--faint)", maxWidth: 620 }}>
          One sensor node is connected, so a single bay can be monitored at a
          time. Unmonitored bays show no vitals rather than placeholder figures.
        </p>
      </div>

      <AddPatientDialog
        open={adding}
        onClose={() => setAdding(false)}
        existingBeds={patients.map((p) => p.bed)}
        onAdd={(data) => {
          const entry = addPatient(data);
          showToast(`${entry.bed} admitted`, "success");
        }}
      />
    </div>
  );
}

function Summary({ value, label, accent }) {
  return (
    <div>
      <div
        className="numeral triage-tint"
        style={{ fontSize: 30, color: accent || "var(--ink)" }}
      >
        {value}
      </div>
      <div className="label" style={{ marginTop: 4 }}>
        {label}
      </div>
    </div>
  );
}

function Divider() {
  return <span style={{ width: 1, height: 34, background: "var(--edge)" }} />;
}
