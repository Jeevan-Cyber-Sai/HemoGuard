"""Stand-in for the ESP32 colour node, for testing without hardware.

Serves the same contract as firmware/hemoguard_color/:

    GET /sensor      phase snapshot, rotating RED -> GREEN -> IR every 2 s
    GET /calibrate   blocks 10 s, stores a water baseline, returns it
    GET /cal_status  whether a baseline is currently held

Use it to prove the backend and dashboard are healthy before wiring the board
in, so a fault can be pinned on one half or the other instead of both at once.

    python tools/fake_node.py
    set HEMOGUARD_ESP32_IP=127.0.0.1:8123
    uvicorn backend.server:app --port 8000

Options:
    --port 8123     listen port
    --fault         report valid:false, to exercise the sensor-fault path
    --hot           emit a bleeding-red colour, to drive the critical band
    --calibrated    start with a baseline already stored, skipping the 10 s wait
"""

import argparse
import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# (led, raw r, g, b, c, normalised r, g, b) - raw counts are what the backend
# scores; the normalised trio is display-only, exactly as the firmware sends it.
PHASES = [
    ("RED",   150,  70, 55, 205, 187, 87, 68),
    ("GREEN",  60, 104, 88, 418,  36, 63, 53),
    ("IR",     20,  18, 15, 300,  17, 15, 12),
]

# --hot: a far redder RED phase. hb_ratio = red/(r+g+b+c) = 250/535 = 0.467,
# which is (0.467 - 0.15) / 0.08 = 3.96 sigma and escalates to the red band.
# The normal phases top out at 2.03, so the critical path is otherwise
# unreachable without hardware.
HOT_RED = ("RED", 250, 60, 45, 180, 220, 53, 40)

# The "water" reference the calibration sweep would measure. Water transmits
# more than blood, so every CLEAR here sits above the corresponding sample
# value and the resulting absorbances come out positive:
#   RED   log10(540/205) = 0.42
#   GREEN log10(860/418) = 0.31
#   IR    log10(455/300) = 0.18
WATER_BASELINE = {
    "red":   {"R": 380.0, "G": 190.0, "B": 150.0, "C": 540.0},
    "green": {"R": 130.0, "G": 240.0, "B": 195.0, "C": 860.0},
    "ir":    {"R":  32.0, "G":  28.0, "B":  24.0, "C": 455.0},
}

BASELINE_KEYS = ["red", "green", "ir"]

CAL_DURATION_S = 10.0

START = time.time()
FAULT = False
HOT = False

_lock = threading.Lock()
_calibrated = False
_baseline = None

# Last computed absorbance / concentration per phase, mirroring the firmware's
# behaviour of holding the other two while one LED is lit.
_abs_phase = [0.0, 0.0, 0.0]
_conc_phase = [0.0, 0.0, 0.0]


def absorbance_for(index, intensity):
    """A = log10(I0 / I), or 0.0 when there is no usable reference."""
    if not _calibrated or _baseline is None:
        return 0.0
    i0 = _baseline[BASELINE_KEYS[index]]["C"]
    if i0 <= 0 or intensity <= 0:
        return 0.0
    return math.log10(i0 / intensity)


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, body):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/sensor":
            return self._sensor()
        if self.path == "/calibrate":
            return self._calibrate()
        if self.path == "/cal_status":
            return self._cal_status()
        self.send_response(404)
        self.end_headers()

    def _sensor(self):
        global _abs_phase, _conc_phase

        elapsed = time.time() - START
        phase = int(elapsed // 2)
        idx = phase % 3
        led, r, g, b, c, nr, ng, nb = HOT_RED if (HOT and idx == 0) else PHASES[idx]

        with _lock:
            a = absorbance_for(idx, float(c))
            _abs_phase[idx] = a
            _conc_phase[idx] = a          # epsilon = path length = 1.0
            abs_now = _abs_phase[idx]
            conc_now = _conc_phase[idx]
            snapshot_abs = list(_abs_phase)
            snapshot_conc = list(_conc_phase)
            calibrated = _calibrated

        self._json(200, {
            "uptime_ms": phase * 2000,
            "weight": None, "spo2": None, "pulse": None,
            "red": r, "green": g, "blue": b, "clear": c,
            "norm_r": nr, "norm_g": ng, "norm_b": nb,
            "hex": "#%02X%02X%02X" % (nr, ng, nb),
            "led": led,
            "valid": not FAULT,
            "calibrated": calibrated,
            "absorbance": round(abs_now, 4),
            "concentration": round(conc_now, 4),
            "abs_red": round(snapshot_abs[0], 4),
            "abs_green": round(snapshot_abs[1], 4),
            "abs_ir": round(snapshot_abs[2], 4),
            "conc_red": round(snapshot_conc[0], 4),
            "conc_green": round(snapshot_conc[1], 4),
            "conc_ir": round(snapshot_conc[2], 4),
        })

    def _calibrate(self):
        """Blocks for the full sweep before replying, exactly as the node does."""
        global _calibrated, _baseline

        print(f"  calibrating for {CAL_DURATION_S:g}s...")
        time.sleep(CAL_DURATION_S)

        with _lock:
            _baseline = {k: dict(v) for k, v in WATER_BASELINE.items()}
            _calibrated = True

        print("  baseline stored")
        self._json(200, {
            "cmd": "calibrate",
            "status": "complete",
            "baseline": _baseline,
            "uptime_ms": int((time.time() - START) * 1000),
            "message": "Water baseline stored",
        })

    def _cal_status(self):
        with _lock:
            self._json(200, {
                "calibrated": _calibrated,
                "baseline": _baseline,
                "uptime_ms": int((time.time() - START) * 1000),
            })

    def log_message(self, *args):
        pass   # one line per second per client is pure noise


def main():
    global FAULT, HOT, _calibrated, _baseline

    parser = argparse.ArgumentParser(description="Simulated HemoGuard colour node")
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--fault", action="store_true",
                        help="report valid:false to test the sensor-fault path")
    parser.add_argument("--hot", action="store_true",
                        help="emit a bleeding-red colour to drive the critical band")
    parser.add_argument("--calibrated", action="store_true",
                        help="start with a water baseline already stored")
    args = parser.parse_args()

    FAULT = args.fault
    HOT = args.hot
    if args.calibrated:
        _baseline = {k: dict(v) for k, v in WATER_BASELINE.items()}
        _calibrated = True

    mode = "  (FAULT mode)" if FAULT else "  (HOT mode)" if HOT else ""
    print(f"Simulated node on http://127.0.0.1:{args.port}/sensor{mode}")
    print(f"  baseline: {'stored' if _calibrated else 'none - POST /calibrate to take one'}")
    print(f"  set HEMOGUARD_ESP32_IP=127.0.0.1:{args.port}")

    # Deliberately single-threaded. The ESP32's WebServer handles one
    # connection at a time, so /sensor really is unanswerable for the 10 s that
    # /calibrate is running. Serving them concurrently here would hide the fact
    # that the backend has to suppress its staleness alarm for the duration.
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
