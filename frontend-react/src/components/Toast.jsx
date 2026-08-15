import { useHemoGuard } from "../context/WebSocketContext";

const TINT = {
  info: "var(--blue)",
  success: "var(--green)",
  error: "var(--primary)",
};

export default function Toast() {
  const { toast } = useHemoGuard();
  if (!toast) return null;
  const tint = TINT[toast.kind] || TINT.info;

  return (
    <div
      key={toast.seq}
      role="status"
      className="toast-in"
      style={{
        position: "fixed",
        top: 16,
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: 90,
        background: "#fff",
        border: `1px solid ${tint}`,
        borderRadius: 10,
        padding: "10px 18px",
        fontSize: 13,
        fontWeight: 500,
        color: tint,
        boxShadow: "0 8px 24px rgba(16,24,40,.10)",
        maxWidth: "80vw",
      }}
    >
      {toast.message}
    </div>
  );
}
