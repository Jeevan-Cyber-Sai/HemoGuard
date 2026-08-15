import { useEffect, useState } from "react";
import { useHemoGuard, API_URL } from "../context/WebSocketContext";

/**
 * Pad weighing.
 *
 * Each press weighs whatever is on the tray, subtracts the dry-pad offset and
 * adds the remaining blood to a running total. The dashboard therefore reports
 * cumulative blood recovered rather than whatever happens to be on the tray at
 * this instant.
 */
export default function WeightPanel() {
  const { metrics, showToast } = useHemoGuard();
  const [weighing, setWeighing] = useState(false);
  const [dryPad, setDryPad] = useState("");
  const [savingPad, setSavingPad] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);

  // Follow the node's value until the field is touched, so it always opens
  // showing what the hardware is actually subtracting.
  useEffect(() => {
    if (dryPad === "" && metrics.dryPadG !== null && metrics.dryPadG !== undefined) {
      setDryPad(String(metrics.dryPadG));
    }
  }, [metrics.dryPadG, dryPad]);

  const ready = metrics.scaleReady;

  const weigh = async () => {
    if (weighing || !ready) return;
    setWeighing(true);
    try {
      const res = await fetch(`${API_URL}/weigh`, { method: "POST" });
      const data = await res.json();
      if (data.status === "error") showToast(data.message, "error");
    } catch {
      showToast("Weigh failed — backend unreachable", "error");
    } finally {
      setWeighing(false);
    }
  };

  const saveDryPad = async () => {
    const grams = Number(dryPad);
    if (!Number.isFinite(grams) || grams < 0 || grams > 500) {
      showToast("Dry pad weight must be between 0 and 500 g", "error");
      return;
    }
    setSavingPad(true);
    try {
      const res = await fetch(`${API_URL}/dry_pad`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ g: grams }),
      });
      const data = await res.json();
      if (data.status === "error") showToast(data.message, "error");
    } catch {
      showToast("Could not set dry pad weight", "error");
    } finally {
      setSavingPad(false);
    }
  };

  const calibrateScale = async () => {
    try {
      const res = await fetch(`${API_URL}/weight_calibrate`, { method: "POST" });
      const data = await res.json();
      if (data.status === "error") showToast(data.message, "error");
    } catch {
      showToast("Could not start scale calibration", "error");
    }
  };

  const reset = async () => {
    setConfirmReset(false);
    try {
      const res = await fetch(`${API_URL}/weight_reset`, { method: "POST" });
      const data = await res.json();
      if (data.status === "error") showToast(data.message, "error");
    } catch {
      showToast("Reset failed", "error");
    }
  };

  return (
    <div className="card" style={{ padding: 18 }}>
      <div style={{ display: "flex", alignItems: "center" }}>
        <span className="label">Pad weighing</span>
        <span
          className="triage-tint"
          style={{
            marginLeft: "auto",
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: "0.07em",
            color: ready ? "var(--green)" : "var(--faint)",
          }}
        >
          {ready ? "LOAD CELL READY" : "NO LOAD CELL"}
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 12 }}>
        <span className="numeral" style={{ fontSize: 36 }}>
          {metrics.bloodLoss === null ? "—" : metrics.bloodLoss.toFixed(1)}
        </span>
        <span style={{ fontSize: 13, color: "var(--muted)", fontWeight: 500 }}>
          mL total
        </span>
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--faint)" }}>
          {metrics.padCount ? `${metrics.padCount} pad${metrics.padCount === 1 ? "" : "s"}` : "no pads yet"}
          {metrics.lastPadG ? ` · last ${metrics.lastPadG.toFixed(1)} g` : ""}
        </span>
      </div>

      <button
        onClick={weigh}
        disabled={weighing || !ready}
        className={`press ${weighing ? "cal-pulse" : ""}`}
        style={{
          width: "100%",
          marginTop: 14,
          padding: "13px 18px",
          borderRadius: "var(--r-sm)",
          background: ready ? "var(--primary)" : "var(--bg-deep)",
          color: ready ? "#fff" : "var(--faint)",
          fontSize: 13.5,
          fontWeight: 650,
          letterSpacing: "0.01em",
          cursor: ready && !weighing ? "pointer" : "not-allowed",
          boxShadow: ready ? "0 2px 10px -3px rgba(232,54,93,.5)" : "none",
        }}
      >
        {weighing ? "Weighing pad…" : "Take blood weight reading from pad"}
      </button>

      <p style={{ fontSize: 11.5, color: "var(--faint)", marginTop: 8, lineHeight: 1.5 }}>
        Place the soaked pad on the tray, then press. The dry-pad weight is
        subtracted and the blood added to the total.
      </p>

      {/* The scale is no longer calibrated at boot - that blocked the LED cycle
          and the colour feed when the HX711 hesitated. Started from here it can
          only ever hold up itself. */}
      <button
        onClick={calibrateScale}
        className="press"
        style={{
          width: "100%",
          marginTop: 8,
          padding: "9px 14px",
          borderRadius: "var(--r-sm)",
          border: "1px solid var(--blue)",
          color: "var(--blue)",
          fontSize: 12.5,
          fontWeight: 600,
        }}
      >
        Calibrate scale (~16 s, follow serial prompts)
      </button>

      <div className="rule" style={{ margin: "14px 0" }} />

      <label className="label" htmlFor="drypad">
        Dry pad weight
      </label>
      <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
        <div style={{ position: "relative", flex: 1 }}>
          <input
            id="drypad"
            type="number"
            step="0.1"
            min="0"
            max="500"
            value={dryPad}
            onChange={(e) => setDryPad(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && saveDryPad()}
            style={{
              width: "100%",
              padding: "9px 34px 9px 12px",
              fontSize: 14,
              color: "var(--ink)",
              background: "var(--bg)",
              border: "1px solid var(--edge-strong)",
              borderRadius: "var(--r-sm)",
              outline: "none",
              transition: "border-color 160ms ease, box-shadow 160ms ease",
            }}
            onFocus={(e) => {
              e.target.style.borderColor = "var(--primary)";
              e.target.style.boxShadow = "0 0 0 3px var(--primary-wash)";
            }}
            onBlur={(e) => {
              e.target.style.borderColor = "var(--edge-strong)";
              e.target.style.boxShadow = "none";
            }}
          />
          <span
            style={{
              position: "absolute",
              right: 11,
              top: "50%",
              transform: "translateY(-50%)",
              fontSize: 12,
              color: "var(--faint)",
              pointerEvents: "none",
            }}
          >
            g
          </span>
        </div>
        <button
          onClick={saveDryPad}
          disabled={savingPad}
          className="press"
          style={{
            padding: "9px 16px",
            borderRadius: "var(--r-sm)",
            border: "1px solid var(--edge-strong)",
            fontSize: 13,
            fontWeight: 600,
            color: "var(--muted)",
            whiteSpace: "nowrap",
          }}
        >
          {savingPad ? "Saving…" : "Set"}
        </button>
      </div>
      <p style={{ fontSize: 11, color: "var(--faint)", marginTop: 6, lineHeight: 1.5 }}>
        Applies to pads weighed from now on. Totals already recorded are not
        adjusted — those pads were measured against the offset in force at the
        time.
      </p>

      <div className="rule" style={{ margin: "14px 0" }} />

      {confirmReset ? (
        <div className="pop" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 12.5, color: "var(--muted)" }}>
            Clear the running total?
          </span>
          <button
            onClick={() => setConfirmReset(false)}
            className="press"
            style={{ marginLeft: "auto", fontSize: 12.5, fontWeight: 600, color: "var(--muted)" }}
          >
            Keep
          </button>
          <button
            onClick={reset}
            className="press"
            style={{
              padding: "6px 14px",
              borderRadius: "var(--r-sm)",
              background: "var(--primary)",
              color: "#fff",
              fontSize: 12.5,
              fontWeight: 650,
            }}
          >
            Reset
          </button>
        </div>
      ) : (
        <button
          onClick={() => setConfirmReset(true)}
          className="press"
          style={{ fontSize: 12.5, fontWeight: 600, color: "var(--faint)" }}
        >
          Reset total for a new patient
        </button>
      )}
    </div>
  );
}
