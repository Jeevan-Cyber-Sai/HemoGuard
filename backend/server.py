"""HemoGuard backend.

Polls the ESP32's GET /sensor endpoint over Wi-Fi, scores each reading with the
Z-score risk engine, logs it to CSV, and broadcasts it to every connected
WebSocket client.

Run with:
    set HEMOGUARD_ESP32_IP=192.168.1.45
    uvicorn backend.server:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import csv
import os
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_FILE = Path(os.environ.get("HEMOGUARD_LOG_FILE", BASE_DIR / "logs" / "experiment_log.csv"))

# Address of the sensor node. A port may be included (e.g. "192.168.1.45:8080");
# without one it defaults to port 80, which is where the ESP32 web server lives.
ESP32_IP = os.environ.get("HEMOGUARD_ESP32_IP", "").strip()
SENSOR_URL = f"http://{ESP32_IP}/sensor" if ESP32_IP else ""
CALIBRATE_URL = f"http://{ESP32_IP}/calibrate" if ESP32_IP else ""
CAL_STATUS_URL = f"http://{ESP32_IP}/cal_status" if ESP32_IP else ""

POLL_INTERVAL_SECONDS = 1.0
POLL_TIMEOUT_SECONDS = 2.0   # must stay below the interval budget

# The node blocks for a 10 s sweep before it answers /calibrate, so the ceiling
# has to clear that with room for the round trip.
CALIBRATION_TIMEOUT_SECONDS = 15.0

# Origins permitted to call the REST endpoints. An origin is an exact
# scheme+host+port match, so a dashboard served on :8000 needs the port spelled
# out - "http://localhost" alone only matches port 80. Override the whole list
# with HEMOGUARD_ALLOWED_ORIGINS as comma-separated values.
#
# NOTE: a page opened straight from disk sends "Origin: null" and is no longer
# allowed here, so GET /latest pre-populate does not work over file://. The
# dashboard still runs - WebSocket upgrades are not subject to CORS - it just
# waits for the first broadcast instead of pre-filling.
WARD_SERVER_ORIGIN = os.environ.get("HEMOGUARD_WARD_ORIGIN", "").strip()

_DEFAULT_ORIGINS = [
    "http://localhost",
    "http://localhost:8000",
    "http://127.0.0.1",
    "http://127.0.0.1:8000",
]

_configured = os.environ.get("HEMOGUARD_ALLOWED_ORIGINS", "").strip()
if _configured:
    ALLOWED_ORIGINS = [o.strip() for o in _configured.split(",") if o.strip()]
else:
    ALLOWED_ORIGINS = list(_DEFAULT_ORIGINS)
    if WARD_SERVER_ORIGIN:
        ALLOWED_ORIGINS.append(WARD_SERVER_ORIGIN)

STALE_AFTER_SECONDS = 10
STALE_CHECK_INTERVAL = 2  # how often the heartbeat re-checks freshness
ROLLING_WINDOW = 5        # weight readings used for the bleeding-rate slope

# 1 g of blood ~ 1 mL. Kept explicit so it can be tuned to 1.06 g/mL if needed.
GRAMS_PER_ML = 1.0

# Baselines: (mean, sd)
BASELINE_RATE = (1.5, 1.0)
BASELINE_HB = (0.15, 0.08)
BASELINE_PULSE = (75.0, 12.0)
BASELINE_SPO2 = (98.0, 1.5)

WEIGHTS = {"rate": 0.40, "hb": 0.30, "pr": 0.20, "spo2": 0.10}

AMBER_THRESHOLD = 1.0
RED_THRESHOLD = 2.5
CONSECUTIVE_CRITICAL_REQUIRED = 2  # anti-spike: two cycles at >= 2.5 to escalate

# How many consecutive invalid readings may be papered over by replaying the
# last good scores. A dropped frame or two is worth riding out; past that the
# held band stops being evidence of anything. A latched red alarms over a
# patient nobody is measuring, and a latched green hides a bleed that began
# after the sensor died - so the band degrades to an explicit unknown instead.
CONSECUTIVE_INVALID_LIMIT = 7   # ~15 s at the node's 2 s phase cadence

# The colorimeter only sees blood colour usefully under the red illumination
# LED - the TCS34725's IR-blocking filter makes the IR phase read near-black,
# and green shifts the ratio wholesale. Only this phase feeds z_hb.
RED_PHASE_LED = "RED"

CSV_COLUMNS = [
    "timestamp", "weight_g", "spo2", "pulse_bpm", "red", "green", "blue",
    "clear", "led", "bleeding_rate", "hb_ratio", "z_risk", "triage", "valid",
    "scored_channels",
    # Beer-Lambert. conc_* are numerically identical to the absorbances by
    # construction (epsilon = path length = 1.0), so logging them records both.
    # These are the TRUE signed values: a negative absorbance means the sample
    # transmitted more light than the water reference, which is evidence of a
    # stale baseline and must survive into the log even though the dashboard
    # floors the display at 0.00.
    "calibrated", "absorbance", "conc_red", "conc_green", "conc_ir",
]

# Display-only fields the node may add. They are forwarded to the dashboard
# untouched and never scored: norm_* are clear-normalised 0-255 values, the
# wrong units for hb_ratio, which always uses the raw red/green/blue/clear
# counts so it stays comparable with BASELINE_HB.
#
# The Beer-Lambert fields ride along here too. They are computed on the node
# against its own water baseline and are deliberately NOT fed to the risk
# engine: the weights and thresholds are unchanged, and an unvalidated relative
# absorbance has no business moving a triage band.
PASSTHROUGH_FIELDS = (
    "norm_r", "norm_g", "norm_b", "hex",
    "calibrated", "absorbance", "concentration",
    "abs_red", "abs_green", "abs_ir",
    "conc_red", "conc_green", "conc_ir",
)


# --------------------------------------------------------------------------
# Risk engine
# --------------------------------------------------------------------------

class RiskEngine:
    """Stateful scorer: keeps the rolling weight window and the anti-spike counter."""

    def __init__(self):
        self.weight_window = deque(maxlen=ROLLING_WINDOW)  # (seconds, grams)
        self.consecutive_critical = 0
        self.red_phase_ratio = None  # last hb ratio measured under the RED LED
        self.last_scored = None      # carried forward when a reading is invalid

    def bleeding_rate(self, weight, moment):
        """mL/min from a least-squares slope over the last ROLLING_WINDOW readings.

        Least squares rather than (last - first) so a single noisy sample cannot
        swing the rate. Negative slopes (weight dropping) clamp to 0.

        A gap in the feed breaks the window: a slope drawn across a dropout
        compares readings minutes apart and reads far too low, hiding a bleed
        for the 5 readings it takes the window to refill. Discontinuous history
        is dropped instead.
        """
        now = moment.timestamp()
        if self.weight_window and now - self.weight_window[-1][0] > STALE_AFTER_SECONDS:
            self.weight_window.clear()

        self.weight_window.append((now, float(weight)))

        if len(self.weight_window) < 2:
            return 0.0

        times = [t for t, _ in self.weight_window]
        weights = [w for _, w in self.weight_window]
        mean_t = sum(times) / len(times)
        mean_w = sum(weights) / len(weights)

        denominator = sum((t - mean_t) ** 2 for t in times)
        if denominator == 0:  # identical timestamps
            return 0.0

        numerator = sum((t - mean_t) * (w - mean_w) for t, w in self.weight_window)
        grams_per_second = numerator / denominator
        ml_per_minute = (grams_per_second * 60.0) / GRAMS_PER_ML
        return max(0.0, ml_per_minute)

    @staticmethod
    def hb_ratio(red, green, blue, clear):
        total = float(red) + float(green) + float(blue) + float(clear)
        if total <= 0:
            return 0.0
        return float(red) / total

    @staticmethod
    def _optional(reading, field, zero_is_absent=True):
        """One channel as a number, or None when it has nothing to report.

        Two different things both mean "do not score this":
          - JSON null, sent by a node where the sensor is not fitted at all
            (the colour node reports weight/spo2/pulse this way);
          - the firmware's 0 failure sentinel. Scored literally, spo2=0 is a
            65-sigma desaturation and pulse=0 a 6-sigma bradycardia.

        Weight passes zero_is_absent=False: an empty drape genuinely weighs
        0.0 g, and reading that as absent would blind the bleeding rate for
        exactly as long as the patient is not bleeding.
        """
        value = reading.get(field)
        if value is None:
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if zero_is_absent and value <= 0:
            return None
        return value

    def hold(self):
        """Last computed scores, unchanged - used when a reading is invalid.

        Deliberately touches no engine state: the weight window, the anti-spike
        counter and the red-phase ratio all stay exactly as the last good
        reading left them.
        """
        if self.last_scored is None:
            return {"bleeding_rate": None, "hb_ratio": None, "z_rate": 0.0,
                    "z_hb": 0.0, "z_pr": 0.0, "z_spo2": 0.0, "z_risk": 0.0,
                    "triage": "green", "scored_channels": []}
        return dict(self.last_scored)

    def score(self, reading, moment):
        # Absent weight must not reach the rolling window: appending a
        # placeholder would fabricate a slope out of nothing, and clearing the
        # window would discard history a load cell may still be feeding.
        weight = self._optional(reading, "weight", zero_is_absent=False)
        rate = self.bleeding_rate(weight, moment) if weight is not None else None

        # Only the red illumination phase produces a meaningful blood-colour
        # ratio. Other phases hold the last red-phase value rather than
        # contributing 0, which would make z_risk oscillate every cycle as the
        # LED rotates and flap the triage band.
        if str(reading.get("led", "")).upper() == RED_PHASE_LED:
            self.red_phase_ratio = self.hb_ratio(
                reading.get("red", 0), reading.get("green", 0),
                reading.get("blue", 0), reading.get("clear", 0),
            )
        ratio = self.red_phase_ratio if self.red_phase_ratio is not None else 0.0
        scored_hb = self.red_phase_ratio is not None

        pulse = self._optional(reading, "pulse")
        spo2 = self._optional(reading, "spo2")

        z_rate = 0.0 if rate is None else max(
            0.0, (rate - BASELINE_RATE[0]) / BASELINE_RATE[1])
        z_hb = max(0.0, (ratio - BASELINE_HB[0]) / BASELINE_HB[1]) if scored_hb else 0.0
        z_pr = 0.0 if pulse is None else max(
            0.0, (pulse - BASELINE_PULSE[0]) / BASELINE_PULSE[1])
        z_spo2 = 0.0 if spo2 is None else max(
            0.0, (BASELINE_SPO2[0] - spo2) / BASELINE_SPO2[1])

        # Score over the channels this node actually reports, renormalised by
        # their share of the weighting. A sensor that is not fitted must not
        # dilute the score towards green: on the colour-only node the fixed
        # formula caps z_risk at 0.30 * z_hb, which can never reach the 2.5 red
        # threshold no matter how much blood the colorimeter sees.
        #
        # With all four channels present the divisor is 1.0, so this is exactly
        # the original weighted sum.
        present = {}
        if rate is not None:
            present["rate"] = z_rate
        if scored_hb:
            present["hb"] = z_hb
        if pulse is not None:
            present["pr"] = z_pr
        if spo2 is not None:
            present["spo2"] = z_spo2

        share = sum(WEIGHTS[name] for name in present)
        if share > 0:
            z_risk = sum(WEIGHTS[name] * z for name, z in present.items()) / share
        else:
            z_risk = 0.0

        triage = self._triage(z_risk)

        self.last_scored = {
            "bleeding_rate": None if rate is None else round(rate, 3),
            "hb_ratio": round(ratio, 4) if scored_hb else None,
            "z_rate": round(z_rate, 3),
            "z_hb": round(z_hb, 3),
            "z_pr": round(z_pr, 3),
            "z_spo2": round(z_spo2, 3),
            "z_risk": round(z_risk, 3),
            "triage": triage,
            "scored_channels": sorted(present),
        }
        return dict(self.last_scored)

    def _triage(self, z_risk):
        """Anti-spike: a single cycle at >= 2.5 holds at amber; the second escalates."""
        if z_risk >= RED_THRESHOLD:
            self.consecutive_critical += 1
            if self.consecutive_critical >= CONSECUTIVE_CRITICAL_REQUIRED:
                return "red"
            return "amber"

        self.consecutive_critical = 0
        return "amber" if z_risk >= AMBER_THRESHOLD else "green"


# --------------------------------------------------------------------------
# CSV logging
# --------------------------------------------------------------------------

def rotate_if_schema_changed():
    """Retire a log whose header predates the current CSV_COLUMNS.

    Appending wider rows under a narrower header silently shifts every column
    from that point on, which is worse than losing the file: the older rows
    still parse, just wrongly. The old log is renamed rather than deleted so
    the earlier run stays available.
    """
    if not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0:
        return

    with open(LOG_FILE, "r", newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle), None)

    if header == CSV_COLUMNS:
        return

    retired = LOG_FILE.with_name(
        f"{LOG_FILE.stem}.{datetime.now():%Y%m%dT%H%M%S}{LOG_FILE.suffix}")
    LOG_FILE.rename(retired)
    print(f"Log schema changed - previous log retired to {retired.name}")


def append_to_csv(payload):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    is_new = not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0

    with open(LOG_FILE, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if is_new:
            writer.writerow(CSV_COLUMNS)
        writer.writerow([
            payload.get("timestamp"),
            payload.get("weight"),
            payload.get("spo2"),
            payload.get("pulse"),
            payload.get("red"),
            payload.get("green"),
            payload.get("blue"),
            payload.get("clear"),
            payload.get("led"),
            payload.get("bleeding_rate"),
            payload.get("hb_ratio"),
            payload.get("z_risk"),
            payload.get("triage"),
            payload.get("valid"),
            "|".join(payload.get("scored_channels") or []),
            payload.get("calibrated"),
            payload.get("absorbance"),
            payload.get("conc_red"),
            payload.get("conc_green"),
            payload.get("conc_ir"),
        ])


# --------------------------------------------------------------------------
# Connection manager
# --------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self):
        self.connections = set()
        self.lock = asyncio.Lock()

    async def connect(self, websocket):
        await websocket.accept()
        async with self.lock:
            self.connections.add(websocket)

    async def disconnect(self, websocket):
        async with self.lock:
            self.connections.discard(websocket)

    async def broadcast(self, payload):
        async with self.lock:
            targets = list(self.connections)

        dead = []
        for websocket in targets:
            try:
                await websocket.send_json(payload)
            except Exception:
                dead.append(websocket)

        if dead:
            async with self.lock:
                for websocket in dead:
                    self.connections.discard(websocket)


# --------------------------------------------------------------------------
# Shared state
# --------------------------------------------------------------------------

manager = ConnectionManager()
engine = RiskEngine()

# triage starts unknown, not green: before the first reading arrives the system
# has measured nothing, and a reassuring green LOW on a dashboard that has never
# seen the patient is the wrong default.
latest_payload = {"status": "waiting", "triage": "unknown", "z_risk": None}
last_reading_time = None   # datetime of the most recent accepted reading
last_seen_uptime = None    # device uptime_ms of the last reading we processed
currently_stale = False
consecutive_invalid = 0    # runs of readings the node flagged valid:false

# The node blocks inside its 10 s calibration sweep and cannot serve /sensor
# while it runs, so every poll in that window times out. Without this flag the
# dashboard would flash STALE in the middle of a calibration the operator just
# started - an alarm state raised by normal operation.
calibrating = False

# Fields the ESP32 must supply for a response to count as a reading.
REQUIRED_FIELDS = ("weight", "spo2", "pulse", "red", "green", "blue", "clear", "led")


def reading_is_valid(reading):
    """A reading counts as valid unless the firmware explicitly said otherwise."""
    flag = reading.get("valid", True)
    return flag not in (False, 0, "false", "False")


def build_payload(reading, moment):
    # An invalid reading means a sensor that was supposed to answer did not.
    # Scoring it would feed failure sentinels into the engine, so the previous
    # scores are carried forward and the frame is flagged instead - but only
    # for CONSECUTIVE_INVALID_LIMIT readings, after which the band is no longer
    # presented as a live assessment.
    global consecutive_invalid

    valid = reading_is_valid(reading)

    if valid:
        consecutive_invalid = 0
        scored = engine.score(reading, moment)
        status = "live"
    else:
        consecutive_invalid += 1
        scored = engine.hold()
        if consecutive_invalid >= CONSECUTIVE_INVALID_LIMIT:
            status = "sensor_fault"
            scored = dict(scored, triage="unknown", z_risk=None)
        else:
            status = "sensor_invalid"

    payload = {
        "timestamp": reading.get("timestamp") or moment.isoformat(timespec="milliseconds"),
        "weight": reading.get("weight"),
        "spo2": reading.get("spo2"),
        "pulse": reading.get("pulse"),
        "red": reading.get("red"),
        "green": reading.get("green"),
        "blue": reading.get("blue"),
        "clear": reading.get("clear"),
        "led": reading.get("led"),
        "valid": valid,
        "status": status,
    }
    for field in PASSTHROUGH_FIELDS:
        if field in reading:
            payload[field] = reading[field]
    payload.update(scored)
    return payload


async def handle_reading(reading):
    """Score one reading from the node, log it, broadcast it."""
    global latest_payload, last_reading_time, last_seen_uptime, currently_stale

    if not isinstance(reading, dict):
        return

    missing = [field for field in REQUIRED_FIELDS if field not in reading]
    if missing:
        print(f"[{datetime.now().isoformat(timespec='milliseconds')}] "
              f"SKIP  /sensor response missing {missing}")
        return

    # The node refreshes its snapshot once per 2 s LED phase but we poll every
    # second, so the same reading is served twice. Processing both would double
    # every point in the trends and every row in the CSV.
    uptime = reading.get("uptime_ms")
    if uptime is not None and uptime == last_seen_uptime:
        return
    last_seen_uptime = uptime

    moment = datetime.now()
    reading = dict(reading)
    reading["timestamp"] = moment.isoformat(timespec="milliseconds")

    payload = build_payload(reading, moment)
    latest_payload = payload
    last_reading_time = moment
    currently_stale = False

    append_to_csv(payload)
    await manager.broadcast(payload)
    flag = "" if payload["valid"] else "  SENSOR_INVALID (scores held)"
    print(f"[{payload['timestamp']}] z_risk={payload['z_risk']} triage={payload['triage']} "
          f"rate={payload['bleeding_rate']} mL/min hb={payload['hb_ratio']}{flag}")


async def poll_sensor():
    """Fetch GET /sensor once a second for as long as the server runs."""
    if not SENSOR_URL:
        print("HEMOGUARD_ESP32_IP is not set - no sensor node to poll.")
        print("  set HEMOGUARD_ESP32_IP=192.168.1.45   (the IP the ESP32 prints on boot)")
        return

    print(f"Polling {SENSOR_URL} every {POLL_INTERVAL_SECONDS:g}s")
    announced_failure = False

    async with httpx.AsyncClient(timeout=POLL_TIMEOUT_SECONDS) as client:
        while True:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            try:
                response = await client.get(SENSOR_URL)
                response.raise_for_status()
                reading = response.json()
            except Exception as exc:
                # Unreachable node, timeout, or malformed body: report once per
                # outage rather than every second.
                if not announced_failure:
                    print(f"[{datetime.now().isoformat(timespec='milliseconds')}] "
                          f"POLL FAILED: {type(exc).__name__}: {exc}")
                    announced_failure = True
                await broadcast_stale()
                continue

            if announced_failure:
                print(f"[{datetime.now().isoformat(timespec='milliseconds')}] "
                      f"POLL RECOVERED")
                announced_failure = False

            try:
                await handle_reading(reading)
            except Exception as exc:  # one bad reading must not kill the poller
                print(f"[{datetime.now().isoformat(timespec='milliseconds')}] "
                      f"ERROR processing reading: {exc}")


async def broadcast_stale():
    """Send the last known payload flagged stale, so the dashboard keeps context."""
    global latest_payload, currently_stale

    if currently_stale:
        return  # already announced; don't spam clients

    if calibrating:
        return  # the node is busy by request, not unreachable

    # A single dropped poll is not staleness. Without this the poller flips the
    # ward badge to STALE on the first timeout, seconds after a perfectly good
    # reading, and any brief Wi-Fi hiccup reads as a dead node.
    if last_reading_time is not None and \
            datetime.now() - last_reading_time <= timedelta(seconds=STALE_AFTER_SECONDS):
        return

    currently_stale = True

    payload = dict(latest_payload)
    payload["status"] = "stale"
    latest_payload = payload
    await manager.broadcast(payload)
    print(f"[{datetime.now().isoformat(timespec='milliseconds')}] STALE - no fresh reading "
          f"in {STALE_AFTER_SECONDS}s")


async def staleness_monitor():
    """A node that answers with a frozen snapshot still needs to read as stale."""
    while True:
        await asyncio.sleep(STALE_CHECK_INTERVAL)
        if last_reading_time is None:
            continue
        if datetime.now() - last_reading_time > timedelta(seconds=STALE_AFTER_SECONDS):
            await broadcast_stale()


# --------------------------------------------------------------------------
# App lifecycle
# --------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app):
    rotate_if_schema_changed()

    poller = asyncio.create_task(poll_sensor())
    monitor = asyncio.create_task(staleness_monitor())

    print(f"Logging to {LOG_FILE}")
    if DASHBOARD_DIST.is_dir():
        print("Dashboard: http://localhost:8000/app/")
    else:
        print("Dashboard: dashboard/dist not built - run `npm run build` in "
              "dashboard/. Serving the legacy page at /frontend/index.html")
    print(f"CORS allowed origins: {', '.join(ALLOWED_ORIGINS)}")
    if not WARD_SERVER_ORIGIN and "HEMOGUARD_ALLOWED_ORIGINS" not in os.environ:
        print("CORS: no ward server origin set (HEMOGUARD_WARD_ORIGIN) - "
              "localhost only")

    yield

    poller.cancel()
    monitor.cancel()


app = FastAPI(title="HemoGuard", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],   # POST /calibrate
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/latest")
async def latest():
    return latest_payload


@app.get("/cal_status")
async def cal_status():
    """Ask the node whether it currently holds a water baseline."""
    if not CAL_STATUS_URL:
        return {"calibrated": False, "baseline": None,
                "error": "HEMOGUARD_ESP32_IP is not set"}
    try:
        async with httpx.AsyncClient(timeout=POLL_TIMEOUT_SECONDS) as client:
            response = await client.get(CAL_STATUS_URL)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        return {"calibrated": False, "baseline": None,
                "error": f"{type(exc).__name__}: {exc}"}


@app.post("/calibrate")
async def calibrate():
    """Run the node's water-baseline sweep, narrating it to every dashboard.

    Progress goes out over the WebSocket rather than only to the caller, so a
    second screen watching the same bed sees the calibration happen instead of
    silently reading absorbances from a baseline it never saw taken.
    """
    global calibrating

    if not CALIBRATE_URL:
        detail = "HEMOGUARD_ESP32_IP is not set - no sensor node to calibrate."
        await manager.broadcast({"type": "calibration", "status": "error",
                                 "message": detail})
        return {"status": "error", "message": detail}

    if calibrating:
        return {"status": "error", "message": "Calibration already in progress"}

    calibrating = True
    await manager.broadcast({
        "type": "calibration",
        "status": "started",
        "message": "Calibrating with water sample - hold still for 10s",
    })
    print(f"[{datetime.now().isoformat(timespec='milliseconds')}] "
          f"CALIBRATE started")

    try:
        async with httpx.AsyncClient(timeout=CALIBRATION_TIMEOUT_SECONDS) as client:
            response = await client.get(CALIBRATE_URL)
            response.raise_for_status()
            result = response.json()
    except Exception as exc:
        detail = f"Calibration failed - check sensor ({type(exc).__name__})"
        print(f"[{datetime.now().isoformat(timespec='milliseconds')}] "
              f"CALIBRATE FAILED: {type(exc).__name__}: {exc}")
        await manager.broadcast({"type": "calibration", "status": "error",
                                 "message": detail})
        return {"status": "error", "message": detail}
    finally:
        # Released before the broadcast so the next poll is free to resume, and
        # in `finally` so a failed sweep cannot wedge staleness off for good.
        calibrating = False

    baseline = result.get("baseline")
    if result.get("status") != "complete" or not baseline:
        detail = result.get("message") or "Calibration failed - retry"
        await manager.broadcast({"type": "calibration", "status": "error",
                                 "message": detail})
        return {"status": "error", "message": detail}

    print(f"[{datetime.now().isoformat(timespec='milliseconds')}] "
          f"CALIBRATE complete: {baseline}")
    await manager.broadcast({
        "type": "calibration",
        "status": "complete",
        "baseline": baseline,
        "message": result.get("message") or "Water baseline stored",
    })
    return result


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        await websocket.send_json(latest_payload)  # prime the client immediately
        while True:
            await websocket.receive_text()  # keeps the socket open; ignores input
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await manager.disconnect(websocket)


# --------------------------------------------------------------------------
# Static dashboard - mounted last so it cannot shadow the API routes above.
# --------------------------------------------------------------------------
# Anchored to BASE_DIR rather than a bare "frontend": StaticFiles resolves a
# relative path against the working directory, so launching uvicorn from
# anywhere but the repo root would abort at startup with "Directory 'frontend'
# does not exist".
app.mount("/frontend", StaticFiles(directory=BASE_DIR / "frontend"), name="frontend")

# The React dashboard, once `npm run build` has produced it. Mounted only when
# the build exists: StaticFiles raises at import time on a missing directory,
# which would take the whole API down just because the UI had not been built.
DASHBOARD_DIST = BASE_DIR / "dashboard" / "dist"

if DASHBOARD_DIST.is_dir():
    app.mount("/app", StaticFiles(directory=DASHBOARD_DIST, html=True), name="dashboard")

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/app/")
else:
    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/frontend/index.html")
