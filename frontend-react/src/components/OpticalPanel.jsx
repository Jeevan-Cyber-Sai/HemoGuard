import LEDIndicator from "./LEDIndicator";
import { useHemoGuard } from "../context/WebSocketContext";

const LED_TINT = { RED: "#e8365d", GREEN: "#12b76a", IR: "#7a5af8" };

/**
 * Everything the ward display shows for the colorimeter, in the light theme.
 *
 * Beer-Lambert figures read "—" until a water baseline exists: without I0 there
 * is nothing to measure attenuation against, and a 0.00 would claim the sample
 * matched the reference rather than admitting none was taken.
 */
export default function OpticalPanel() {
  const { metrics, isLive, isCalibrated } = useHemoGuard();

  // Floored at zero here and only here. The node sends the true signed value
  // and the CSV keeps it, because a sample transmitting more light than water
  // is evidence of a stale baseline - not a number to quietly discard.
  const bl = (v) =>
    !isCalibrated || v === null || v === undefined
      ? "—"
      : Math.max(0, v).toFixed(2);

  const count = (v) => (v === null || v === undefined ? "—" : Math.round(v));

  return (
    <div className="card" style={{ padding: 18 }}>
      <div style={{ display: "flex", alignItems: "center" }}>
        <span className="label">Optical sensor</span>
        <span
          className="triage-tint"
          style={{
            marginLeft: "auto",
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.06em",
            color: LED_TINT[String(metrics.led).toUpperCase()] || "var(--faint)",
          }}
        >
          LED: {metrics.led || "—"}
        </span>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          gap: 20,
          marginTop: 14,
        }}
      >
        {/* ---- raw channels + colour ---- */}
        <div
          style={{
            paddingRight: 20,
            borderRight: "1px solid var(--edge)",
            display: "flex",
            flexDirection: "column",
            gap: 14,
            minWidth: 148,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span
              style={{
                width: 34,
                height: 34,
                borderRadius: 8,
                border: "1px solid var(--edge)",
                background: metrics.hex || "var(--bg)",
                transition: "background 300ms ease",
                flex: "none",
              }}
            />
            <span
              style={{ fontSize: 12, color: "var(--muted)", fontVariantNumeric: "tabular-nums" }}
            >
              {metrics.hex || "—"}
            </span>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 14px" }}>
            {[
              ["R", metrics.red],
              ["G", metrics.green],
              ["B", metrics.blue],
              ["C", metrics.clear],
            ].map(([k, v]) => (
              <div key={k} style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
                <span style={{ fontSize: 10, color: "var(--faint)", width: 8 }}>{k}</span>
                <span
                  style={{
                    fontSize: 16,
                    fontWeight: 700,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {count(v)}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* ---- Beer-Lambert ---- */}
        <div>
          <div style={{ display: "flex", alignItems: "center" }}>
            <span className="label">Beer-Lambert</span>
            <span
              className="triage-tint"
              style={{
                marginLeft: "auto",
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: "0.06em",
                padding: "3px 9px",
                borderRadius: 999,
                border: `1px solid ${isCalibrated ? "#12b76a44" : "var(--edge)"}`,
                color: isCalibrated ? "var(--green)" : "var(--faint)",
                background: isCalibrated ? "#12b76a10" : "transparent",
              }}
            >
              {isCalibrated ? "CAL ✓" : "CAL ✗"}
            </span>
          </div>

          <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 5 }}>
            <Row label="Absorbance" value={bl(metrics.absorbance)} />
            <Row
              label="Concentration"
              value={
                metrics.hbConcentration !== null
                  ? metrics.hbConcentration.toFixed(1)
                  : bl(metrics.concentration)
              }
              unit={metrics.hbConcentration !== null ? "g/dL" : "rel. units"}
            />
            {metrics.bloodMl !== null && (
              <Row label="Blood in sample" value={metrics.bloodMl.toFixed(2)} unit="mL" />
            )}
            {metrics.hbMassMg !== null && (
              <Row label="Hb mass" value={metrics.hbMassMg.toFixed(0)} unit="mg" />
            )}
          </div>

          <div
            style={{
              marginTop: 12,
              paddingTop: 10,
              borderTop: "1px solid var(--edge)",
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            {metrics.phases.map((ph) => (
              <div
                key={ph.name}
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  fontSize: 12,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                <span style={{ color: LED_TINT[ph.name], fontWeight: 700, width: 54 }}>
                  {ph.name}
                </span>
                {isCalibrated && ph.cal === false ? (
                  <span style={{ color: "var(--faint)" }}>
                    no baseline{ph.name === "IR" ? " (not required)" : ""}
                  </span>
                ) : (
                  <span style={{ color: "var(--muted)" }}>
                    A=<b style={{ color: "var(--ink)" }}>{bl(ph.abs)}</b>
                    <span style={{ marginLeft: 14 }}>
                      C=<b style={{ color: "var(--ink)" }}>{bl(ph.conc)}</b>
                    </span>
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--edge)" }}>
        <LEDIndicator active={metrics.led} live={isLive} />
      </div>
    </div>
  );
}

function Row({ label, value, unit }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", fontSize: 13 }}>
      <span style={{ color: "var(--muted)" }}>{label}</span>
      <span style={{ marginLeft: "auto", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
        {value}
        {unit && (
          <span style={{ fontSize: 11, fontWeight: 500, color: "var(--muted)", marginLeft: 5 }}>
            {unit}
          </span>
        )}
      </span>
    </div>
  );
}
