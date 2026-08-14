const TINT = { info: "#2979ff", success: "#00e676", error: "#ff1744" };

/**
 * Single top-centre notification. One at a time by construction - the caller
 * holds one slot of state, so a new message replaces rather than stacks.
 */
export function Toast({ toast }) {
  if (!toast) return null;
  const tint = TINT[toast.kind] || TINT.info;

  return (
    <div
      key={toast.seq}
      role="status"
      className="hg-toast fixed top-3 z-[60] rounded border bg-card px-5 py-2.5 font-medium shadow-lg"
      style={{
        left: "50%",
        transform: "translateX(-50%)",
        borderColor: tint,
        color: tint,
        fontSize: "clamp(0.65rem, 1.7vh, 0.9rem)",
      }}
    >
      {toast.message}
    </div>
  );
}
