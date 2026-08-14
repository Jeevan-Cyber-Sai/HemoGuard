"""HemoGuard backend.

Watches sensor_data.json (written by serial_reader.py), scores each reading with
the Z-score risk engine, logs it to CSV, and broadcasts it to every connected
WebSocket client.

Run with:
    uvicorn backend.server:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import csv
import json
import os
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
SENSOR_FILE = Path(os.environ.get("HEMOGUARD_SENSOR_FILE", BASE_DIR / "sensor_data.json"))
LOG_FILE = Path(os.environ.get("HEMOGUARD_LOG_FILE", BASE_DIR / "logs" / "experiment_log.csv"))

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

CSV_COLUMNS = [
    "timestamp", "weight_g", "spo2", "pulse_bpm", "red", "green", "blue",
    "clear", "led", "bleeding_rate", "hb_ratio", "z_risk", "triage",
]


# --------------------------------------------------------------------------
# Risk engine
# --------------------------------------------------------------------------

class RiskEngine:
    """Stateful scorer: keeps the rolling weight window and the anti-spike counter."""

    def __init__(self):
        self.weight_window = deque(maxlen=ROLLING_WINDOW)  # (seconds, grams)
        self.consecutive_critical = 0

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

    def score(self, reading, moment):
        rate = self.bleeding_rate(reading.get("weight", 0), moment)
        ratio = self.hb_ratio(
            reading.get("red", 0), reading.get("green", 0),
            reading.get("blue", 0), reading.get("clear", 0),
        )
        pulse = float(reading.get("pulse", BASELINE_PULSE[0]))
        spo2 = float(reading.get("spo2", BASELINE_SPO2[0]))

        z_rate = max(0.0, (rate - BASELINE_RATE[0]) / BASELINE_RATE[1])
        z_hb = max(0.0, (ratio - BASELINE_HB[0]) / BASELINE_HB[1])
        z_pr = max(0.0, (pulse - BASELINE_PULSE[0]) / BASELINE_PULSE[1])
        z_spo2 = max(0.0, (BASELINE_SPO2[0] - spo2) / BASELINE_SPO2[1])

        z_risk = (
            WEIGHTS["rate"] * z_rate
            + WEIGHTS["hb"] * z_hb
            + WEIGHTS["pr"] * z_pr
            + WEIGHTS["spo2"] * z_spo2
        )

        triage = self._triage(z_risk)

        return {
            "bleeding_rate": round(rate, 3),
            "hb_ratio": round(ratio, 4),
            "z_rate": round(z_rate, 3),
            "z_hb": round(z_hb, 3),
            "z_pr": round(z_pr, 3),
            "z_spo2": round(z_spo2, 3),
            "z_risk": round(z_risk, 3),
            "triage": triage,
        }

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

latest_payload = {"status": "waiting", "triage": "green", "z_risk": 0.0}
last_reading_time = None   # datetime of the most recent accepted reading
last_seen_timestamp = None # dedupes repeated watchdog events for one write
currently_stale = False


def parse_timestamp(value):
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return None


def build_payload(reading, moment):
    scored = engine.score(reading, moment)
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
        "status": "live",
    }
    payload.update(scored)
    return payload


async def process_sensor_file():
    """Read the sensor file, score it, log it, broadcast it."""
    global latest_payload, last_reading_time, last_seen_timestamp, currently_stale

    try:
        with open(SENSOR_FILE, "r", encoding="utf-8") as handle:
            reading = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return  # mid-write or absent; the next event will pick it up

    if not isinstance(reading, dict):
        return

    file_timestamp = reading.get("timestamp")
    if file_timestamp is not None and file_timestamp == last_seen_timestamp:
        return  # duplicate watchdog event for a write we already handled
    last_seen_timestamp = file_timestamp

    moment = parse_timestamp(file_timestamp) or datetime.now()
    age = (datetime.now() - moment).total_seconds()

    if age > STALE_AFTER_SECONDS:
        # The file changed but carries an old reading - warn rather than score it.
        await broadcast_stale()
        return

    payload = build_payload(reading, moment)
    latest_payload = payload
    last_reading_time = datetime.now()
    currently_stale = False

    append_to_csv(payload)
    await manager.broadcast(payload)
    print(f"[{payload['timestamp']}] z_risk={payload['z_risk']} triage={payload['triage']} "
          f"rate={payload['bleeding_rate']} mL/min hb={payload['hb_ratio']}")


async def broadcast_stale():
    """Send the last known payload flagged stale, so the dashboard keeps context."""
    global latest_payload, currently_stale

    if currently_stale:
        return  # already announced; don't spam clients
    currently_stale = True

    payload = dict(latest_payload)
    payload["status"] = "stale"
    latest_payload = payload
    await manager.broadcast(payload)
    print(f"[{datetime.now().isoformat(timespec='milliseconds')}] STALE - no fresh reading "
          f"in {STALE_AFTER_SECONDS}s")


async def staleness_monitor():
    """A silent sensor produces no file events, so freshness needs its own heartbeat."""
    while True:
        await asyncio.sleep(STALE_CHECK_INTERVAL)
        if last_reading_time is None:
            continue
        if datetime.now() - last_reading_time > timedelta(seconds=STALE_AFTER_SECONDS):
            await broadcast_stale()


# --------------------------------------------------------------------------
# File watcher
# --------------------------------------------------------------------------

class SensorFileHandler(FileSystemEventHandler):
    """Runs on the watchdog thread; hands work to the asyncio loop."""

    def __init__(self, loop, queue):
        self.loop = loop
        self.queue = queue

    def _notify(self, path):
        if Path(path).name != SENSOR_FILE.name:
            return
        self.loop.call_soon_threadsafe(self.queue.put_nowait, True)

    def on_modified(self, event):
        if not event.is_directory:
            self._notify(event.src_path)

    def on_created(self, event):
        if not event.is_directory:
            self._notify(event.src_path)

    def on_moved(self, event):
        # serial_reader.py writes atomically, so the real signal is the rename.
        if not event.is_directory:
            self._notify(event.dest_path)


async def file_event_consumer(queue):
    while True:
        await queue.get()
        try:
            await process_sensor_file()
        except Exception as exc:  # never let one bad reading kill the consumer
            print(f"[{datetime.now().isoformat(timespec='milliseconds')}] "
                  f"ERROR processing reading: {exc}")


# --------------------------------------------------------------------------
# App lifecycle
# --------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app):
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    SENSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    observer = Observer()
    observer.schedule(SensorFileHandler(loop, queue), str(SENSOR_FILE.parent), recursive=False)
    observer.start()

    consumer = asyncio.create_task(file_event_consumer(queue))
    monitor = asyncio.create_task(staleness_monitor())

    print(f"Watching {SENSOR_FILE}")
    print(f"Logging to {LOG_FILE}")

    if SENSOR_FILE.exists():
        await process_sensor_file()  # pick up whatever is already there

    yield

    consumer.cancel()
    monitor.cancel()
    observer.stop()
    observer.join(timeout=5)


app = FastAPI(title="HemoGuard", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/latest")
async def latest():
    return latest_payload


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
