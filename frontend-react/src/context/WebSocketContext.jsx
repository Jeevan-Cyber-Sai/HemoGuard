import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

const HOST = location.hostname || "localhost";
const WS_URL = `ws://${HOST}:8000/ws`;
export const API_URL = `http://${HOST}:8000`;

const RETRY_MS = 3000;
const MAX_POINTS = 60;
const MAX_EVENTS = 20;

// The backend clamps every channel at 6 sigma, so the fused score cannot exceed
// 6 either. Scaling by 15 - as an older spec did - would peg the displayed risk
// at 40/100 no matter how bad the reading got.
export const Z_RISK_MAX = 6;

// 1 mL of blood weighs about 1.06 g, so grams -> mL divides. Multiplying, as the
// brief said, would overstate the loss by 12%.
const BLOOD_DENSITY_G_PER_ML = 1.06;

// Statuses that mean "what is on screen is not a live measurement".
const DEGRADED = {
  stale: "STALE",
  offline: "OFFLINE",
  waiting: "WAITING",
  sensor_invalid: "SENSOR",
  sensor_fault: "SENSOR FAULT",
};

export const TRIAGE_COLOUR = {
  green: "#12b76a",
  amber: "#f79009",
  red: "#e8365d",
  unknown: "#98a2b3",
};

export const TRIAGE_LABEL = {
  green: "STABLE",
  amber: "WATCH",
  red: "CRITICAL",
  unknown: "NO DATA",
};

const Ctx = createContext(null);

export function useHemoGuard() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useHemoGuard must be used inside WebSocketProvider");
  return ctx;
}

/** Numeric or null - never a silent zero for an absent channel. */
function num(value) {
  if (value === null || value === undefined) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function WebSocketProvider({ children }) {
  const [sensorData, setSensorData] = useState(null);
  const [sparklineHistory, setSparklineHistory] = useState([]);
  const [abnormalEvents, setAbnormalEvents] = useState([]);
  const [acknowledged, setAcknowledged] = useState(false);
  const [audioBlocked, setAudioBlocked] = useState(true);
  const [toast, setToast] = useState(null);
  const [calibrating, setCalibrating] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const [escalationId, setEscalationId] = useState(0);

  const lastPlotted = useRef(null);
  const prevTriage = useRef(null);
  const socketRef = useRef(null);
  const retryRef = useRef(null);
  const closedByUs = useRef(false);
  const toastTimer = useRef(null);

  const showToast = useCallback((message, kind = "info") => {
    clearTimeout(toastTimer.current);
    setToast({ message, kind, seq: Date.now() });
    toastTimer.current = setTimeout(() => setToast(null), 4000);
  }, []);

  // ---------------------------------------------------------------- audio
  const audioCtx = useRef(null);
  const alarmTimer = useRef(null);
  const alarmRunning = useRef(false);

  const ensureAudio = useCallback(() => {
    if (!audioCtx.current) {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      if (!Ctor) return null;
      audioCtx.current = new Ctor();
    }
    if (audioCtx.current.state === "suspended") audioCtx.current.resume();
    return audioCtx.current;
  }, []);

  const beep = useCallback(
    (freq, type, ms, peak) => {
      const ctx = ensureAudio();
      if (!ctx || ctx.state !== "running") return;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = type;
      osc.frequency.value = freq;
      const now = ctx.currentTime;
      const dur = ms / 1000;
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(peak, now + 0.012);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + dur);
      osc.connect(gain).connect(ctx.destination);
      osc.start(now);
      osc.stop(now + dur + 0.02);
    },
    [ensureAudio],
  );

  const stopAlarm = useCallback(() => {
    alarmRunning.current = false;
    if (alarmTimer.current) {
      clearInterval(alarmTimer.current);
      alarmTimer.current = null;
    }
  }, []);

  const startAlarm = useCallback(() => {
    if (alarmRunning.current) return;
    alarmRunning.current = true;
    const fire = () => beep(440, "square", 500, 0.22);
    fire();
    alarmTimer.current = setInterval(fire, 800);
  }, [beep]);

  // Browsers block audio until the page is interacted with, so a silent alarm
  // must never be mistaken for no alarm.
  useEffect(() => {
    const unlock = () => {
      const ctx = ensureAudio();
      setAudioBlocked(!ctx || ctx.state !== "running");
    };
    const opts = { passive: true };
    ["click", "keydown", "touchstart"].forEach((e) =>
      document.addEventListener(e, unlock, opts),
    );
    return () =>
      ["click", "keydown", "touchstart"].forEach((e) =>
        document.removeEventListener(e, unlock, opts),
      );
  }, [ensureAudio]);

  // ---------------------------------------------------------------- feed
  useEffect(() => {
    function accept(data) {
      if (!data || typeof data !== "object") return;

      // Calibration and reference progress share the socket. They carry no
      // reading, so treating one as a frame would blank every card on screen.
      // Pad weighing reports through the same socket so every screen sees it.
      if (data.type === "weigh") {
        showToast(data.message || "Pad weighed",
                  data.status === "error" ? "error" : "success");
        return;
      }

      if (data.type === "calibration" || data.type === "reference") {
        if (data.status === "started") {
          setCalibrating(true);
          setCountdown(10);
          showToast(data.message || "Calibrating…", "info");
        } else {
          setCalibrating(false);
          setCountdown(0);
          showToast(
            data.message || "Calibration complete",
            data.status === "error" ? "error" : "success",
          );
        }
        return;
      }

      setSensorData(data);

      const degraded = DEGRADED[data.status] !== undefined;
      const fresh = data.timestamp && data.timestamp !== lastPlotted.current;
      if (degraded || data.status !== "live" || !fresh) return;
      lastPlotted.current = data.timestamp;

      const z = num(data.z_risk);
      setSparklineHistory((h) =>
        [
          ...h,
          {
            t: new Date(data.timestamp).toLocaleTimeString("en-GB"),
            risk: z === null ? null : Math.min(100, (z / Z_RISK_MAX) * 100),
            bloodLoss:
              num(data.weight) === null
                ? null
                : num(data.weight) / BLOOD_DENSITY_G_PER_ML,
            rate: num(data.bleeding_rate),
            pulse: num(data.pulse),
            spo2: num(data.spo2),
          },
        ].slice(-MAX_POINTS),
      );
    }

    function connect() {
      clearTimeout(retryRef.current);
      let socket;
      try {
        socket = new WebSocket(WS_URL);
      } catch {
        retryRef.current = setTimeout(connect, RETRY_MS);
        return;
      }
      socketRef.current = socket;

      socket.addEventListener("message", (evt) => {
        try {
          accept(JSON.parse(evt.data));
        } catch (err) {
          console.warn("bad frame:", err.message);
        }
      });
      socket.addEventListener("close", () => {
        if (closedByUs.current) return;
        setSensorData((p) => ({ ...(p || {}), status: "offline" }));
        retryRef.current = setTimeout(connect, RETRY_MS);
      });
      socket.addEventListener("error", () => {
        try {
          socket.close();
        } catch {
          /* close() drives the retry */
        }
      });
    }

    fetch(`${API_URL}/latest`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d && d.status !== "waiting") accept(d);
      })
      .catch((err) => console.warn("prime failed:", err.message))
      .finally(connect);

    return () => {
      closedByUs.current = true;
      clearTimeout(retryRef.current);
      clearTimeout(toastTimer.current);
      try {
        socketRef.current?.close();
      } catch {
        /* already gone */
      }
    };
  }, [showToast]);

  // ------------------------------------------------------- derived values
  const p = sensorData || {};
  const degradedLabel = sensorData ? DEGRADED[p.status] || null : "WAITING";
  const isLive = degradedLabel === null;
  const triage = p.triage || "unknown";
  const isCalibrated = Boolean(p.calibrated);

  const zRisk = num(p.z_risk);
  const metrics = useMemo(
    () => ({
      bloodLoss:
        num(p.weight) === null ? null : num(p.weight) / BLOOD_DENSITY_G_PER_ML,
      bloodLossRate: num(p.bleeding_rate),
      pulse: num(p.pulse),
      spo2: num(p.spo2),
      riskScore: zRisk === null ? null : Math.min(100, (zRisk / Z_RISK_MAX) * 100),
      zRisk,

      // Optical - raw counts are what the backend actually scores; the
      // normalised trio and hex are display only.
      red: num(p.red),
      green: num(p.green),
      blue: num(p.blue),
      clear: num(p.clear),
      hex: p.hex || null,
      led: p.led || null,

      // Beer-Lambert
      absorbance: num(p.absorbance),
      concentration: num(p.concentration),
      hbIndex: num(p.hb_index),
      hbMode: p.hb_mode || null,
      hbConcentration: num(p.hb_g_dl),
      bloodMl: num(p.blood_ml),
      hbMassMg: num(p.hb_mass_mg),

      // Per phase. cal_* distinguishes "no baseline" from "zero absorbance" -
      // a phase that was never referenced is a different statement from one
      // that matched the water exactly.
      phases: [
        { name: "RED", abs: num(p.abs_red), conc: num(p.conc_red), cal: p.cal_red },
        { name: "GREEN", abs: num(p.abs_green), conc: num(p.conc_green), cal: p.cal_green },
        { name: "IR", abs: num(p.abs_ir), conc: num(p.conc_ir), cal: p.cal_ir },
      ],

      padCount: num(p.pad_count),
      lastPadG: num(p.last_pad_g),
      dryPadG: num(p.dry_pad_g),
      scaleReady: Boolean(p.scale_ready),

      finger: p.finger,
      vitalsSimulated: Boolean(p.vitals_simulated),
      scored: Array.isArray(p.scored_channels) ? p.scored_channels : [],
    }),
    [p, zRisk],
  );

  // Abnormal events are recorded from live frames only, so a held or stale
  // reading cannot manufacture a log entry that never happened.
  useEffect(() => {
    if (!sensorData || sensorData.status !== "live") return;
    const entries = [];
    const stamp = sensorData.timestamp;
    if (metrics.spo2 !== null && metrics.spo2 < 94)
      entries.push({ label: "SpO₂ below 94%", value: `${metrics.spo2}%`, severity: "amber" });
    if (metrics.pulse !== null && metrics.pulse > 100)
      entries.push({ label: "Tachycardia", value: `${metrics.pulse} bpm`, severity: "amber" });
    if (metrics.bloodLossRate !== null && metrics.bloodLossRate > 1.7)
      entries.push({
        label: "Bleeding rate elevated",
        value: `${metrics.bloodLossRate.toFixed(1)} mL/min`,
        severity: "red",
      });
    if (!entries.length) return;
    setAbnormalEvents((prev) => {
      const next = [...entries.map((e) => ({ ...e, time: stamp })), ...prev];
      // Same condition every second would otherwise flood the log.
      const seen = new Set();
      return next
        .filter((e) => {
          const key = `${e.label}|${e.time}`;
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        })
        .slice(0, MAX_EVENTS);
    });
  }, [sensorData, metrics]);

  // ------------------------------------------------------------- alarms
  useEffect(() => {
    if (triage === prevTriage.current) return;
    const previous = prevTriage.current;
    prevTriage.current = triage;

    if (previous === "red" && triage !== "red") setAcknowledged(false);
    if (triage === "amber") beep(880, "sine", 200, 0.16);
    // Bumped once per escalation, so the app navigates to the alert view on the
    // transition into red rather than on every red frame.
    if (triage === "red" && previous !== "red") setEscalationId((n) => n + 1);
  }, [triage, beep]);

  const alarming = triage === "red" && !acknowledged;

  useEffect(() => {
    if (alarming) startAlarm();
    else stopAlarm();
  }, [alarming, startAlarm, stopAlarm]);

  useEffect(() => stopAlarm, [stopAlarm]);

  useEffect(() => {
    if (!calibrating) return;
    const id = setInterval(() => setCountdown((c) => Math.max(0, c - 1)), 1000);
    return () => clearInterval(id);
  }, [calibrating]);

  const startCalibration = useCallback(async () => {
    if (calibrating) return;
    setCalibrating(true);
    setCountdown(10);
    try {
      const res = await fetch(`${API_URL}/calibrate`, { method: "POST" });
      const data = await res.json();
      if (data && data.status === "error") {
        setCalibrating(false);
        setCountdown(0);
        showToast(data.message || "Calibration failed", "error");
      }
    } catch {
      setCalibrating(false);
      setCountdown(0);
      showToast("Calibration failed — backend unreachable", "error");
    }
  }, [calibrating, showToast]);

  const value = {
    sensorData,
    metrics,
    triage,
    isLive,
    degradedLabel,
    isCalibrated,
    abnormalEvents,
    sparklineHistory,
    acknowledged,
    acknowledge: () => setAcknowledged(true),
    alarming,
    escalationId,
    audioBlocked,
    toast,
    showToast,
    calibrating,
    countdown,
    startCalibration,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
