import { useEffect, useRef, useState } from "react";

const FIELDS = [
  { key: "bed", label: "Bed", placeholder: "Bed 7", required: true, width: "0 0 120px" },
  { key: "name", label: "Patient name", placeholder: "A. Patient", required: true, width: "1 1 auto" },
  { key: "admitted", label: "Admitted", placeholder: "Today", width: "0 0 130px" },
  { key: "note", label: "Note", placeholder: "Post-operative", width: "1 1 100%" },
];

export default function AddPatientDialog({ open, onClose, onAdd, existingBeds }) {
  const [form, setForm] = useState({ bed: "", name: "", admitted: "", note: "" });
  const [error, setError] = useState("");
  const firstField = useRef(null);

  useEffect(() => {
    if (!open) return;
    setForm({ bed: "", name: "", admitted: "", note: "" });
    setError("");
    // Focus lands on the first field so the dialog is usable without reaching
    // for the mouse.
    const id = setTimeout(() => firstField.current?.focus(), 60);
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(id);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  if (!open) return null;

  const submit = (e) => {
    e.preventDefault();
    const bed = form.bed.trim();
    const name = form.name.trim();
    if (!bed || !name) {
      setError("Bed and patient name are both required.");
      return;
    }
    // A duplicate bed would put two cards in the same bay and there would be no
    // way to tell which one the sensor belonged to.
    if (existingBeds.some((b) => b.toLowerCase() === bed.toLowerCase())) {
      setError(`${bed} already exists on the ward.`);
      return;
    }
    onAdd(form);
    onClose();
  };

  return (
    <>
      <div
        className="scrim-in"
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(22,24,29,.34)",
          backdropFilter: "blur(3px)",
          zIndex: 100,
        }}
      />
      <form
        onSubmit={submit}
        role="dialog"
        aria-modal="true"
        aria-label="Admit patient"
        className="dialog-in"
        style={{
          position: "fixed",
          top: "50%",
          left: "50%",
          zIndex: 101,
          width: "min(520px, calc(100vw - 40px))",
          background: "var(--card)",
          borderRadius: "var(--r-lg)",
          boxShadow: "var(--shadow-lg)",
          border: "1px solid var(--edge)",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            padding: "20px 24px 16px",
            borderBottom: "1px solid var(--edge)",
            background:
              "linear-gradient(180deg, var(--primary-wash) 0%, transparent 100%)",
          }}
        >
          <div style={{ fontSize: 17, fontWeight: 700, letterSpacing: "-0.02em" }}>
            Admit patient
          </div>
          <div style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 3 }}>
            The bay is added to the board. Assign the sensor to it afterwards to
            start monitoring.
          </div>
        </div>

        <div style={{ padding: 24, display: "flex", flexWrap: "wrap", gap: 14 }}>
          {FIELDS.map((f, i) => (
            <label key={f.key} style={{ flex: f.width, minWidth: 0 }}>
              <span className="label">
                {f.label}
                {f.required && <span style={{ color: "var(--primary)" }}> *</span>}
              </span>
              <input
                ref={i === 0 ? firstField : null}
                value={form[f.key]}
                placeholder={f.placeholder}
                onChange={(e) => {
                  setForm((p) => ({ ...p, [f.key]: e.target.value }));
                  setError("");
                }}
                style={{
                  width: "100%",
                  marginTop: 6,
                  padding: "10px 12px",
                  fontSize: 14,
                  color: "var(--ink)",
                  background: "var(--bg)",
                  border: "1px solid var(--edge-strong)",
                  borderRadius: "var(--r-sm)",
                  outline: "none",
                  transition: "border-color 160ms ease, box-shadow 160ms ease",
                }}
                onFocus={(e) => {
                  e.target.style.borderColor = "var(--primary)";
                  e.target.style.boxShadow = "0 0 0 3px var(--primary-wash)";
                }}
                onBlur={(e) => {
                  e.target.style.borderColor = "var(--edge-strong)";
                  e.target.style.boxShadow = "none";
                }}
              />
            </label>
          ))}

          {error && (
            <div
              className="pop"
              style={{
                flex: "1 1 100%",
                fontSize: 12.5,
                color: "var(--primary-deep)",
                background: "var(--primary-wash)",
                padding: "9px 12px",
                borderRadius: "var(--r-sm)",
              }}
            >
              {error}
            </div>
          )}
        </div>

        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 10,
            padding: "0 24px 22px",
          }}
        >
          <button
            type="button"
            onClick={onClose}
            className="press"
            style={{
              padding: "10px 18px",
              borderRadius: "var(--r-sm)",
              border: "1px solid var(--edge-strong)",
              fontSize: 13.5,
              fontWeight: 600,
              color: "var(--muted)",
            }}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="press"
            style={{
              padding: "10px 22px",
              borderRadius: "var(--r-sm)",
              background: "var(--primary)",
              color: "#fff",
              fontSize: 13.5,
              fontWeight: 650,
              boxShadow: "0 2px 8px -2px rgba(232,54,93,.5)",
            }}
          >
            Admit
          </button>
        </div>
      </form>
    </>
  );
}
