import { useEffect, useRef, useState } from "react";
import { API_URL, WS_URL, RETRY_MS, MAX_POINTS, DEGRADED } from "../lib/constants";

/**
 * Owns the connection to the backend and the trend history.
 *
 * The transport behaviour is carried over unchanged: prime once from GET
 * /latest, then live on the WebSocket, reconnecting every RETRY_MS whenever the
 * socket closes.
 */
export function useFeed() {
  const [payload, setPayload] = useState(null);
  const [weightHistory, setWeightHistory] = useState([]);
  const [zriskHistory, setZriskHistory] = useState([]);
  const [calibration, setCalibration] = useState(null);

  // Monotonic tag on each calibration event. Two failures in a row carry
  // identical fields, and without this the second would be object-equal to the
  // first and never re-fire the toast.
  const calSeq = useRef(0);

  // Refs, not state: these gate whether a frame is recorded and must be read at
  // their current value inside the socket callback. As state they would be
  // captured stale by the closure and every frame would look like the first.
  const lastPlotted = useRef(null);
  const socketRef = useRef(null);
  const retryRef = useRef(null);
  const closedByUs = useRef(false);

  useEffect(() => {
    // Plot each reading once. A stale repeat re-sends the last reading, and the
    // REST pre-populate is immediately echoed by the WebSocket priming frame -
    // both would otherwise duplicate a point in the trends.
    function accept(data) {
      if (!data || typeof data !== "object") return;

      // Calibration progress shares the socket with sensor frames. It carries
      // no reading, so it must never reach the payload state or the trends -
      // treated as one it would blank every card on screen.
      if (data.type === "calibration" || data.type === "reference") {
        calSeq.current += 1;
        setCalibration({ ...data, seq: calSeq.current });
        return;
      }

      setPayload(data);

      const stale = DEGRADED[data.status] !== undefined;
      const fresh = data.timestamp && data.timestamp !== lastPlotted.current;
      if (stale || data.status !== "live" || !fresh) return;

      lastPlotted.current = data.timestamp;
      if (typeof data.weight === "number") {
        setWeightHistory((h) => [...h, data.weight].slice(-MAX_POINTS));
      }
      if (typeof data.z_risk === "number") {
        setZriskHistory((h) => [...h, data.z_risk].slice(-MAX_POINTS));
      }
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
        // A closed socket means the backend is gone, not that the last reading
        // aged out - say so rather than reusing the stale wording.
        setPayload((p) => ({ ...(p || {}), status: "offline" }));
        retryRef.current = setTimeout(connect, RETRY_MS); // retry every 3 s
      });

      socket.addEventListener("error", () => {
        try {
          socket.close();
        } catch {
          /* close() triggers the retry */
        }
      });
    }

    // Backend down, or CORS refused it - the WebSocket is the real feed anyway.
    fetch(`${API_URL}/latest`, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data && data.status !== "waiting") accept(data);
      })
      .catch((err) => console.warn("prime failed:", err.message))
      .finally(connect);

    return () => {
      closedByUs.current = true;
      clearTimeout(retryRef.current);
      try {
        socketRef.current?.close();
      } catch {
        /* already gone */
      }
    };
  }, []);

  return { payload, weightHistory, zriskHistory, calibration };
}
