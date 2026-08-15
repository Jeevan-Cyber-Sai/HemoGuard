"""Stand-in for the ESP8266 vitals node (MAX30102), for testing without hardware.

Serves the same GET /data contract as firmware/hemoguard_vitals/.

    python tools/fake_vitals.py
    set HEMOGUARD_VITALS_IP=127.0.0.1:8124

Options:
    --port 8124     listen port
    --no-finger     report no finger, so SpO2 and pulse read as absent
    --demo          set demoMode:true, as the original simulated sketch did -
                    used to check the dashboard labels unmeasured values
"""

import argparse
import json
import math
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

START = time.time()
NO_FINGER = False
DEMO = False


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/data":
            self.send_response(404)
            self.end_headers()
            return

        t = time.time() - START

        finger = not NO_FINGER
        # Gentle physiological drift so the dashboard has something to move.
        hr = int(72 + 6 * math.sin(t / 11.0))
        spo2 = int(97 + 1.5 * math.sin(t / 17.0))
        ir = 45000 if finger else 1200

        body = json.dumps({
            "ir": ir,
            "fingerDetected": finger,
            "isCalibrating": False,
            "hr": hr if finger else 0,
            "spo2": spo2 if finger else 0,
            "hr_valid": finger,
            "spo2_valid": finger,
            "demoMode": DEMO,
            "window": 100 if finger else 0,
        }).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    global NO_FINGER, DEMO

    parser = argparse.ArgumentParser(description="Simulated HemoGuard vitals node")
    parser.add_argument("--port", type=int, default=8124)
    parser.add_argument("--no-finger", action="store_true")
    parser.add_argument("--demo", action="store_true",
                        help="report demoMode:true, as the simulated sketch did")
    args = parser.parse_args()

    NO_FINGER = args.no_finger
    DEMO = args.demo

    mode = "  (DEMO/simulated values)" if DEMO else ""
    print(f"Simulated vitals node on http://127.0.0.1:{args.port}/data{mode}")
    print(f"  set HEMOGUARD_VITALS_IP=127.0.0.1:{args.port}")

    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
