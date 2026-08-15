import { NavLink, useLocation } from "react-router-dom";
import { useHemoGuard, TRIAGE_COLOUR } from "../context/WebSocketContext";

const NAV = [
  { to: "/", label: "Views", end: true },
  { to: "/nurse", label: "Nurse Station" },
  { to: "/patient", label: "Patient Detail" },
  { to: "/critical", label: "Critical Alert" },
];

const ROW_H = 40;
const GAP = 2;

export default function Sidebar() {
  const { triage, alarming, isLive } = useHemoGuard();
  const location = useLocation();
  const tint = TRIAGE_COLOUR[triage] || TRIAGE_COLOUR.unknown;

  const active = Math.max(
    0,
    NAV.findIndex((n) => (n.end ? location.pathname === n.to : location.pathname === n.to)),
  );

  return (
    <aside
      style={{
        width: 232,
        flex: "none",
        borderRight: "1px solid var(--edge)",
        background: "rgba(255,255,255,.72)",
        backdropFilter: "blur(8px)",
        display: "flex",
        flexDirection: "column",
        padding: "22px 14px 16px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "0 10px 24px" }}>
        <span
          className="triage-tint"
          style={{
            width: 30,
            height: 30,
            borderRadius: 9,
            background: "var(--primary)",
            display: "grid",
            placeItems: "center",
            color: "#fff",
            fontSize: 14,
            boxShadow: "0 3px 10px -3px rgba(232,54,93,.6)",
          }}
        >
          ♥
        </span>
        <div>
          <div style={{ fontWeight: 700, fontSize: 15, letterSpacing: "-0.02em" }}>
            HemoGuard
          </div>
          <div style={{ fontSize: 10, color: "var(--faint)", letterSpacing: "0.06em" }}>
            BLEEDING MONITOR
          </div>
        </div>
      </div>

      {/* The highlight is one element that slides between rows, rather than a
          background appearing and disappearing per link. It gives the nav a
          sense of continuity as you move through it. */}
      <nav style={{ position: "relative", display: "flex", flexDirection: "column", gap: GAP }}>
        <span
          aria-hidden="true"
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            height: ROW_H,
            borderRadius: 9,
            background: "var(--primary-wash)",
            transform: `translateY(${active * (ROW_H + GAP)}px)`,
            transition: "transform 380ms var(--ease-spring)",
          }}
        />
        <span
          aria-hidden="true"
          style={{
            position: "absolute",
            left: 0,
            width: 3,
            height: 18,
            borderRadius: 2,
            background: "var(--primary)",
            transform: `translateY(${active * (ROW_H + GAP) + (ROW_H - 18) / 2}px)`,
            transition: "transform 380ms var(--ease-spring)",
          }}
        />

        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            style={({ isActive }) => ({
              position: "relative",
              display: "flex",
              alignItems: "center",
              gap: 10,
              height: ROW_H,
              padding: "0 12px",
              fontSize: 13.5,
              fontWeight: isActive ? 650 : 500,
              color: isActive ? "var(--primary-deep)" : "var(--muted)",
              transition: "color 220ms ease",
            })}
          >
            {item.label}
            {item.to === "/critical" && alarming && (
              <span
                className="ping"
                style={{
                  marginLeft: "auto",
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  background: "var(--primary)",
                }}
              />
            )}
          </NavLink>
        ))}
      </nav>

      <div
        style={{
          marginTop: "auto",
          padding: 14,
          borderRadius: "var(--r)",
          border: "1px solid var(--edge)",
          background: "var(--bg-deep)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <span
            className="triage-tint"
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: isLive ? "var(--green)" : "var(--faint)",
            }}
          />
          <span className="label" style={{ color: "var(--muted)" }}>
            {isLive ? "Feed live" : "No feed"}
          </span>
        </div>
        <div
          className="triage-tint"
          style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 8, lineHeight: 1.5 }}
        >
          Triage{" "}
          <span style={{ color: tint, fontWeight: 650 }}>
            {triage === "unknown" ? "no data" : triage}
          </span>
        </div>
      </div>
    </aside>
  );
}
