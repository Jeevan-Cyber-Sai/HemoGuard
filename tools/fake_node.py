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
# A blood sample under each illumination, chosen to behave the way haemoglobin
# actually does rather than to produce round numbers. Hb absorbs ~150x more
# strongly at 542-577 nm than at 660 nm, so the GREEN phase returns very little
# light while RED and IR come back nearly unattenuated. That ordering is the
# whole signal the optical channel is built on, and a simulator that got it
# backwards would validate a scoring bug instead of catching one.
#
# (led, raw r, g, b, c, normalised r, g, b)
PHASES = [
    ("RED",   460,  60, 40, 520, 225, 29, 20),
    ("GREEN",  40,  95, 30, 110,  93, 220, 70),
    ("IR",     25,  20, 18, 380,  17,  13, 12),
]

# --hot: a heavier sample. The green phase drops further still, which is what a
# rising haemoglobin concentration does to transmitted green light.
HOT_GREEN = ("GREEN", 30, 45, 22, 45, 170, 255, 125)

# The "water" reference the calibration sweep would measure. Water absorbs
# almost nothing across the visible band, so every CLEAR sits above the
# corresponding sample value:
#   A_red   = log10(560/520) = 0.032    (blood barely absorbs red)
#   A_green = log10(880/110) = 0.903    (blood absorbs green hard)
#   A_ir    = log10(430/380) = 0.054
#   hb_index = A_green - A_red = 0.871
WATER_BASELINE = {
    "red":   {"R": 500.0, "G": 210.0, "B": 165.0, "C": 560.0},
    "green": {"R": 150.0, "G": 430.0, "B": 205.0, "C": 880.0},
    "ir":    {"R":  30.0, "G":  26.0, "B":  22.0, "C": 430.0},
}

BASELINE_KEYS = ["red", "green", "ir"]

CAL_DURATION_S = 10.0

START = time.time()
FAULT = False
HOT = False

_lock = threading.Lock()
_calibrated = False
_calibrating = False
_cal_end = 0.0
_baseline = None
NO_IR = False

# Last computed absorbance / concentration per phase, mirroring the firmware's
# behaviour of holding the other two while one LED is lit.
_abs_phase = [0.0, 0.0, 0.0]
_conc_phase = [0.0, 0.0, 0.0]


def absorbance_for(index, intensity):
    """A = log10(I0 / I), or 0.0 when there is no usable reference."""
    if not _calibrated or _baseline is None:
        return 0.0
    entry = _baseline.get(BASELINE_KEYS[index])
    if not entry:
        return 0.0
    i0 = entry["C"]
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
        led, r, g, b, c, nr, ng, nb = (
            HOT_GREEN if (HOT and idx == 1) else PHASES[idx])

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
            "cal_red": bool(_baseline and _baseline.get("red")),
            "cal_green": bool(_baseline and _baseline.get("green")),
            "cal_ir": bool(_baseline and _baseline.get("ir")),
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
        """Acknowledges immediately; the sweep runs on a timer, as the node does."""
        global _calibrating, _cal_end

        with _lock:
            if _calibrating:
                self._json(409, {"cmd": "calibrate", "status": "busy",
                                 "message": "Calibration already running"})
                return
            _calibrating = True
            _cal_end = time.time() + CAL_DURATION_S

        print(f"  calibration started ({CAL_DURATION_S:g}s)")
        self._json(200, {
            "cmd": "calibrate",
            "status": "started",
            "duration_ms": int(CAL_DURATION_S * 1000),
            "message": "Calibration started - poll /cal_status",
        })

    def _cal_status(self):
        global _calibrating, _calibrated, _baseline

        with _lock:
            if _calibrating and time.time() >= _cal_end:
                _baseline = {k: dict(v) for k, v in WATER_BASELINE.items()}
                if NO_IR:
                    # Dead IR LED: that phase returns no light, so it gets no
                    # baseline. RED and GREEN still do, and those are the two
                    # the haemoglobin index is built from.
                    _baseline["ir"] = None
                _calibrated = True
                _calibrating = False
                print(f"  baseline stored{' (no IR)' if NO_IR else ''}")

            progress = 0.0
            if _calibrating:
                progress = max(0.0, min(1.0,
                                        1.0 - (_cal_end - time.time()) / CAL_DURATION_S))
            elif _calibrated:
                progress = 1.0

            self._json(200, {
                "calibrated": _calibrated,
                "calibrating": _calibrating,
                "progress": round(progress, 2),
                "baseline": _baseline,
                "uptime_ms": int((time.time() - START) * 1000),
            })

    def log_message(self, *args):
        pass   # one line per second per client is pure noise


def main():
    global FAULT, HOT, NO_IR, _calibrated, _baseline

    parser = argparse.ArgumentParser(description="Simulated HemoGuard colour node")
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--fault", action="store_true",
                        help="report valid:false to test the sensor-fault path")
    parser.add_argument("--hot", action="store_true",
                        help="heavier sample: drives the critical band once calibrated")
    parser.add_argument("--calibrated", action="store_true",
                        help="start with a water baseline already stored")
    parser.add_argument("--no-ir", action="store_true",
                        help="simulate a dead IR LED: no IR baseline")
    args = parser.parse_args()

    FAULT = args.fault
    HOT = args.hot
    NO_IR = args.no_ir
    if args.calibrated:
        _baseline = {k: dict(v) for k, v in WATER_BASELINE.items()}
        if args.no_ir:
            _baseline["ir"] = None
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
