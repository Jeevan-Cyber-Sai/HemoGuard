import { useEffect, useRef } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { WebSocketProvider, useHemoGuard } from "./context/WebSocketContext";
import Sidebar from "./components/Sidebar";
import PageTransition from "./components/PageTransition";
import Toast from "./components/Toast";
import ViewSelection from "./views/ViewSelection";
import NurseStation from "./views/NurseStation";
import PatientDetail from "./views/PatientDetail";
import CriticalAlert from "./views/CriticalAlert";

/**
 * Jumps to the alert view when triage escalates into red.
 *
 * Keyed on escalationId, which the context bumps once per transition INTO red
 * rather than on every red frame - otherwise the router would drag the user
 * back here every second and they could never navigate away from an
 * unacknowledged alarm.
 */
function EscalationRouter() {
  const { escalationId } = useHemoGuard();
  const navigate = useNavigate();
  const location = useLocation();
  const seen = useRef(0);

  useEffect(() => {
    if (escalationId === 0 || escalationId === seen.current) return;
    seen.current = escalationId;
    if (location.pathname !== "/critical") navigate("/critical");
  }, [escalationId, navigate, location.pathname]);

  return null;
}

function AudioGate() {
  const { audioBlocked, triage } = useHemoGuard();
  if (!audioBlocked || triage === "unknown") return null;

  return (
    <div
      style={{
        position: "fixed",
        bottom: 16,
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: 80,
        background: "#fff",
        border: "1px solid var(--amber)",
        borderRadius: 10,
        padding: "9px 18px",
        fontSize: 12,
        color: "var(--amber)",
        boxShadow: "0 6px 18px rgba(16,24,40,.08)",
      }}
    >
      Click anywhere to enable alarm audio
    </div>
  );
}

function Shell() {
  return (
    <div style={{ display: "flex", height: "100%" }}>
      <Sidebar />
      <main style={{ flex: 1, minWidth: 0, overflow: "hidden" }}>
        <PageTransition>
          <Routes>
            <Route path="/" element={<ViewSelection />} />
            <Route path="/nurse" element={<NurseStation />} />
            <Route path="/patient" element={<PatientDetail />} />
            <Route path="/critical" element={<CriticalAlert />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </PageTransition>
      </main>
      <EscalationRouter />
      <Toast />
      <AudioGate />
    </div>
  );
}

export default function App() {
  return (
    <WebSocketProvider>
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </WebSocketProvider>
  );
}
