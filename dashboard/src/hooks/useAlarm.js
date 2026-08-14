import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Web Audio alarms - no files, no assets.
 *
 *   amber   one soft chime, 880 Hz sine, 200 ms
 *   red     440 Hz square, 500 ms on / 300 ms off, looping until acknowledged
 */
export function useAlarm(triage) {
  const [acknowledged, setAcknowledged] = useState(false);
  const [audioBlocked, setAudioBlocked] = useState(true);

  const ctxRef = useRef(null);
  const timerRef = useRef(null);
  const runningRef = useRef(false);
  const prevTriage = useRef(null);

  const ensureAudio = useCallback(() => {
    if (!ctxRef.current) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      ctxRef.current = new Ctx();
    }
    if (ctxRef.current.state === "suspended") ctxRef.current.resume();
    return ctxRef.current;
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
    runningRef.current = false;
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const startAlarm = useCallback(() => {
    if (runningRef.current) return;
    runningRef.current = true;
    const fire = () => beep(440, "square", 500, 0.22);
    fire();
    timerRef.current = setInterval(fire, 800);
  }, [beep]);

  // Browsers block audio until the user interacts with the page. Track that so
  // a silent alarm is never mistaken for no alarm.
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

  useEffect(() => {
    const band = triage || null;

    if (band !== prevTriage.current) {
      // Leaving critical clears the acknowledgement, so the next escalation
      // alarms again rather than staying silent.
      if (prevTriage.current === "red" && band !== "red") setAcknowledged(false);
      if (band === "amber") beep(880, "sine", 200, 0.16);
      prevTriage.current = band;
    }
  }, [triage, beep]);

  const critical = triage === "red";
  const alarming = critical && !acknowledged;

  useEffect(() => {
    if (alarming) startAlarm();
    else stopAlarm();
  }, [alarming, startAlarm, stopAlarm]);

  useEffect(() => stopAlarm, [stopAlarm]);

  return {
    critical,
    alarming,
    acknowledged,
    audioBlocked,
    acknowledge: () => setAcknowledged(true),
  };
}
