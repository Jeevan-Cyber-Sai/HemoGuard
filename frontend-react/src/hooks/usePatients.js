import { useCallback, useEffect, useState } from "react";

const KEY = "hemoguard.patients.v1";

/**
 * The ward roster.
 *
 * There is exactly ONE sensor node, so at most one bed can be wired to it at a
 * time. That is modelled explicitly with a `sensor` flag rather than pretending
 * every bed has hardware: assigning it to a bed moves it off whichever bed held
 * it. Every other bed shows as unmonitored, which is the truth - inventing
 * vitals for them would put fabricated patients on a ward board.
 *
 * Persisted to localStorage so the roster survives a reload. It is ward
 * bookkeeping, not measurement, so the browser is the right place for it.
 */
const SEED = [
  {
    id: "p-seed-4",
    bed: "Bed 4",
    name: "Monitored patient",
    admitted: "Today",
    note: "Post-operative",
    sensor: true,
  },
];

function load() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return SEED;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || !parsed.length) return SEED;
    // Only one sensor exists; if a stored roster claims otherwise, keep the
    // first and clear the rest rather than showing two live beds.
    let seen = false;
    return parsed.map((p) => {
      const sensor = Boolean(p.sensor) && !seen;
      if (sensor) seen = true;
      return { ...p, sensor };
    });
  } catch {
    return SEED;
  }
}

export function usePatients() {
  const [patients, setPatients] = useState(load);

  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(patients));
    } catch {
      /* storage full or blocked - the roster still works for this session */
    }
  }, [patients]);

  const addPatient = useCallback((data) => {
    const entry = {
      id: `p-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      bed: data.bed?.trim() || "Unassigned",
      name: data.name?.trim() || "Unnamed",
      admitted: data.admitted?.trim() || "Today",
      note: data.note?.trim() || "",
      sensor: false,
    };
    setPatients((prev) => [...prev, entry]);
    return entry;
  }, []);

  const removePatient = useCallback((id) => {
    setPatients((prev) => prev.filter((p) => p.id !== id));
  }, []);

  /** Moves the single sensor to this bed, clearing it everywhere else. */
  const assignSensor = useCallback((id) => {
    setPatients((prev) =>
      prev.map((p) => ({ ...p, sensor: p.id === id ? !p.sensor : false })),
    );
  }, []);

  return { patients, addPatient, removePatient, assignSensor };
}
