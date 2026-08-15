import { useHemoGuard } from "../context/WebSocketContext";

export default function CalibrationButton() {
  const { calibrating, countdown, startCalibration } = useHemoGuard();

  return (
    <button
      onClick={startCalibration}
      disabled={calibrating}
      className={calibrating ? "cal-pulse" : ""}
      title="Take a water baseline (10 s)"
      style={{
        padding: "7px 14px",
        borderRadius: 8,
        border: "1px solid var(--blue)",
        color: "var(--blue)",
        background: "#fff",
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: "0.06em",
        cursor: calibrating ? "not-allowed" : "pointer",
        whiteSpace: "nowrap",
      }}
    >
      {calibrating ? `CALIBRATING ${countdown}s` : "CALIBRATE"}
    </button>
  );
}
