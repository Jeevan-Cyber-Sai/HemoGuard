"""Stand-in for the ESP32 colour node, for testing without hardware.

Serves the same GET /sensor contract as firmware/hemoguard_color/, rotating
RED -> GREEN -> IR every 2 s. Use it to prove the backend and dashboard are
healthy before wiring the board in, so a fault can be pinned on one half or
the other instead of both at once.

    python tools/fake_node.py
    set HEMOGUARD_ESP32_IP=127.0.0.1:8123
    uvicorn backend.server:app --port 8000

Options:
    --port 8123     listen port
    --fault         report valid:false, to exercise the sensor-fault path
"""

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# (led, raw r, g, b, c, normalised r, g, b) - raw counts are what the backend
# scores; the normalised trio is display-only, exactly as the firmware sends it.
PHASES = [
    ("RED",   150,  70, 55, 205, 187, 87, 68),
    ("GREEN",  60, 104, 88, 418,  36, 63, 53),
    ("IR",     20,  18, 15, 300,  17, 15, 12),
]

START = time.time()
FAULT = False


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/sensor":
            self.send_response(404)
            self.end_headers()
            return

        elapsed = time.time() - START
        phase = int(elapsed // 2)
        led, r, g, b, c, nr, ng, nb = PHASES[phase % 3]

        body = json.dumps({
            "uptime_ms": phase * 2000,
            "weight": None, "spo2": None, "pulse": None,
            "red": r, "green": g, "blue": b, "clear": c,
            "norm_r": nr, "norm_g": ng, "norm_b": nb,
            "hex": "#%02X%02X%02X" % (nr, ng, nb),
            "led": led,
            "valid": not FAULT,
        }).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass   # one line per second per client is pure noise


def main():
    global FAULT

    parser = argparse.ArgumentParser(description="Simulated HemoGuard colour node")
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--fault", action="store_true",
                        help="report valid:false to test the sensor-fault path")
    args = parser.parse_args()

    FAULT = args.fault

    print(f"Simulated node on http://127.0.0.1:{args.port}/sensor"
          f"{'  (FAULT mode)' if FAULT else ''}")
    print(f"  set HEMOGUARD_ESP32_IP=127.0.0.1:{args.port}")

    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
