import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useHemoGuard } from "../context/WebSocketContext";
import CalibrationButton from "./CalibrationButton";

export default function Header({ title, subtitle }) {
  const { isLive, degradedLabel, alarming, triage } = useHemoGuard();
  const navigate = useNavigate();
  const [clock, setClock] = useState("--:--:--");

  useEffect(() => {
    const id = setInterval(
      () => setClock(new Date().toLocaleTimeString("en-GB")),
      1000,
    );
    return () => clearInterval(id);
  }, []);

  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        gap: 16,
        padding: "16px 24px",
        background: "#fff",
        borderBottom: "1px solid var(--edge)",
      }}
    >
      <div>
        <div style={{ fontSize: 17, fontWeight: 700, letterSpacing: "-0.01em" }}>
          {title}
        </div>
        {subtitle && (
          <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>
            {subtitle}
          </div>
        )}
      </div>

      <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12 }}>
        <span
          style={{
            fontSize: 12,
            color: "var(--muted)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {clock}
        </span>

        <CalibrationButton />

        {/* Bell - routes to the alert view, and marks itself when alarming. */}
        <button
          onClick={() => navigate("/critical")}
          aria-label="Critical alerts"
          style={{
            position: "relative",
            width: 34,
            height: 34,
            borderRadius: 8,
            border: "1px solid var(--edge)",
            background: alarming ? "#fdf2f5" : "#fff",
            display: "grid",
            placeItems: "center",
            fontSize: 14,
            color: alarming ? "var(--primary)" : "var(--muted)",
          }}
        >
          ⌁
          {alarming && (
            <span
              style={{
                position: "absolute",
                top: 6,
                right: 6,
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: "var(--primary)",
                border: "1.5px solid #fff",
              }}
            />
          )}
        </button>

        <div
          className="triage-tint"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 7,
            padding: "6px 12px",
            borderRadius: 999,
            border: `1px solid ${isLive ? "#12b76a33" : "#d0d5dd"}`,
            background: isLive ? "#12b76a10" : "#f9fafb",
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.07em",
            color: isLive ? "var(--green)" : "var(--faint)",
          }}
        >
          <span style={{ position: "relative", display: "flex", width: 7, height: 7 }}>
            {isLive && (
              <span
                className="ping"
                style={{
                  position: "absolute",
                  inset: 0,
                  borderRadius: "50%",
                  background: "var(--green)",
                }}
              />
            )}
            <span
              style={{
                position: "relative",
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: isLive ? "var(--green)" : "var(--faint)",
              }}
            />
          </span>
          {isLive ? "LIVE" : degradedLabel || "—"}
        </div>
      </div>
    </header>
  );
}
