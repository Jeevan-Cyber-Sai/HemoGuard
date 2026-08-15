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
import json
import math
import os
import random
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
WEIGH_URL = f"http://{ESP32_IP}/weigh" if ESP32_IP else ""
DRY_PAD_URL = f"http://{ESP32_IP}/dry_pad" if ESP32_IP else ""
WEIGHT_RESET_URL = f"http://{ESP32_IP}/weight_reset" if ESP32_IP else ""
WEIGHT_CAL_URL = f"http://{ESP32_IP}/weight_calibrate" if ESP32_IP else ""
SCALE_URL = f"http://{ESP32_IP}/scale" if ESP32_IP else ""

# Weighing a pad averages ten HX711 conversions at 10 SPS, so the node blocks
# for about a second before it answers.
WEIGH_TIMEOUT_SECONDS = 8.0

# Demo weighing, for showing the system when the load cell is not usable.
#
#     set HEMOGUARD_WEIGHT_DEMO=1
#
# Each press produces a plausible soaked pad instead of reading the HX711. The
# figures are flagged weight_simulated so the dashboard marks them SIMULATED -
# an unlabelled invented number on a blood-loss card is indistinguishable from a
# measurement, and this is the one screen where that must never happen.
WEIGHT_DEMO = os.environ.get("HEMOGUARD_WEIGHT_DEMO", "").strip() not in ("", "0")

# Gross weight range a demo pad lands in, before the dry pad is subtracted.
# Read directly rather than through _threshold(), which is not defined yet here.
def _env_float(name, default):
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


WEIGHT_DEMO_MIN_G = _env_float("HEMOGUARD_WEIGHT_DEMO_MIN_G", 80.0)
WEIGHT_DEMO_MAX_G = _env_float("HEMOGUARD_WEIGHT_DEMO_MAX_G", 100.0)

# Continuous seepage between pads, g/s. Blood does not arrive only when someone
# presses a button, and a total frozen between presses reads as a dead display.
# 0.12 g/s is about 7 mL/min - visibly climbing without running away.
WEIGHT_DEMO_DRIFT_G_PER_S = _env_float("HEMOGUARD_WEIGHT_DEMO_DRIFT", 0.12)

# Optional second node: the ESP8266 carrying the MAX30102 pulse oximeter. It is
# a separate board on its own IP, so it gets its own poller and its readings are
# merged into the colour node's frame before scoring.
#
#     set HEMOGUARD_VITALS_IP=192.168.43.52
#
# Unset, the SpO2 and pulse channels simply stay unfitted, exactly as now.
VITALS_IP = os.environ.get("HEMOGUARD_VITALS_IP", "").strip()
VITALS_URL = f"http://{VITALS_IP}/data" if VITALS_IP else ""

POLL_INTERVAL_SECONDS = 1.0
POLL_TIMEOUT_SECONDS = 2.0   # must stay below the interval budget

# Vitals older than this are dropped rather than merged. The oximeter is on a
# different board from the colorimeter, so it can die on its own - and a frozen
# pulse silently riding along on a live colour frame is indistinguishable from a
# real one.
VITALS_MAX_AGE_SECONDS = 6.0

# How long the colour node must be silent before the vitals node starts
# publishing frames on its own. The two boards are independent, so one being
# absent must not blank the other - but while both are feeding, only the colour
# frames are broadcast, or every reading would go out twice.
COLOUR_QUIET_SECONDS = 3.0

# The node blocks for a 10 s sweep before it answers /calibrate, so the ceiling
# has to clear that with room for the round trip.
CALIBRATION_TIMEOUT_SECONDS = 15.0

# The node now acknowledges /calibrate immediately and runs the sweep in its own
# loop, so this request is short. Progress is followed on /cal_status.
CALIBRATION_ACK_TIMEOUT_SECONDS = 5.0

# How long to follow the sweep before giving up. Generously above the node's
# 10 s so a slow phase is never mistaken for a dead board.
CALIBRATION_WATCH_SECONDS = 30.0

# Consecutive unanswered status polls before declaring the node gone. A busy
# node drops the odd one; only sustained silence means it actually reset.
CALIBRATION_MAX_MISSED_POLLS = 6

# Connection refusals are retried; the node is often just finishing the poll it
# was already serving when the button was pressed.
CALIBRATE_ATTEMPTS = 3

# Seconds of live index averaged when setting the concentration reference. One
# frame carries the full per-frame noise straight into the slope.
REFERENCE_SAMPLE_SECONDS = 6.0

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
    # Vite dev server for frontend-react/
    "http://localhost:5173",
    "http://127.0.0.1:5173",
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

# How long a pad-interval rate stays meaningful. Pads are weighed minutes apart,
# so the figure has to survive between them - but not forever, or a rate from an
# hour ago would still be presented as current.
PAD_RATE_MAX_AGE_SECONDS = 1800.0

# 1 g of blood ~ 1 mL. Kept explicit so it can be tuned to 1.06 g/mL if needed.
GRAMS_PER_ML = 1.0

# --------------------------------------------------------------------------
# Scoring baselines and thresholds
# --------------------------------------------------------------------------
# Every one of these is overridable from the environment as "mean,sd", e.g.
#     set HEMOGUARD_BASELINE_HB=0.0,0.35
# because the optical channel in particular can only be scaled against the rig
# it is running on. tools/tune_baselines.py derives them from a recorded run.


def _baseline(name, mean, sd):
    raw = os.environ.get(f"HEMOGUARD_BASELINE_{name}", "").strip()
    if not raw:
        return (mean, sd)
    try:
        parsed_mean, parsed_sd = (float(part) for part in raw.split(","))
    except ValueError:
        print(f"BASELINE {name}: cannot parse {raw!r}, expected 'mean,sd' - "
              f"using default {mean},{sd}")
        return (mean, sd)
    if not parsed_sd > 0:
        # A zero or negative sd makes every z-score infinite or sign-flipped.
        print(f"BASELINE {name}: sd must be > 0, got {parsed_sd} - "
              f"using default {mean},{sd}")
        return (mean, sd)
    return (parsed_mean, parsed_sd)


def _threshold(name, default):
    raw = os.environ.get(f"HEMOGUARD_{name}", "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"{name}: cannot parse {raw!r} - using default {default}")
        return default


# Bleeding rate, mL/min. Anchored to post-operative drain figures rather than
# invented: a z of 1.0 lands at ~102 mL/hr, the usual "tell the surgeon" mark,
# and a z of 2.5 at ~210 mL/hr, which is a surgical emergency.
#
# The previous sd of 1.0 mL/min put those thresholds at 150 and 240 mL/hr but
# with so little headroom that a real bleed produced z_rate ~88, and the rate
# channel then supplied 98.8% of the total score - the other three sensors were
# arithmetically incapable of affecting the band.
BASELINE_RATE = _baseline("RATE", 0.5, 1.2)

# Haemoglobin index - differential absorbance, see RiskEngine.hb_index().
# Water reads 0.0 by construction, so the mean is 0.0 and the sd sets how much
# absorbance counts as one sigma. THIS ONE IS RIG-SPECIFIC: it depends on the
# path length, the cuvette and the LED brightness. Measure your own sample and
# set it with tools/tune_baselines.py.
BASELINE_HB = _baseline("HB", 0.0, 0.40)

# --------------------------------------------------------------------------
# Haemoglobin concentration calibration
# --------------------------------------------------------------------------
# hb_g_dl = slope * hb_index + intercept
#
# Beer-Lambert gives c = A / (eps * l), but neither term is knowable on this
# rig: the LED's peak wavelength is unspecified and eps swings by an order of
# magnitude across the green band, the TCS34725 filters are broad rather than
# narrow so no single eps applies, and the path length depends on the sample
# holder. The honest route to real units is the one every colorimeter uses -
# regress the measured index against samples of KNOWN concentration and keep
# the fit.
#
# Unset means unset. The dashboard then reports the relative index and says so,
# rather than printing a g/dL figure that was never measured against anything.
#
#     set HEMOGUARD_HB_CALIBRATION=16.12,0.05
#
# tools/fit_concentration.py produces that line from a dilution series.
def _hb_calibration():
    raw = os.environ.get("HEMOGUARD_HB_CALIBRATION", "").strip()
    if not raw:
        return None
    try:
        slope, intercept = (float(part) for part in raw.split(","))
    except ValueError:
        print(f"HB_CALIBRATION: cannot parse {raw!r}, expected "
              f"'slope,intercept' - reporting relative index instead")
        return None
    if slope <= 0:
        print(f"HB_CALIBRATION: slope must be > 0, got {slope} - "
              f"reporting relative index instead")
        return None
    return (slope, intercept)


HB_CALIBRATION = _hb_calibration()

# Where a reference taken from the dashboard is kept, so the scale survives a
# restart. A calibration you have to redo every time the server bounces is one
# that will be wrong half the time.
CALIBRATION_FILE = Path(os.environ.get(
    "HEMOGUARD_CALIBRATION_FILE", BASE_DIR / "calibration.json"))

# --------------------------------------------------------------------------
# Sample geometry - turns concentration into an amount
# --------------------------------------------------------------------------
# Concentration answers "how strong", not "how much". To report an amount the
# volume being looked at has to be known, and only the operator knows it.
#
#     set HEMOGUARD_SAMPLE_VOLUME_ML=3.0
#
# Left at 0 the amounts are simply not reported, rather than computed from a
# volume nobody supplied.
SAMPLE_VOLUME_ML = _threshold("SAMPLE_VOLUME_ML", 0.0)

# Haemoglobin of the undiluted blood the sample was made from. Used only to
# express the sample as an equivalent volume of whole blood. 14 g/dL is a
# typical adult figure; set it to the real value if you have one.
WHOLE_BLOOD_G_DL = _threshold("WHOLE_BLOOD_G_DL", 14.0)

BASELINE_PULSE = _baseline("PULSE", 75.0, 12.0)   # z=2.5 at 105 bpm
BASELINE_SPO2 = _baseline("SPO2", 98.0, 2.0)      # z=2.5 at 93%

WEIGHTS = {"rate": 0.40, "hb": 0.30, "pr": 0.20, "spo2": 0.10}

# Per-channel z ceiling. Past about 6 sigma the exact figure carries no further
# decision - it is already maximally abnormal - while an unbounded one lets a
# single channel swamp the fusion. A nudged drape yields z_rate near 150, at
# which point the "weighted" score is rate alone and the other three sensors are
# arithmetically incapable of changing the band. Clamping keeps every channel
# able to matter, and bounds z_risk to Z_CLAMP so the gauge has a real top end.
Z_CLAMP = _threshold("Z_CLAMP", 6.0)

AMBER_THRESHOLD = _threshold("AMBER_THRESHOLD", 1.0)
RED_THRESHOLD = _threshold("RED_THRESHOLD", 2.5)
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
    "clear", "led", "bleeding_rate", "hb_index", "hb_mode", "hb_g_dl", "blood_ml", "hb_mass_mg", "vitals_simulated", "z_hb", "z_risk",
    "triage", "valid",
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
    "cal_red", "cal_green", "cal_ir",
    "pad_count", "last_pad_g", "dry_pad_g", "scale_ready", "weight_simulated",
    "live_gross_g", "live_net_g",
    "finger", "vitals_ir", "vitals_simulated",
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
        self.last_hb = None          # (value, mode) of the last usable hb index
        self.last_pad_count = -1     # pad weighings seen, for the interval rate
        self.last_pad_at = None
        self.pad_rate_value = None
        self.phases_seen = set()     # LEDs actually lit since the last baseline
        self.calibrated_seen = False # so a new baseline can reset the above
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

    def pad_rate(self, reading, moment):
        """mL/min from discrete pad weighings, or None when it cannot be known.

        The first pad has no preceding interval to divide by, so it yields no
        rate at all rather than a guess. After that the figure holds until the
        next pad - it is the best available estimate - but only up to
        PAD_RATE_MAX_AGE_SECONDS. Past that nobody has weighed anything for a
        long time and the old number has stopped describing the present, which
        is exactly the latched-reading failure this system keeps having to
        avoid.
        """
        count = self._as_float(reading.get("pad_count"))
        if count is None:
            return None
        count = int(count)

        if count < self.last_pad_count:
            # The counter went backwards, which only happens when the total is
            # reset for a new patient. Everything the old rate described belongs
            # to the previous one, so it is discarded rather than carried over.
            self.last_pad_count = count
            self.last_pad_at = None
            self.pad_rate_value = None
            return None

        if count != self.last_pad_count:
            # A rate needs TWO pads: the blood in this one, over the time since
            # the previous one was weighed. On the very first pad the only
            # elapsed time available is however long the backend happened to
            # have been running, which is not a collection interval at all - it
            # produced a spurious 295 mL/min from a 25 g pad.
            if self.last_pad_at is not None and self.last_pad_count >= 1:
                elapsed_min = (moment - self.last_pad_at).total_seconds() / 60.0
                grams = self._as_float(reading.get("last_pad_g")) or 0.0
                if elapsed_min > 0:
                    self.pad_rate_value = (grams / elapsed_min) / GRAMS_PER_ML
            self.last_pad_count = count
            self.last_pad_at = moment

        if self.last_pad_at is None:
            return None
        if (moment - self.last_pad_at).total_seconds() > PAD_RATE_MAX_AGE_SECONDS:
            return None
        return self.pad_rate_value

    def hb_index(self, reading):
        """Haemoglobin index from the raw sensor counts. Returns (value, mode).

        Preferred mode, "absorbance" - needs a water baseline:

            A_green = log10(I0_green / I_green)   strong Hb absorption
            A_red   = log10(I0_red   / I_red)     weak Hb absorption
            index   = A_green - A_red

        Haemoglobin absorbs around 150x more strongly at 542-577 nm than at
        660 nm, so the green term carries the signal and the red term carries
        almost none of it. Both terms contain the SAME wavelength-independent
        losses - scattering, turbidity, cuvette reflections, LED ageing - so
        subtracting cancels them and what survives is c*l*(eps_g - eps_r):
        directly proportional to haemoglobin concentration, which is what
        Beer-Lambert actually licenses us to claim.

        Fallback mode, "chromaticity" - no baseline yet:

            index = red / (red + green + blue)

        Measured under the RED phase only, and a far weaker signal: it is a
        colour ratio, not a concentration, and under red illumination the
        return is red whatever the sample is. It exists so the channel is not
        simply dead before calibration, not because it is trustworthy.

        The previous formula was red/(red+green+blue+clear), which had two
        defects at once. It summed C - the unfiltered photodiode that measures
        TOTAL light - alongside R/G/B as though it were a fourth colour, and it
        was read under the red LED where blood and a red plastic card differ by
        about 2%.
        """
        # A frame with no LED carries no optical data at all - that is a
        # vitals-only frame, published while the colour node is absent. Holding
        # the last haemoglobin reading across it would keep scoring a sample
        # nothing is currently looking at, which is the same latched-data
        # failure the triage band already guards against.
        if reading.get("led") is None:
            self.last_hb = None
            self.phases_seen.clear()
            return None, None

        calibrated = bool(reading.get("calibrated"))
        led = str(reading.get("led", "")).upper()

        # The node holds absPhase[] at 0.0 until each LED has been lit once, so
        # for the first seconds after boot or calibration abs_green is 0 simply
        # because green has not come round yet. Scored literally that reads as
        # "no haemoglobin", which is a false negative on a bleeding monitor -
        # indistinguishable from a clean drape. Wait until both wavelengths the
        # differential needs have actually been measured.
        if calibrated and not self.calibrated_seen:
            self.phases_seen.clear()          # baseline replaced; start again
        self.calibrated_seen = calibrated
        if led:
            self.phases_seen.add(led)

        if calibrated:
            a_green = self._as_float(reading.get("abs_green"))
            a_red = self._as_float(reading.get("abs_red"))
            measured = {"RED", "GREEN"} <= self.phases_seen
            if a_green is not None and a_red is not None and measured:
                # Floored at zero: a negative differential means green came back
                # brighter than the water reference, which is a stale baseline
                # rather than negative haemoglobin.
                return max(0.0, a_green - a_red), "absorbance"
            return None, None

        if led == RED_PHASE_LED:
            red = self._as_float(reading.get("red")) or 0.0
            green = self._as_float(reading.get("green")) or 0.0
            blue = self._as_float(reading.get("blue")) or 0.0
            total = red + green + blue
            if total > 0:
                return red / total, "chromaticity"

        return None, None

    @staticmethod
    def _as_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

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
            return {"bleeding_rate": None, "hb_index": None, "hb_mode": None,
                    "hb_g_dl": None, "blood_ml": None, "hb_mass_mg": None,
                    "z_rate": 0.0, "z_hb": 0.0, "z_pr": 0.0, "z_spo2": 0.0,
                    "z_risk": None, "triage": "unknown", "scored_channels": []}
        return dict(self.last_scored)

    def score(self, reading, moment):
        # Absent weight must not reach the rolling window: appending a
        # placeholder would fabricate a slope out of nothing, and clearing the
        # window would discard history a load cell may still be feeding.
        weight = self._optional(reading, "weight", zero_is_absent=False)

        # Two different weight sources need two different rate calculations.
        #
        # A drape sitting on the cell rises continuously, so a least-squares
        # slope over the rolling window is the rate. Pads weighed one at a time
        # give a CUMULATIVE total that is flat and then steps - a slope across
        # that step reports the whole pad as if it were shed in five seconds,
        # which would alarm on every weighing.
        #
        # For pads the honest figure is the pad's blood divided by the interval
        # since the previous pad: the true average rate over the period that pad
        # was collecting.
        if reading.get("pad_count") is not None:
            rate = self.pad_rate(reading, moment)
        else:
            rate = self.bleeding_rate(weight, moment) if weight is not None else None

        # The absorbance form is available on every frame - the node holds all
        # three phase absorbances and refreshes whichever LED is lit - so the
        # index no longer waits for the red phase to come round. The last value
        # is held only for the chromaticity fallback, which genuinely is
        # red-phase-only; without that hold z_risk would oscillate every cycle
        # as the LED rotates and flap the triage band.
        hb_value, hb_mode = self.hb_index(reading)
        if hb_value is not None:
            self.last_hb = (hb_value, hb_mode)

        if self.last_hb is None:
            hb_index, hb_mode = None, None
        else:
            hb_index, hb_mode = self.last_hb

        # Only the absorbance form is on a known scale. A chromaticity has no
        # baseline it can be measured against - under red light water reads
        # ~0.79 and blood ~0.81 - so scoring it would put a number in the
        # triage band that carries no information about haemoglobin. It is
        # reported for display and left out of the score.
        scored_hb = hb_mode == "absorbance"

        pulse = self._optional(reading, "pulse")
        spo2 = self._optional(reading, "spo2")

        def z(value, baseline, invert=False):
            """One-sided z-score, floored at 0 and capped at Z_CLAMP."""
            if value is None:
                return 0.0
            mean, sd = baseline
            raw = (mean - value) / sd if invert else (value - mean) / sd
            return min(Z_CLAMP, max(0.0, raw))

        z_rate = z(rate, BASELINE_RATE)
        z_hb = z(hb_index, BASELINE_HB) if scored_hb else 0.0
        z_pr = z(pulse, BASELINE_PULSE)
        z_spo2 = z(spo2, BASELINE_SPO2, invert=True)

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
            triage = self._triage(z_risk)
        else:
            # Nothing scoreable at all - an uncalibrated colour-only node is
            # exactly this. z_risk of 0 would paint a reassuring green LOW over
            # a system that has measured nothing, so the band says so instead.
            z_risk = None
            triage = "unknown"
            self.consecutive_critical = 0

        # Real units only once a reference of known concentration has been
        # measured. Floored at zero: a negative fitted concentration is an
        # extrapolation below the calibrated range, not a measurement.
        hb_g_dl = None
        blood_ml = None
        hb_mass_mg = None

        calibration = active_hb_calibration()
        if scored_hb and calibration is not None:
            slope, intercept = calibration
            hb_g_dl = max(0.0, slope * hb_index + intercept)

            # An amount needs a volume, and only the operator knows it.
            if SAMPLE_VOLUME_ML > 0:
                # c is g per 100 mL, so mass_g = c * V / 100; reported in mg.
                hb_mass_mg = hb_g_dl * SAMPLE_VOLUME_ML / 100.0 * 1000.0
                # How much undiluted blood the sample is equivalent to.
                #
                # Capped at the sample volume, because there cannot be more
                # blood in the cuvette than the cuvette holds. Above the cap the
                # sample is denser than the WHOLE_BLOOD_G_DL reference, which
                # means it is not a dilution of that blood at all and the model
                # no longer applies - reporting 4.3 mL in a 3 mL cuvette would
                # be arithmetic pretending to be a measurement.
                if WHOLE_BLOOD_G_DL > 0:
                    blood_ml = min(
                        SAMPLE_VOLUME_ML,
                        (hb_g_dl / WHOLE_BLOOD_G_DL) * SAMPLE_VOLUME_ML,
                    )

        self.last_scored = {
            "bleeding_rate": None if rate is None else round(rate, 3),
            "hb_index": None if hb_index is None else round(hb_index, 4),
            "hb_mode": hb_mode,
            "hb_g_dl": None if hb_g_dl is None else round(hb_g_dl, 2),
            "blood_ml": None if blood_ml is None else round(blood_ml, 3),
            "hb_mass_mg": None if hb_mass_mg is None else round(hb_mass_mg, 1),
            "z_rate": round(z_rate, 3),
            "z_hb": round(z_hb, 3),
            "z_pr": round(z_pr, 3),
            "z_spo2": round(z_spo2, 3),
            "z_risk": None if z_risk is None else round(z_risk, 3),
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
            payload.get("hb_index"),
            payload.get("hb_mode"),
            payload.get("hb_g_dl"),
            payload.get("blood_ml"),
            payload.get("hb_mass_mg"),
            payload.get("vitals_simulated"),
            payload.get("z_hb"),
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

# Last frame from the oximeter node, with the wall-clock time it arrived so a
# dead board's numbers can be aged out rather than merged forever.
latest_vitals = None
latest_vitals_at = None

# Demo weighing state, used only when WEIGHT_DEMO is on.
demo_total_g = 0.0
demo_last_pad_g = 0.0
demo_pad_count = 0
demo_dry_pad_g = 17.0
demo_drift_since = None   # when the current seepage interval started

# When the COLOUR node last produced a reading, kept apart from
# last_reading_time (which covers any broadcast, from either board). Without the
# distinction, a vitals-only frame would refresh the same clock it is tested
# against and the vitals node would immediately stop publishing.
last_colour_at = None

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
    global last_colour_at

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
    reading = merge_vitals(reading)
    reading = apply_weight_demo(reading)
    reading = dict(reading)
    reading["timestamp"] = moment.isoformat(timespec="milliseconds")

    payload = build_payload(reading, moment)
    latest_payload = payload
    last_reading_time = moment
    last_colour_at = moment
    currently_stale = False

    append_to_csv(payload)
    await manager.broadcast(payload)
    flag = "" if payload["valid"] else "  SENSOR_INVALID (scores held)"
    print(f"[{payload['timestamp']}] z_risk={payload['z_risk']} triage={payload['triage']} "
          f"rate={payload['bleeding_rate']} mL/min "
          f"hb={payload['hb_index']} ({payload['hb_mode']}){flag}")


def merge_vitals(reading):
    """Fold the oximeter node's numbers into the colour node's frame.

    Absent, invalid and stale all resolve to None rather than 0. The scoring
    engine reads 0 as a failure sentinel anyway, but None states the case
    plainly and keeps the two boards' failure modes separate: the colorimeter
    going quiet must not blank the pulse, and vice versa.
    """
    if latest_vitals is None or latest_vitals_at is None:
        return reading

    age = (datetime.now() - latest_vitals_at).total_seconds()
    if age > VITALS_MAX_AGE_SECONDS:
        return reading

    reading = dict(reading)
    reading["pulse"] = latest_vitals.get("pulse")
    reading["spo2"] = latest_vitals.get("spo2")
    reading["finger"] = latest_vitals.get("finger")
    reading["vitals_ir"] = latest_vitals.get("ir")

    # Carried through so the dashboard can label simulated numbers as such.
    # A demo-mode oximeter emits a plausible random walk, which is exactly the
    # kind of value that looks measured and is not.
    reading["vitals_simulated"] = latest_vitals.get("simulated", False)
    return reading


def demo_drift_g():
    """Blood accrued since the last pad was banked.

    Time-based rather than accumulated per tick, so the climb is the same
    whatever the publish cadence happens to be. The rate itself breathes on a
    slow sine so the line is not a dead straight ramp - but it never decreases,
    because blood already lost does not come back.
    """
    if demo_pad_count <= 0 or demo_drift_since is None:
        return 0.0
    elapsed = (datetime.now() - demo_drift_since).total_seconds()
    if elapsed <= 0:
        return 0.0
    breathing = 1.0 + 0.35 * math.sin(elapsed / 7.0)
    return WEIGHT_DEMO_DRIFT_G_PER_S * elapsed * breathing


def apply_weight_demo(reading):
    """Overlay demo pad figures, flagged so the dashboard can label them."""
    if not WEIGHT_DEMO:
        return reading
    reading = dict(reading)
    # Demo starts at a literal 0.0, not null. In demo the tray is understood to
    # be empty rather than unmeasured, so zero is the honest figure - and it
    # gives the operator something to watch climb as pads are added.
    reading["weight"] = round(demo_total_g + demo_drift_g(), 2)
    reading["pad_count"] = demo_pad_count
    reading["last_pad_g"] = demo_last_pad_g
    reading["dry_pad_g"] = demo_dry_pad_g
    reading["scale_ready"] = True
    reading["live_gross_g"] = round(demo_dry_pad_g + demo_drift_g(), 2)
    reading["live_net_g"] = round(demo_drift_g(), 2)
    reading["weight_simulated"] = True
    return reading


async def publish_vitals_only():
    """Broadcast a frame built from the oximeter alone.

    Used when the colour node is absent. The optical fields are left out
    entirely rather than zeroed, so the engine scores only what is actually
    being measured and the dashboard marks haemoglobin as unfitted.
    """
    global latest_payload, last_reading_time, currently_stale

    moment = datetime.now()
    reading = apply_weight_demo(merge_vitals({"weight": None, "valid": True}))
    reading["timestamp"] = moment.isoformat(timespec="milliseconds")

    payload = build_payload(reading, moment)
    latest_payload = payload
    last_reading_time = moment
    currently_stale = False

    append_to_csv(payload)
    await manager.broadcast(payload)


async def poll_vitals():
    """Fetch the oximeter node's GET /data once a second."""
    global latest_vitals, latest_vitals_at

    if not VITALS_URL:
        print("HEMOGUARD_VITALS_IP is not set - SpO2 and pulse stay unfitted.")
        return

    print(f"Polling vitals {VITALS_URL} every {POLL_INTERVAL_SECONDS:g}s")
    announced_failure = False
    warned_simulated = False

    limits = httpx.Limits(max_keepalive_connections=0)
    async with httpx.AsyncClient(timeout=POLL_TIMEOUT_SECONDS, limits=limits,
                                 headers={"Connection": "close"}) as client:
        while True:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            try:
                response = await client.get(VITALS_URL)
                response.raise_for_status()
                data = response.json()
            except Exception as exc:
                if not announced_failure:
                    print(f"[{datetime.now().isoformat(timespec='milliseconds')}] "
                          f"VITALS POLL FAILED: {type(exc).__name__}: {exc}")
                    announced_failure = True
                continue

            if announced_failure:
                print(f"[{datetime.now().isoformat(timespec='milliseconds')}] "
                      f"VITALS POLL RECOVERED")
                announced_failure = False

            if not isinstance(data, dict):
                continue

            # A reading only counts when a finger is present AND the node
            # flagged it valid. Without the finger the oximeter still returns
            # numbers; they just describe the tabletop.
            finger = bool(data.get("fingerDetected"))
            usable = finger and not data.get("isCalibrating")

            pulse = data.get("hr") if (usable and data.get("hr_valid")) else None
            spo2 = data.get("spo2") if (usable and data.get("spo2_valid")) else None

            simulated = bool(data.get("demoMode"))
            if simulated and not warned_simulated:
                print("VITALS: node reports demoMode - SpO2 and pulse are "
                      "SIMULATED, not measured. Flagged as such on the "
                      "dashboard.")
                warned_simulated = True

            latest_vitals = {
                "pulse": pulse,
                "spo2": spo2,
                "ir": data.get("ir"),
                "finger": finger,
                "simulated": simulated,
            }
            latest_vitals_at = datetime.now()

            # Drive the dashboard alone while the colour node is quiet. When it
            # is feeding, its frames already carry these vitals merged in, and
            # publishing here as well would double every point in the trends.
            colour_quiet = (
                last_colour_at is None
                or (datetime.now() - last_colour_at).total_seconds()
                > COLOUR_QUIET_SECONDS
            )
            if colour_quiet:
                try:
                    await publish_vitals_only()
                except Exception as exc:
                    print(f"[{datetime.now().isoformat(timespec='milliseconds')}] "
                          f"ERROR publishing vitals-only frame: {exc}")


async def demo_publisher():
    """Publish frames when demo weighing is on and no node is feeding.

    Without a colour or vitals node there is nothing generating frames at all,
    so the demo totals would never reach the dashboard. This fills that gap only
    - the moment real hardware starts feeding, its frames take over and this
    stands down.
    """
    if not WEIGHT_DEMO:
        return

    print("WEIGHT DEMO is ON - pad weights are generated, not measured.")

    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

        quiet = (
            last_colour_at is None
            or (datetime.now() - last_colour_at).total_seconds() > COLOUR_QUIET_SECONDS
        )
        vitals_quiet = (
            latest_vitals_at is None
            or (datetime.now() - latest_vitals_at).total_seconds()
            > VITALS_MAX_AGE_SECONDS
        )
        if not (quiet and vitals_quiet):
            continue

        try:
            await publish_vitals_only()
        except Exception as exc:
            print(f"[{datetime.now().isoformat(timespec='milliseconds')}] "
                  f"ERROR publishing demo frame: {exc}")


async def poll_sensor():
    """Fetch GET /sensor once a second for as long as the server runs."""
    if not SENSOR_URL:
        print("HEMOGUARD_ESP32_IP is not set - no sensor node to poll.")
        print("  set HEMOGUARD_ESP32_IP=192.168.1.45   (the IP the ESP32 prints on boot)")
        return

    print(f"Polling {SENSOR_URL} every {POLL_INTERVAL_SECONDS:g}s")
    announced_failure = False

    # No keep-alive. The ESP32's WebServer handles a single connection at a
    # time, so a pooled socket held open between polls occupies the node's only
    # slot and every other request - /calibrate above all - is refused outright
    # rather than queued. Closing after each poll leaves the slot free.
    limits = httpx.Limits(max_keepalive_connections=0)

    async with httpx.AsyncClient(timeout=POLL_TIMEOUT_SECONDS, limits=limits,
                                 headers={"Connection": "close"}) as client:
        while True:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

            # Stand off entirely while the node is calibrating: it cannot answer
            # /sensor during the sweep, and competing for its one socket is what
            # makes the calibration itself fail to connect.
            if calibrating:
                continue

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

    # The oximeter is a separate board. While it is feeding, the dashboard is
    # live - the colour node being down shows up as haemoglobin going unfitted,
    # not as the whole feed going stale.
    if latest_vitals_at is not None and             (datetime.now() - latest_vitals_at).total_seconds() <= VITALS_MAX_AGE_SECONDS:
        return

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
    load_saved_calibration()

    poller = asyncio.create_task(poll_sensor())
    vitals = asyncio.create_task(poll_vitals())
    demo = asyncio.create_task(demo_publisher())
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
    vitals.cancel()
    demo.cancel()
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


# --------------------------------------------------------------------------
# Concentration reference
# --------------------------------------------------------------------------
# Beer-Lambert is linear THROUGH THE ORIGIN: zero concentration absorbs nothing,
# and the water calibration has already pinned that end. So one sample of known
# concentration fixes the whole scale - slope = known_g_dl / measured_index -
# and a full dilution series is only needed to prove the line is straight, which
# tools/fit_concentration.py still does.

saved_calibration = None   # {"slope", "reference_g_dl", "reference_index", ...}


def load_saved_calibration():
    global saved_calibration
    if not CALIBRATION_FILE.exists():
        return
    try:
        with open(CALIBRATION_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        slope = float(data["slope"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"Calibration file {CALIBRATION_FILE} unreadable ({exc}) - ignoring")
        return
    if not slope > 0:
        print(f"Calibration file has slope {slope}, which cannot be used - ignoring")
        return
    saved_calibration = data
    print(f"Loaded Hb reference: {slope:.4f} g/dL per index unit "
          f"(from {data.get('reference_g_dl')} g/dL sample)")


def store_calibration(data):
    global saved_calibration
    saved_calibration = data
    try:
        CALIBRATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CALIBRATION_FILE, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
    except OSError as exc:
        # The measurement still applies to this session; only persistence failed.
        print(f"Could not save calibration to {CALIBRATION_FILE}: {exc}")


def active_hb_calibration():
    """(slope, intercept), or None. The environment wins over the saved file so
    an explicit override is never silently replaced by an old bench reference."""
    if HB_CALIBRATION is not None:
        return HB_CALIBRATION
    if saved_calibration is not None:
        return (float(saved_calibration["slope"]),
                float(saved_calibration.get("intercept", 0.0)))
    return None


def describe_node_failure(exc, url):
    """Turn a transport exception into the thing the operator should go and do.

    The old wording blamed the sensor for every failure, which sent people to
    check wiring when the actual fault was that nothing on the network answered
    at that address. The distinction is in the exception type and it is worth
    surfacing: a refused connection and a silent one have completely different
    fixes.
    """
    if isinstance(exc, httpx.ConnectError):
        return (f"Cannot reach the sensor node at {url} - nothing answered. "
                f"Check the IP printed on the ESP32's serial monitor, and that "
                f"the board and this PC are on the same Wi-Fi.")
    if isinstance(exc, (httpx.ReadTimeout, httpx.ConnectTimeout,
                        httpx.PoolTimeout)):
        return (f"The node accepted the connection but did not finish "
                f"calibrating within {CALIBRATION_TIMEOUT_SECONDS:g}s. "
                f"Check the serial monitor for a reset.")
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 404:
            return ("The node has no /calibrate endpoint - it is running older "
                    "firmware. Re-flash firmware/hemoguard_color.")
        return f"The node rejected the calibration request (HTTP {code})."
    return f"Calibration failed: {type(exc).__name__}: {exc}"


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


@app.get("/calibration")
async def get_calibration():
    active = active_hb_calibration()
    return {
        "calibrated": active is not None,
        "slope": active[0] if active else None,
        "intercept": active[1] if active else None,
        "source": ("environment" if HB_CALIBRATION is not None
                   else "saved" if saved_calibration else None),
        "reference": saved_calibration,
        "sample_volume_ml": SAMPLE_VOLUME_ML or None,
        "whole_blood_g_dl": WHOLE_BLOOD_G_DL,
    }


@app.post("/reference")
async def set_reference(body: dict):
    """Scale the index to real units from one sample of known concentration.

    Averages the live index for a few seconds rather than trusting a single
    frame, because one reading carries the full per-frame noise straight into
    the slope and every later concentration inherits it.
    """
    try:
        known_g_dl = float(body.get("g_dl"))
    except (TypeError, ValueError):
        return {"status": "error", "message": "Send a numeric g_dl value."}

    if not known_g_dl > 0:
        return {"status": "error",
                "message": "Reference concentration must be greater than 0. "
                           "Water is already the zero point."}

    seconds = float(body.get("seconds") or REFERENCE_SAMPLE_SECONDS)
    seconds = max(2.0, min(30.0, seconds))

    await manager.broadcast({
        "type": "reference", "status": "started",
        "message": f"Measuring reference - hold the {known_g_dl:g} g/dL sample still",
    })

    samples = []
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.4)
        if latest_payload.get("status") != "live":
            continue
        if latest_payload.get("hb_mode") != "absorbance":
            continue
        value = latest_payload.get("hb_index")
        if isinstance(value, (int, float)):
            samples.append(float(value))

    if len(samples) < 3:
        detail = ("Not enough live readings. Calibrate the node against water "
                  "first, and check the feed is LIVE.")
        await manager.broadcast({"type": "reference", "status": "error",
                                 "message": detail})
        return {"status": "error", "message": detail}

    mean_index = sum(samples) / len(samples)
    if mean_index <= 0:
        detail = ("The sample absorbed no more than the water baseline, so it "
                  "cannot set a scale. Re-calibrate against water, then put the "
                  "sample back without moving the cuvette.")
        await manager.broadcast({"type": "reference", "status": "error",
                                 "message": detail})
        return {"status": "error", "message": detail}

    spread = max(samples) - min(samples)
    slope = known_g_dl / mean_index

    record = {
        "slope": round(slope, 6),
        "intercept": 0.0,          # Beer-Lambert is linear through the origin
        "reference_g_dl": known_g_dl,
        "reference_index": round(mean_index, 6),
        "samples": len(samples),
        "spread": round(spread, 6),
        "taken_at": datetime.now().isoformat(timespec="seconds"),
    }
    store_calibration(record)

    print(f"[{datetime.now().isoformat(timespec='milliseconds')}] "
          f"REFERENCE set: {known_g_dl} g/dL at index {mean_index:.4f} "
          f"-> {slope:.4f} g/dL per unit (n={len(samples)}, spread={spread:.4f})")

    await manager.broadcast({
        "type": "reference", "status": "complete", "reference": record,
        "message": f"Reference set: {known_g_dl:g} g/dL at index {mean_index:.3f}",
    })
    return {"status": "complete", "reference": record}


@app.delete("/reference")
async def clear_reference():
    global saved_calibration
    saved_calibration = None
    try:
        CALIBRATION_FILE.unlink(missing_ok=True)
    except OSError as exc:
        print(f"Could not remove {CALIBRATION_FILE}: {exc}")
    await manager.broadcast({"type": "reference", "status": "cleared",
                             "message": "Concentration reference cleared"})
    return {"status": "cleared"}


async def _node_get(url, timeout, params=None):
    """One short request to the node, with the pooling turned off."""
    limits = httpx.Limits(max_keepalive_connections=0)
    async with httpx.AsyncClient(timeout=timeout, limits=limits,
                                 headers={"Connection": "close"}) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


@app.post("/weigh")
async def weigh():
    """Weigh the pad currently on the tray and add its blood to the total."""
    global demo_total_g, demo_last_pad_g, demo_pad_count

    if WEIGHT_DEMO:
        global demo_drift_since
        gross = random.uniform(WEIGHT_DEMO_MIN_G, WEIGHT_DEMO_MAX_G)
        blood = max(0.0, gross - demo_dry_pad_g)
        # Bank the seepage accrued since the last pad before adding this one,
        # so the running total never jumps backwards when the clock resets.
        demo_total_g = round(demo_total_g + demo_drift_g() + blood, 2)
        demo_last_pad_g = round(blood, 2)
        demo_pad_count += 1
        demo_drift_since = datetime.now()
        result = {"status": "ok", "gross_g": round(gross, 2),
                  "pad_g": demo_last_pad_g, "total_g": demo_total_g,
                  "pad_count": demo_pad_count, "dry_pad_g": demo_dry_pad_g,
                  "simulated": True}
        await manager.broadcast({
            "type": "weigh", "status": "complete",
            "message": (f"SIMULATED pad {demo_pad_count}: {demo_last_pad_g:.2f} g "
                        f"· total {demo_total_g:.2f} g"),
            "result": result})
        return result

    if not WEIGH_URL:
        return {"status": "error",
                "message": "HEMOGUARD_ESP32_IP is not set - no load cell to read."}
    try:
        result = await _node_get(WEIGH_URL, WEIGH_TIMEOUT_SECONDS)
    except Exception as exc:
        detail = describe_node_failure(exc, WEIGH_URL)
        print(f"[{datetime.now().isoformat(timespec='milliseconds')}] "
              f"WEIGH FAILED: {type(exc).__name__}: {exc}")
        await manager.broadcast({"type": "weigh", "status": "error",
                                 "message": detail})
        return {"status": "error", "message": detail}

    pad = result.get("pad_g")
    total = result.get("total_g")
    print(f"[{datetime.now().isoformat(timespec='milliseconds')}] "
          f"PAD {result.get('pad_count')}: {pad} g blood, total {total} g")

    message = (f"Pad {result.get('pad_count')}: {pad:.2f} g blood · "
               f"total {total:.2f} g") if isinstance(pad, (int, float)) else \
              "Pad weighed"
    await manager.broadcast({"type": "weigh", "status": "complete",
                             "message": message, "result": result})
    return result


@app.post("/dry_pad")
async def set_dry_pad(body: dict):
    """Set the dry-pad offset subtracted from every future pad."""
    try:
        grams = float(body.get("g"))
    except (TypeError, ValueError):
        return {"status": "error", "message": "Send a numeric g value."}

    # Mirrors the node's own guard so a bad figure is refused before it reaches
    # the hardware: a negative offset would add phantom blood to every pad.
    if not (0.0 <= grams <= 500.0):
        return {"status": "error",
                "message": "Dry pad weight must be between 0 and 500 g."}

    if WEIGHT_DEMO:
        global demo_dry_pad_g
        demo_dry_pad_g = grams
        await manager.broadcast({"type": "weigh", "status": "complete",
                                 "message": f"Dry pad weight set to {grams:g} g"})
        return {"status": "ok", "dry_pad_g": grams, "simulated": True}

    if not DRY_PAD_URL:
        return {"status": "error",
                "message": "HEMOGUARD_ESP32_IP is not set - no load cell."}
    try:
        result = await _node_get(DRY_PAD_URL, POLL_TIMEOUT_SECONDS,
                                 params={"g": f"{grams:.2f}"})
    except Exception as exc:
        detail = describe_node_failure(exc, DRY_PAD_URL)
        return {"status": "error", "message": detail}

    await manager.broadcast({"type": "weigh", "status": "complete",
                             "message": f"Dry pad weight set to {grams:g} g",
                             "result": result})
    return result


@app.post("/weight_reset")
async def weight_reset():
    """Clear the accumulated blood total, e.g. between patients."""
    if WEIGHT_DEMO:
        global demo_total_g, demo_last_pad_g, demo_pad_count, demo_drift_since
        demo_total_g = 0.0
        demo_last_pad_g = 0.0
        demo_pad_count = 0
        demo_drift_since = None
        await manager.broadcast({"type": "weigh", "status": "complete",
                                 "message": "Blood total reset to 0 g"})
        return {"status": "ok", "total_g": 0.0, "pad_count": 0}

    if not WEIGHT_RESET_URL:
        return {"status": "error",
                "message": "HEMOGUARD_ESP32_IP is not set - no load cell."}
    try:
        result = await _node_get(WEIGHT_RESET_URL, POLL_TIMEOUT_SECONDS)
    except Exception as exc:
        return {"status": "error",
                "message": describe_node_failure(exc, WEIGHT_RESET_URL)}

    await manager.broadcast({"type": "weigh", "status": "complete",
                             "message": "Blood total reset to 0 g",
                             "result": result})
    return result


@app.post("/weight_calibrate")
async def weight_calibrate():
    """Start the load-cell calibration. The node answers immediately and then
    runs the ~16 s routine, prompting on its serial monitor."""
    if not WEIGHT_CAL_URL:
        return {"status": "error",
                "message": "HEMOGUARD_ESP32_IP is not set - no load cell."}
    try:
        result = await _node_get(WEIGHT_CAL_URL, POLL_TIMEOUT_SECONDS)
    except Exception as exc:
        return {"status": "error",
                "message": describe_node_failure(exc, WEIGHT_CAL_URL)}

    await manager.broadcast({
        "type": "weigh", "status": "complete",
        "message": "Scale calibration started - follow the serial prompts (~16 s)",
        "result": result,
    })
    return result


@app.get("/scale")
async def scale_reading():
    """Live gross and net from the load cell, without touching the total.

    This is the check for "the scale reads wrong": getWeight() returns NET, so a
    100 g object on a bare tray shows 83 g with a 17 g pad offset. Gross is what
    proves the calibration itself.
    """
    if not SCALE_URL:
        return {"status": "error",
                "message": "HEMOGUARD_ESP32_IP is not set - no load cell."}
    try:
        return await _node_get(SCALE_URL, WEIGH_TIMEOUT_SECONDS)
    except Exception as exc:
        return {"status": "error",
                "message": describe_node_failure(exc, SCALE_URL)}


@app.get("/diag")
async def diag():
    """One call that says which link in the chain is broken.

    Reachability, the calibrate route and the baseline are three separate
    failures with three different fixes, and guessing between them from a single
    error string wastes far more time than asking the node directly.
    """
    report = {
        "colour_node_ip": ESP32_IP or None,
        "vitals_node_ip": VITALS_IP or None,
        "last_reading": last_reading_time.isoformat() if last_reading_time else None,
        "last_vitals": latest_vitals_at.isoformat() if latest_vitals_at else None,
        "checks": [],
        "verdict": [],
    }

    targets = []
    if ESP32_IP:
        targets += [("colour", "sensor", SENSOR_URL),
                    ("colour", "cal_status", CAL_STATUS_URL)]
    if VITALS_IP:
        targets += [("vitals", "data", VITALS_URL)]

    limits = httpx.Limits(max_keepalive_connections=0)
    async with httpx.AsyncClient(timeout=3.0, limits=limits,
                                 headers={"Connection": "close"}) as client:
        for node, name, url in targets:
            entry = {"node": node, "endpoint": name, "url": url}
            try:
                response = await client.get(url)
                entry["ok"] = response.is_success
                entry["http_status"] = response.status_code
                # The vitals node advertises whether its numbers are measured.
                if node == "vitals" and response.is_success:
                    body = response.json()
                    entry["demo_mode"] = bool(body.get("demoMode"))
                    entry["finger"] = bool(body.get("fingerDetected"))
            except Exception as exc:
                entry["ok"] = False
                entry["error"] = type(exc).__name__
                entry["detail"] = str(exc)
            report["checks"].append(entry)

    def verdict_for(node, label, env_name):
        rows = [c for c in report["checks"] if c["node"] == node]
        if not rows:
            report["verdict"].append(
                f"{label}: {env_name} is not set in the window running uvicorn, "
                f"so this node is not being polled at all.")
            return
        if not any(r.get("ok") for r in rows):
            report["verdict"].append(
                f"{label}: not reachable at all. The IP is stale, the board is "
                f"off, or it is on a DIFFERENT Wi-Fi network from this PC.")
        elif not all(r.get("ok") for r in rows):
            report["verdict"].append(
                f"{label}: answers on some endpoints only - likely older "
                f"firmware. Re-flash it.")
        else:
            report["verdict"].append(f"{label}: reachable, all endpoints OK.")

    verdict_for("colour", "Colour node (haemoglobin)", "HEMOGUARD_ESP32_IP")
    verdict_for("vitals", "Vitals node (SpO2/pulse)", "HEMOGUARD_VITALS_IP")

    # Both boards have to sit on the same network as this PC. Two nodes on
    # different SSIDs is the single most common cause of "one works, one does
    # not", and comparing their subnets catches it immediately.
    def subnet(ip):
        host = (ip or "").split(":")[0]
        parts = host.split(".")
        return ".".join(parts[:3]) if len(parts) == 4 else None

    a, b = subnet(ESP32_IP), subnet(VITALS_IP)
    if a and b and a != b:
        report["verdict"].append(
            f"WARNING: the two nodes are on different subnets ({a}.x and "
            f"{b}.x). They are almost certainly joined to different Wi-Fi "
            f"networks - check the SSID in each board's sketch.")

    vitals_demo = [c for c in report["checks"]
                   if c["node"] == "vitals" and c.get("demo_mode")]
    if vitals_demo:
        report["verdict"].append(
            "NOTE: the vitals node reports demoMode - its SpO2 and pulse are "
            "simulated random values, not measurements. Flash "
            "firmware/hemoguard_vitals to measure them.")

    return report


@app.post("/calibrate")
async def calibrate():
    """Run the node's water-baseline sweep, narrating it to every dashboard.

    Progress goes out over the WebSocket rather than only to the caller, so a
    second screen watching the same bed sees the calibration happen instead of
    silently reading absorbances from a baseline it never saw taken.
    """
    global calibrating, last_reading_time

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

    # Let the in-flight poll finish and its socket close, so the node has a free
    # connection slot before we ask for one.
    await asyncio.sleep(POLL_TIMEOUT_SECONDS + 0.3)

    # The node acknowledges immediately and runs the sweep in its own loop, so
    # this is a short request followed by polling - never one long request that
    # a single timeout can lose. A slow phase and a board that reset are now
    # distinguishable: the first keeps reporting progress, the second stops
    # answering.
    limits = httpx.Limits(max_keepalive_connections=0)
    last_error = None
    baseline = None

    try:
        async with httpx.AsyncClient(timeout=CALIBRATION_ACK_TIMEOUT_SECONDS,
                                     limits=limits,
                                     headers={"Connection": "close"}) as client:
            ack = None
            for attempt in range(1, CALIBRATE_ATTEMPTS + 1):
                try:
                    response = await client.get(CALIBRATE_URL)
                    response.raise_for_status()
                    ack = response.json()
                    break
                except httpx.ConnectError as exc:
                    # Nothing accepted the connection - worth retrying, the node
                    # may still have been finishing the poll it was serving.
                    last_error = exc
                    print(f"[{datetime.now().isoformat(timespec='milliseconds')}] "
                          f"CALIBRATE connect attempt {attempt}/{CALIBRATE_ATTEMPTS} "
                          f"failed: {exc}")
                    if attempt < CALIBRATE_ATTEMPTS:
                        await asyncio.sleep(1.5)
                except Exception as exc:
                    last_error = exc
                    break

            if ack is None:
                detail = describe_node_failure(last_error, CALIBRATE_URL)
                print(f"[{datetime.now().isoformat(timespec='milliseconds')}] "
                      f"CALIBRATE FAILED: {type(last_error).__name__}: {last_error}")
                await manager.broadcast({"type": "calibration", "status": "error",
                                         "message": detail})
                return {"status": "error", "message": detail}

            # Follow the sweep to completion.
            deadline = asyncio.get_running_loop().time() + CALIBRATION_WATCH_SECONDS
            missed = 0
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(1.0)
                try:
                    status = (await client.get(CAL_STATUS_URL)).json()
                    missed = 0
                except Exception as exc:
                    # A few dropped status polls are normal on a busy node; only
                    # a sustained silence means it has actually gone away.
                    last_error = exc
                    missed += 1
                    if missed >= CALIBRATION_MAX_MISSED_POLLS:
                        detail = ("Lost contact with the node during calibration "
                                  "- check the serial monitor for a reset.")
                        await manager.broadcast({"type": "calibration",
                                                 "status": "error",
                                                 "message": detail})
                        return {"status": "error", "message": detail}
                    continue

                if status.get("calibrating"):
                    continue

                if status.get("calibrated") and status.get("baseline"):
                    baseline = status["baseline"]
                break
    finally:
        # Released before the broadcast so the next poll is free to resume, and
        # in `finally` so a failed sweep cannot wedge staleness off for good.
        #
        # The staleness clock is restarted at the same moment. It kept running
        # while the poller stood off, so without this the monitor sees a reading
        # ~13 s old the instant the flag drops and flashes STALE for the second
        # before the next poll lands. Answering /cal_status is itself proof the
        # node was alive just now, so the reset states a fact.
        calibrating = False
        last_reading_time = datetime.now()

    if not baseline:
        detail = ("Calibration finished without a usable water baseline - the "
                  "RED or GREEN phase returned no light. Those two carry the "
                  "haemoglobin measurement; IR is optional. Check both LEDs "
                  "light and the sensor is against the sample.")
        await manager.broadcast({"type": "calibration", "status": "error",
                                 "message": detail})
        return {"status": "error", "message": detail}

    result = {"cmd": "calibrate", "status": "complete", "baseline": baseline,
              "message": "Water baseline stored"}

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
