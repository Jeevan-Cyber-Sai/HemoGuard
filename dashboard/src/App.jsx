import { useCallback, useEffect, useRef, useState } from "react";
import { useFeed } from "./hooks/useFeed";
import { useAlarm } from "./hooks/useAlarm";
import { Card, Readout } from "./components/Card";
import { Ecg } from "./components/Ecg";
import { Gauge } from "./components/Gauge";
import { Sparkline } from "./components/Sparkline";
import { LedCycle } from "./components/LedCycle";
import { Toast } from "./components/Toast";
import {
  API_URL,
  COLOURS,
  TRIAGE_TEXT,
  DEGRADED,
  LED_TINT,
  GAUGE_CAP,
  DASH,
  num,
} from "./lib/constants";

const CAL_SECONDS = 10;

function useClock() {
  const [t, setT] = useState(DASH.repeat(1) === "—" ? "—:—:—" : "");
  useEffect(() => {
    const id = setInterval(
      () => setT(new Date().toLocaleTimeString("en-GB")),
      1000,
    );
    return () => clearInterval(id);
  }, []);
  return t;
}

export default function App() {
  const { payload, weightHistory, zriskHistory, calibration } = useFeed();
  const clock = useClock();

  const [toast, setToast] = useState(null);
  const [calibrating, setCalibrating] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const toastTimer = useRef(null);
  const toastSeq = useRef(0);

  const showToast = useCallback((message, kind) => {
    clearTimeout(toastTimer.current);
    toastSeq.current += 1;
    setToast({ message, kind, seq: toastSeq.current });
    toastTimer.current = setTimeout(() => setToast(null), 4000);
  }, []);

  useEffect(() => () => clearTimeout(toastTimer.current), []);

  // The backend narrates calibration over the WebSocket rather than only to
  // the caller, so a second screen watching this bed sees it happen too. That
  // makes the broadcast - not the POST - the source of truth here.
  useEffect(() => {
    if (!calibration) return;
    if (calibration.status === "started") {
      setCalibrating(true);
      setCountdown(CAL_SECONDS);
      showToast("Place water sample on sensor — calibrating baseline", "info");
    } else if (calibration.status === "complete") {
      setCalibrating(false);
      setCountdown(0);
      showToast("Calibration complete ✓", "success");
    } else {
      setCalibrating(false);
      setCountdown(0);
      showToast(calibration.message || "Calibration failed — retry", "error");
    }
  }, [calibration, showToast]);

  useEffect(() => {
    if (!calibrating) return;
    const id = setInterval(
      () => setCountdown((c) => Math.max(0, c - 1)),
      1000,
    );
    return () => clearInterval(id);
  }, [calibrating]);

  const startCalibration = useCallback(async () => {
    if (calibrating) return;
    // Optimistic, so the button acknowledges the click immediately; the
    // broadcast that follows confirms it and drives everything after.
    setCalibrating(true);
    setCountdown(CAL_SECONDS);
    try {
      const res = await fetch(`${API_URL}/calibrate`, { method: "POST" });
      const data = await res.json();
      if (data && data.status === "error") {
        setCalibrating(false);
        setCountdown(0);
        showToast(data.message || "Calibration failed — retry", "error");
      }
    } catch {
      setCalibrating(false);
      setCountdown(0);
      showToast("Calibration failed — retry", "error");
    }
  }, [calibrating, showToast]);

  const p = payload || {};
  const triage = p.triage || null;
  const { critical, alarming, acknowledged, audioBlocked, acknowledge } =
    useAlarm(triage);

  // Anything the backend does not call "live" is held or absent data, however
  // it got that way. Rendering it under a LIVE badge is the one failure this
  // dashboard must never have.
  const degradedLabel = payload ? DEGRADED[p.status] || null : DASH;
  const degraded = degradedLabel !== null;
  const accent = COLOURS[triage] || COLOURS.unknown;

  // scored_channels lists what the node actually reported this frame, so a
  // sensor that is not fitted reads as unpopulated rather than as a healthy
  // zero. "rate" covers the load cell, which feeds both weight and rate.
  const ch = Array.isArray(p.scored_channels) ? p.scored_channels : null;
  const has = (name) => (ch ? ch.includes(name) : false);
  const absent = (name) => (ch ? !has(name) : false);

  const hex =
    p.hex ||
    (typeof p.norm_r === "number"
      ? "#" +
        [p.norm_r, p.norm_g, p.norm_b]
          .map((v) => Math.max(0, Math.min(255, v | 0)).toString(16).padStart(2, "0"))
          .join("")
          .toUpperCase()
      : null);

  const wLo = weightHistory.length ? Math.min(...weightHistory) : null;
  const wHi = weightHistory.length ? Math.max(...weightHistory) : null;

  // Beer-Lambert readout. Everything reads "—" until a water baseline exists,
  // because without I0 there is nothing to measure attenuation against.
  //
  // A negative absorbance is floored at 0.00 here and ONLY here: the node sends
  // the true signed value and the CSV records it, since a sample transmitting
  // more light than the water reference is evidence of a stale baseline, not a
  // number to quietly discard.
  const bl = (v) => {
    if (!p.calibrated) return DASH;
    if (v === null || v === undefined || Number.isNaN(Number(v))) return DASH;
    return Math.max(0, Number(v)).toFixed(2);
  };

  return (
    <div
      className={
        "grid h-full gap-[0.9vh] p-[0.9vh] " +
        (degraded ? "hg-degraded " : "")
      }
      style={{
        // Bottom row grown from 1.28fr to carry the expanded optical tile plus
        // the LED strip. At 1.62fr the two lower phase rows fell past the
        // card's overflow:hidden and vanished silently, which is the worst way
        // for a readout to fail - present, plausible, and short three lines.
        gridTemplateRows:
          "auto minmax(0,1fr) minmax(0,1fr) auto minmax(0,1.6fr)",
      }}
    >
      {/* ---------------- header ---------------- */}
      <header className="grid grid-cols-[1fr_auto_1fr] items-center rounded-md border border-edge bg-card px-4 py-[0.9vh]">
        <div
          className="flex items-center gap-3 font-bold tracking-[0.32em]"
          style={{ fontSize: "clamp(0.72rem, 2.1vh, 1.2rem)" }}
        >
          <span className="relative flex h-2.5 w-2.5 shrink-0">
            <span
              className="hg-ping absolute inline-flex h-full w-full rounded-full"
              style={{ backgroundColor: accent }}
            />
            <span
              className="relative inline-flex h-2.5 w-2.5 rounded-full transition-colors duration-500"
              style={{ backgroundColor: accent }}
            />
          </span>
          <span>
            HEMO
            <span style={{ color: accent }} className="transition-colors duration-500">
              GUARD
            </span>
          </span>
        </div>

        <div className="flex items-center gap-5 font-mono" style={{ fontSize: "clamp(0.66rem, 1.9vh, 1rem)" }}>
          <span className="font-bold tracking-[0.14em] text-muted">BED 4</span>
          <span className="tabular-nums">{clock}</span>
        </div>

        <div className="flex items-center gap-2.5 justify-self-end">
          <button
            onClick={startCalibration}
            disabled={calibrating}
            className={
              "rounded-full border px-3.5 py-1.5 font-mono font-bold tracking-[0.16em] transition-colors " +
              (calibrating ? "hg-calpulse cursor-not-allowed" : "cursor-pointer")
            }
            style={{
              fontSize: "clamp(0.55rem, 1.5vh, 0.78rem)",
              borderColor: "#2979ff",
              color: "#2979ff",
              background: "transparent",
            }}
          >
            {calibrating ? `CALIBRATING... ${countdown}s` : "CALIBRATE"}
          </button>

          <div
            className="rounded border px-3 py-1.5 font-mono font-bold tracking-[0.2em] transition-colors"
            style={{
              fontSize: "clamp(0.58rem, 1.6vh, 0.85rem)",
              borderColor: degraded ? "#4a5568" : COLOURS.green,
              color: degraded ? "#4a5568" : COLOURS.green,
            }}
          >
            {payload ? degradedLabel || "LIVE" : DASH}
          </div>
        </div>
      </header>

      {/* ---------------- primary vitals ---------------- */}
      <div className="grid min-h-0 grid-cols-3 gap-[0.9vh]">
        {/* An unfitted channel renders no numeral at all. A dimmed em-dash at
            hero size is a 6px-thick horizontal bar - it reads as a loading
            state, which is the one thing this must never look like. The
            NOT FITTED tag already carries the meaning. */}
        <Card label="Weight" accent={accent} absent={absent("rate")} flashKey={p.weight}>
          {!absent("rate") && <Readout value={num(p.weight, 1)} unit="g" />}
        </Card>
        <Card label="SpO₂" accent={accent} absent={absent("spo2")} flashKey={p.spo2}>
          {!absent("spo2") && <Readout value={num(p.spo2, 0)} unit="%" />}
        </Card>
        <Card label="Pulse Rate" accent={accent} absent={absent("pr")} flashKey={p.pulse}>
          {!absent("pr") && (
            <>
              <Readout value={num(p.pulse, 0)} unit="bpm" />
              <Ecg bpm={Number(p.pulse)} />
            </>
          )}
        </Card>
      </div>

      {/* ---------------- derived metrics ---------------- */}
      <div className="grid min-h-0 grid-cols-3 gap-[0.9vh]">
        <Card label="Bleeding Rate" accent={accent} absent={absent("rate")} flashKey={p.bleeding_rate}>
          {!absent("rate") && (
            <Readout value={num(p.bleeding_rate, 1)} unit="mL/min" size="var(--v-mid)" />
          )}
        </Card>
        <Card label="Hb Ratio" accent={accent} absent={absent("hb")} flashKey={p.hb_ratio}>
          {!absent("hb") && <Readout value={num(p.hb_ratio, 2)} size="var(--v-mid)" />}
        </Card>
        <Card label="Z-Risk" accent={accent} flashKey={p.z_risk}>
          <Gauge z={p.z_risk} />
        </Card>
      </div>

      {/* ---------------- triage band ---------------- */}
      <div
        className={
          "flex items-center gap-5 overflow-hidden rounded-md border border-edge bg-card px-5 py-[1.1vh] shadow-[0_1px_10px_rgba(0,0,0,0.45)] transition-colors duration-500 " +
          (alarming ? "hg-breathe" : "")
        }
        style={{ borderLeftWidth: 4, borderLeftColor: accent }}
      >
        <span
          className="font-semibold uppercase tracking-[0.16em] text-muted"
          style={{ fontSize: "var(--v-label)" }}
        >
          Triage Band
        </span>
        <span
          className="font-mono font-bold tracking-[0.28em] transition-colors duration-500"
          style={{ fontSize: "var(--v-band)", color: accent }}
        >
          {triage ? TRIAGE_TEXT[triage] || TRIAGE_TEXT.unknown : DASH}
        </span>

        {critical && !acknowledged && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              acknowledge();
            }}
            className="ml-auto rounded border px-6 py-2.5 font-bold tracking-[0.14em] transition-colors hover:bg-crit hover:text-white"
            style={{
              fontSize: "clamp(0.6rem, 1.6vh, 0.85rem)",
              borderColor: COLOURS.red,
              color: COLOURS.red,
            }}
          >
            ACKNOWLEDGE
          </button>
        )}
        {critical && acknowledged && (
          <span
            className="ml-auto font-mono tracking-[0.16em] text-muted"
            style={{ fontSize: "clamp(0.58rem, 1.5vh, 0.8rem)" }}
          >
            ACKNOWLEDGED
          </span>
        )}
      </div>

      {/* ---------------- optical + trends ---------------- */}
      <div className="grid min-h-0 grid-cols-[minmax(390px,1.35fr)_2fr] gap-[0.9vh]">
        <div className="grid min-h-0 grid-rows-[minmax(0,1fr)_auto] gap-[0.9vh]">
          <Card accent={accent}>
            <div className="flex items-baseline justify-between">
              <span
                className="font-semibold uppercase tracking-[0.16em] text-muted"
                style={{ fontSize: "var(--v-label)" }}
              >
                Optical Sensor
              </span>
              <span
                className="font-mono font-bold tracking-[0.12em] transition-colors duration-300"
                style={{
                  fontSize: "var(--v-label)",
                  color: LED_TINT[String(p.led).toUpperCase()] || "#4a5568",
                }}
              >
                LED: {p.led || DASH}
              </span>
            </div>

            {/* Two columns rather than the one long stack: at 768p a single
                vertical list ran past the card and the GREEN and IR rows were
                silently clipped, which is the worst way for a readout to fail
                - present, plausible, and three lines short. */}
            <div className="mt-[0.7vh] grid min-h-0 flex-1 grid-cols-[auto_1fr] gap-x-4">
              <div className="flex flex-col justify-around border-r border-edge pr-4">
                <div className="flex items-center gap-2">
                  <span
                    className="h-6 w-6 shrink-0 rounded border border-edge transition-colors duration-300"
                    style={{ backgroundColor: hex || "#080c14" }}
                  />
                  <span
                    className="font-mono text-muted"
                    style={{ fontSize: "calc(var(--v-label) * 1.1)" }}
                  >
                    {hex || DASH}
                  </span>
                </div>

                <div
                  className="grid grid-cols-2 gap-x-3 gap-y-[0.3vh] font-mono font-bold tabular-nums"
                  style={{ fontSize: "var(--v-chan)" }}
                >
                  {[
                    ["R", p.red],
                    ["G", p.green],
                    ["B", p.blue],
                    ["C", p.clear],
                  ].map(([k, v]) => (
                    <span key={k} className="flex items-baseline gap-1.5">
                      <span className="text-muted" style={{ fontSize: "var(--v-label)" }}>
                        {k}
                      </span>
                      {num(v, 0)}
                    </span>
                  ))}
                </div>
              </div>

              <div className="flex min-h-0 flex-col">
                <div className="flex items-baseline justify-between">
                  <span
                    className="font-semibold uppercase tracking-[0.16em] text-muted"
                    style={{ fontSize: "var(--v-label)" }}
                  >
                    Beer-Lambert
                  </span>
                  <span
                    className="rounded-full border px-2 py-0.5 font-mono font-bold tracking-[0.1em]"
                    style={{
                      fontSize: "calc(var(--v-label) * 0.95)",
                      borderColor: p.calibrated ? COLOURS.green : "#4a5568",
                      color: p.calibrated ? COLOURS.green : "#4a5568",
                    }}
                  >
                    {p.calibrated ? "CAL ✓" : "CAL ✗"}
                  </span>
                </div>

                <div
                  className="mt-[0.4vh] font-mono tabular-nums"
                  style={{ fontSize: "calc(var(--v-label) * 1.3)" }}
                >
                  <div className="flex justify-between">
                    <span className="text-muted">Absorbance</span>
                    <span className="font-bold">{bl(p.absorbance)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted">Concentration</span>
                    <span>
                      <span className="font-bold">{bl(p.concentration)}</span>
                      <span
                        className="ml-1.5 text-muted"
                        style={{ fontSize: "calc(var(--v-label) * 0.9)" }}
                      >
                        rel. units
                      </span>
                    </span>
                  </div>
                </div>

                <div className="my-[0.5vh] border-t border-edge" />

                {/* Per-phase. Only the lit LED updates each cycle; the other
                    two hold their last measured value. */}
                <div
                  className="font-mono tabular-nums"
                  style={{ fontSize: "calc(var(--v-label) * 1.15)" }}
                >
                  {[
                    ["RED", p.abs_red, p.conc_red],
                    ["GREEN", p.abs_green, p.conc_green],
                    ["IR", p.abs_ir, p.conc_ir],
                  ].map(([name, a, c]) => (
                    <div key={name} className="flex items-baseline justify-between">
                      <span style={{ color: LED_TINT[name] }}>{name}</span>
                      <span className="text-muted">
                        A=<span className="text-ink">{bl(a)}</span>
                        <span className="ml-2.5">
                          C=<span className="text-ink">{bl(c)}</span>
                        </span>
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </Card>

          <LedCycle led={p.led} live={!degraded} />
        </div>

        <div className="grid min-h-0 grid-rows-2 gap-[0.9vh]">
          {/* Without a load cell there is no weight series to draw, so the
              panel says so rather than presenting an empty chart as a result. */}
          <Card accent={accent} absent={absent("rate")} className="!py-[0.8vh]">
            <div className="flex items-baseline justify-between">
              <span
                className="font-semibold uppercase tracking-[0.16em] text-muted"
                style={{ fontSize: "var(--v-label)" }}
              >
                Weight Trend — 60 pts
              </span>
              {!absent("rate") && (
                <span
                  className="font-mono tabular-nums text-muted"
                  style={{ fontSize: "var(--v-label)" }}
                >
                  {wLo === null ? DASH : `${wLo.toFixed(1)} – ${wHi.toFixed(1)} g`}
                </span>
              )}
            </div>
            {!absent("rate") && <Sparkline data={weightHistory} colour="#2979ff" />}
          </Card>

          <Card accent={accent} className="!py-[0.8vh]">
            <div className="flex items-baseline justify-between">
              <span
                className="font-semibold uppercase tracking-[0.16em] text-muted"
                style={{ fontSize: "var(--v-label)" }}
              >
                Z-Risk Trend — 60 pts
              </span>
              <span
                className="font-mono tabular-nums text-muted"
                style={{ fontSize: "var(--v-label)" }}
              >
                0 – 15
              </span>
            </div>
            <Sparkline
              data={zriskHistory}
              colour={COLOURS[triage] || COLOURS.unknown}
              fixed
              min={0}
              max={GAUGE_CAP}
            />
          </Card>
        </div>
      </div>

      {/* ---------------- overlays ---------------- */}
      <Toast toast={toast} />

      {alarming && (
        <div className="hg-flashover pointer-events-none fixed inset-0 z-50 bg-crit" />
      )}

      {degraded && (
        <div
          className="pointer-events-none fixed bottom-3 right-4 z-40 font-mono font-bold tracking-[0.3em] text-edge"
          style={{ fontSize: "clamp(1rem, 3vh, 2rem)", transform: "rotate(-8deg)" }}
        >
          {degradedLabel}
        </div>
      )}

      {critical && audioBlocked && (
        <div className="fixed bottom-3 left-1/2 z-50 -translate-x-1/2 rounded border border-watch bg-card px-5 py-2.5 text-watch"
             style={{ fontSize: "clamp(0.65rem, 1.7vh, 0.85rem)" }}>
          Click anywhere to enable alarm audio
        </div>
      )}
    </div>
  );
}
