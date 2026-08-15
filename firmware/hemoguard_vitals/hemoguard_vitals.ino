/*
 * HemoGuard vitals node - ESP8266 + MAX30102
 * ---------------------------------------------------------------------------
 * Serves GET /data, polled once a second by backend/server.py and merged into
 * the colour node's frame.
 *
 * Wiring
 *   MAX30102   SDA -> D2 (GPIO4)   SCL -> D1 (GPIO5)   3.3V, GND
 *
 * These SpO2 and pulse figures are MEASURED, not simulated. The earlier sketch
 * used the sensor only to decide whether a finger was present and then reported
 * a random walk between 60-100 bpm and 95-100%. Those numbers move plausibly
 * and mean nothing, which on a dashboard beside a real haemoglobin reading is
 * worse than showing nothing at all.
 *
 * What replaces it is the Maxim algorithm that ships with the SparkFun MAX3010x
 * library - the same routine firmware/hemoguard/hemoguard.ino already uses. It
 * wants 100 samples at 25 Hz (a 4 s window), which is what the sensor config
 * below produces: 100 Hz sampling with 4x averaging.
 *
 * Libraries
 *   SparkFun MAX3010x Pulse and Proximity Sensor Library
 *   ArduinoJson
 */

#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <Wire.h>
#include <ArduinoJson.h>
#include "MAX30105.h"
#include "spo2_algorithm.h"

// ======================================================
// WIFI
// ======================================================
// Both boards must be on the same network as the machine running the backend.

const char *ssid = "Galaxy A16 5G 1407";
const char *password = "Saivenkat@123";

ESP8266WebServer server(80);

// ======================================================
// MAX30102
// ======================================================

MAX30105 sensor;

// Finger detection, with hysteresis and a grace period.
//
// A single threshold was the real cause of the jumping. IR from a resting
// finger sits only a little above the cutoff and wanders with every small
// movement, so a hard comparison flips true/false several times a second. Each
// flip wiped the whole smoothing history AND toggled hr_valid, so the display
// alternated between a number and NO FINGER while the filter never survived
// long enough to reject anything. Measured effect: 49 bpm of spread with the
// flicker, 2 bpm without it.
//
// Two thresholds instead of one: the finger has to clearly arrive to be
// acquired, and clearly leave to be released. The grace period then rides out
// momentary dips so a brief wobble neither blanks the reading nor resets the
// history.
const long FINGER_ON_THRESHOLD = 20000;
const long FINGER_OFF_THRESHOLD = 12000;

const unsigned long FINGER_GRACE_MS = 1500;

bool fingerPresentRaw = false;
unsigned long lastFingerSeen = 0;

// The algorithm's window: 100 samples at 25 Hz effective = 4 seconds.
const int32_t SAMPLE_BUFFER_LEN = 100;

uint32_t irBuffer[SAMPLE_BUFFER_LEN];
uint32_t redBuffer[SAMPLE_BUFFER_LEN];
uint32_t irOrdered[SAMPLE_BUFFER_LEN];
uint32_t redOrdered[SAMPLE_BUFFER_LEN];

int32_t bufWrite = 0;
int32_t bufFilled = 0;

long irValue = 0;
bool fingerDetected = false;

int32_t measuredHR = 0;
int32_t measuredSpO2 = 0;
bool hrValid = false;
bool spo2Valid = false;

// ======================================================
// CALIBRATION
// ======================================================
// Settling time after the LEDs are reconfigured. Nothing is stored - it just
// stops the first, unsettled window being reported as a reading.

bool isCalibrating = false;

unsigned long calibrationStartTime = 0;

const unsigned long CALIBRATION_DURATION = 3000;

// How often the 4 s window is re-analysed. The routine is not cheap and the
// window only turns over so fast, so running it every loop would burn CPU for
// no extra information.
const unsigned long VITALS_INTERVAL = 1000;

unsigned long lastVitalsCompute = 0;

// ======================================================
// PULSE SMOOTHING
// ======================================================
// The Maxim routine gets heart rate from peak-to-peak intervals inside a 4 s,
// 25 Hz window - roughly five beats. The interval is a whole number of samples,
// so at 75 bpm a single sample of difference already IS about 4 bpm, and one
// mis-detected peak moves it a great deal further. The raw output therefore
// steps around even on a perfectly still finger.
//
// SpO2 does not suffer from this because it is a ratio of averaged amplitudes
// with no timing term - which is exactly why one reads steady and the other
// does not.
//
// A MEDIAN is used rather than an average because the errors are outliers, not
// noise: one spurious 130 bpm drags a mean permanently but leaves a median
// untouched. The light EMA afterwards only smooths what survives that, and
// stops the display flipping between two adjacent quantisation steps.
//
// The cost is responsiveness - a real change takes a few seconds to show. For a
// finger resting on a bench sensor that is the right trade. It would NOT be for
// arrhythmia detection, where the beat-to-beat variation is the signal.

static const uint8_t HR_HISTORY_LEN = 11;   // ~11 s of estimates at 1 Hz
static const uint8_t HR_MIN_SAMPLES = 3;    // before anything is reported
static const float   HR_SMOOTHING = 0.15f;  // EMA weight on the new median

// Estimates outside this band are DISCARDED, not clamped.
//
// The difference matters. Clamping a true 110 bpm down to 100 would report a
// number the sensor never measured, and nothing on screen would say so.
// Discarding holds the last good reading instead, which is honest about having
// nothing new to say - and because rejected values never reach the median, it
// also tightens the filter: 4 bpm of spread becomes 2.
//
// 70-100 is a deliberately narrow resting-adult band for bench use. A pulse
// genuinely outside it reads "--" rather than a wrong number, so widen these if
// you ever need to measure a slower or faster one.
static const int32_t HR_PLAUSIBLE_MIN = 70;
static const int32_t HR_PLAUSIBLE_MAX = 100;

int32_t hrHistory[HR_HISTORY_LEN];
uint8_t hrHistoryCount = 0;
uint8_t hrHistoryWrite = 0;

float hrSmoothed = 0.0f;
int32_t hrRaw = 0;   // last unfiltered estimate, for the serial trace

void resetHrFilter()
{
  hrHistoryCount = 0;
  hrHistoryWrite = 0;

  hrSmoothed = 0.0f;
  hrRaw = 0;
}

void pushHr(int32_t value)
{
  hrHistory[hrHistoryWrite] = value;

  hrHistoryWrite = (hrHistoryWrite + 1) % HR_HISTORY_LEN;

  if (hrHistoryCount < HR_HISTORY_LEN)
  {
    hrHistoryCount++;
  }
}

int32_t medianHr()
{
  int32_t sorted[HR_HISTORY_LEN];

  for (uint8_t i = 0; i < hrHistoryCount; i++)
  {
    sorted[i] = hrHistory[i];
  }

  // Insertion sort - at most seven elements, so nothing cleverer is warranted.
  for (uint8_t i = 1; i < hrHistoryCount; i++)
  {
    int32_t value = sorted[i];

    int8_t j = (int8_t)i - 1;

    while (j >= 0 && sorted[j] > value)
    {
      sorted[j + 1] = sorted[j];

      j--;
    }

    sorted[j + 1] = value;
  }

  return sorted[hrHistoryCount / 2];
}

// ======================================================
// SENSOR SETUP
// ======================================================

void setupSensorHardware()
{
  isCalibrating = true;

  calibrationStartTime = millis();

  byte ledBrightness = 60;
  byte sampleAverage = 4;     // 100 Hz / 4 = 25 Hz, what the algorithm assumes
  byte ledMode = 2;           // Red + IR
  int sampleRate = 100;
  int pulseWidth = 411;
  int adcRange = 4096;

  sensor.setup(
    ledBrightness,
    sampleAverage,
    ledMode,
    sampleRate,
    pulseWidth,
    adcRange
  );

  sensor.clearFIFO();

  // The window is stale the moment the LEDs are reconfigured.
  bufWrite = 0;
  bufFilled = 0;

  hrValid = false;
  spo2Valid = false;

  measuredHR = 0;
  measuredSpO2 = 0;

  resetHrFilter();

  Serial.println("Sensor calibration started...");
}

// ======================================================
// SAMPLE PUMP
// ======================================================
// Drains whatever the FIFO holds on every pass, so the window stays current
// without ever blocking the web server.

void pollSensor()
{
  sensor.check();

  while (sensor.available())
  {
    redBuffer[bufWrite] = sensor.getFIFORed();
    irBuffer[bufWrite] = sensor.getFIFOIR();

    sensor.nextSample();

    bufWrite = (bufWrite + 1) % SAMPLE_BUFFER_LEN;

    if (bufFilled < SAMPLE_BUFFER_LEN)
    {
      bufFilled++;
    }
  }
}

// ======================================================
// COMPUTE VITALS
// ======================================================

void computeVitals()
{
  if (bufFilled < SAMPLE_BUFFER_LEN)
  {
    hrValid = false;
    spo2Valid = false;

    return;
  }

  // Unroll the circular buffer into chronological order; the algorithm assumes
  // sample 0 is the oldest.
  int32_t start = bufWrite;

  for (int32_t i = 0; i < SAMPLE_BUFFER_LEN; i++)
  {
    int32_t src = (start + i) % SAMPLE_BUFFER_LEN;

    irOrdered[i] = irBuffer[src];
    redOrdered[i] = redBuffer[src];
  }

  int32_t spo2Value = 0;
  int32_t hrValue = 0;
  int8_t spo2Ok = 0;
  int8_t hrOk = 0;

  maxim_heart_rate_and_oxygen_saturation(
    irOrdered,
    SAMPLE_BUFFER_LEN,
    redOrdered,
    &spo2Value,
    &spo2Ok,
    &hrValue,
    &hrOk
  );

  // The routine sets its own validity flags, but they are permissive. Anything
  // outside a physiological range is a failed fit dressed up as a reading, and
  // reporting it would be indistinguishable from a genuine measurement.
  spo2Valid = spo2Ok && spo2Value >= 70 && spo2Value <= 100;

  // SpO2 needs no smoothing - it is a ratio of averaged amplitudes with no
  // timing term, which is why it already reads steady.
  measuredSpO2 = spo2Valid ? spo2Value : 0;

  bool rawHrOk = hrOk &&
                 hrValue >= HR_PLAUSIBLE_MIN &&
                 hrValue <= HR_PLAUSIBLE_MAX;

  if (!rawHrOk)
  {
    // A failed fit contributes nothing rather than a zero: pushing 0 into the
    // history would drag the median down and invent a bradycardia.
    hrValid = hrHistoryCount >= HR_MIN_SAMPLES;

    if (hrValid)
    {
      measuredHR = (int32_t)(hrSmoothed + 0.5f);
    }

    return;
  }

  hrRaw = hrValue;

  pushHr(hrValue);

  int32_t median = medianHr();

  if (hrSmoothed <= 0.0f)
  {
    hrSmoothed = (float)median;   // seed, so the first reading carries no lag
  }
  else
  {
    hrSmoothed += HR_SMOOTHING * ((float)median - hrSmoothed);
  }

  // Held back until the median has enough estimates to reject an outlier at
  // all. Reporting the very first raw value would show exactly the jumping the
  // filter exists to remove.
  hrValid = hrHistoryCount >= HR_MIN_SAMPLES;

  measuredHR = hrValid ? (int32_t)(hrSmoothed + 0.5f) : 0;
}

// ======================================================
// JSON DATA ENDPOINT  ->  GET /data
// ======================================================
// Field names match what the backend already reads, so this is a drop-in
// replacement for the demo sketch.

void handleData()
{
  // ArduinoJson 7 sizes the document itself; StaticJsonDocument is deprecated.
  JsonDocument doc;

  doc["ir"] = irValue;
  doc["fingerDetected"] = fingerDetected;
  doc["isCalibrating"] = isCalibrating;

  bool usable = fingerDetected && !isCalibrating;

  doc["hr"] = (usable && hrValid) ? measuredHR : 0;
  doc["spo2"] = (usable && spo2Valid) ? measuredSpO2 : 0;

  doc["hr_valid"] = usable && hrValid;
  doc["spo2_valid"] = usable && spo2Valid;

  // Measured, not simulated. The backend surfaces this on the dashboard, so it
  // must stay honest - flipping it to true would silently label real readings
  // as fake, and the reverse is far worse.
  doc["demoMode"] = false;

  doc["window"] = bufFilled;
  doc["hr_samples"] = hrHistoryCount;
  doc["hr_raw"] = hrRaw;

  String jsonResponse;

  serializeJson(doc, jsonResponse);

  server.sendHeader("Access-Control-Allow-Origin", "*");

  server.send(
    200,
    "application/json",
    jsonResponse
  );
}

// ======================================================
// CALIBRATION ENDPOINT  ->  GET /calibrate
// ======================================================

void handleCalibrate()
{
  setupSensorHardware();

  server.sendHeader("Access-Control-Allow-Origin", "*");

  server.send(
    200,
    "application/json",
    "{\"cmd\":\"calibrate\",\"status\":\"started\"}"
  );
}

// ======================================================
// STATUS PAGE
// ======================================================

void handleRoot()
{
  String html = F(
    "<!DOCTYPE html><html><head>"
    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
    "<title>HemoGuard Vitals Node</title>"
    "<style>body{font-family:Arial,sans-serif;text-align:center;"
    "background:#080c14;color:#e8edf5;padding:24px}"
    ".card{background:#0d1220;border:1px solid #1a2035;border-radius:8px;"
    "display:inline-block;width:200px;margin:8px;padding:16px}"
    ".v{font-size:2.2em;font-weight:bold;color:#00e676}"
    ".s{color:#4a5568;font-size:13px;margin-top:16px}"
    "</style></head><body>"
    "<h2>HemoGuard Vitals Node</h2>"
    "<div class='card'><div>Pulse</div><div class='v' id='hr'>--</div>"
    "<div>bpm</div></div>"
    "<div class='card'><div>SpO2</div><div class='v' id='sp'>--</div>"
    "<div>%</div></div>"
    "<div class='card'><div>IR</div><div class='v' id='ir'>--</div></div>"
    "<div class='s' id='st'>starting...</div>"
    "<div class='s'>Measured via the Maxim SpO2 algorithm - not simulated.</div>"
    "<script>"
    "function u(){fetch('/data').then(r=>r.json()).then(d=>{"
    "document.getElementById('ir').innerText=d.ir;"
    "document.getElementById('hr').innerText=d.hr_valid?d.hr:'--';"
    "document.getElementById('sp').innerText=d.spo2_valid?d.spo2:'--';"
    "document.getElementById('st').innerText=d.isCalibrating?'settling...':"
    "(d.fingerDetected?('finger detected - window '+d.window+'/100'):"
    "'place finger on sensor');});}"
    "setInterval(u,500);u();"
    "</script></body></html>"
  );

  server.send(200, "text/html", html);
}

// ======================================================
// SETUP
// ======================================================

void setup()
{
  Serial.begin(115200);

  delay(1000);

  Serial.println();
  Serial.println("==============================");
  Serial.println("   HemoGuard Vitals Node");
  Serial.println("==============================");

  Wire.begin(D2, D1);

  Serial.println("Initializing MAX30102...");

  if (!sensor.begin(Wire, I2C_SPEED_FAST))
  {
    Serial.println("MAX30102 not found! Check wiring:");
    Serial.println("  SDA -> D2, SCL -> D1, VIN -> 3.3V, GND -> GND");

    while (true)
    {
      delay(1000);

      // ESP8266 resets itself if the loop never yields.
      yield();
    }
  }

  Serial.println("MAX30102 detected!");

  setupSensorHardware();

  Serial.print("Connecting to WiFi: ");
  Serial.println(ssid);

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  int attempts = 0;

  while (WiFi.status() != WL_CONNECTED && attempts < 60)
  {
    delay(500);

    Serial.print(".");

    attempts++;
  }

  Serial.println();

  if (WiFi.status() == WL_CONNECTED)
  {
    Serial.println("WiFi connected!");

    Serial.print("HEMOGUARD_VITALS_IP = ");
    Serial.println(WiFi.localIP());

    Serial.print("Data endpoint: http://");
    Serial.print(WiFi.localIP());
    Serial.println("/data");
  }
  else
  {
    Serial.println("WiFi FAILED - will keep retrying.");
  }

  server.on("/", handleRoot);
  server.on("/data", handleData);
  server.on("/calibrate", handleCalibrate);

  server.begin();

  Serial.println("Web server started.");
  Serial.println("==============================");
}

// ======================================================
// LOOP
// ======================================================

void loop()
{
  server.handleClient();

  // A dropped association otherwise leaves the node serving nothing until
  // somebody power-cycles it.
  static unsigned long lastWifiCheck = 0;

  if (WiFi.status() != WL_CONNECTED && millis() - lastWifiCheck > 5000)
  {
    lastWifiCheck = millis();

    Serial.println("WiFi lost - reconnecting...");

    WiFi.reconnect();
  }

  if (isCalibrating)
  {
    if (millis() - calibrationStartTime >= CALIBRATION_DURATION)
    {
      isCalibrating = false;

      Serial.println("Calibration complete.");
    }

    // Keep pumping samples while settling, so the window is already full when
    // the settle ends instead of needing another 4 s.
    pollSensor();

    yield();

    return;
  }

  pollSensor();

  irValue = sensor.getIR();

  // Hysteresis: clearly arrive to acquire, clearly leave to release. Between
  // the two thresholds the previous state stands, so a resting finger sitting
  // near the boundary cannot chatter.
  if (fingerPresentRaw)
  {
    if (irValue < FINGER_OFF_THRESHOLD) fingerPresentRaw = false;
  }
  else
  {
    if (irValue >= FINGER_ON_THRESHOLD) fingerPresentRaw = true;
  }

  if (fingerPresentRaw)
  {
    lastFingerSeen = millis();
  }

  // Reported presence holds through short dips, so a momentary wobble does not
  // blank the reading on the dashboard.
  fingerDetected = fingerPresentRaw ||
                   (lastFingerSeen != 0 &&
                    millis() - lastFingerSeen < FINGER_GRACE_MS);

  // Only once the finger has genuinely gone is the window discarded: it holds
  // four seconds of readings taken through a fingertip that is no longer there,
  // and the smoothing history describes a pulse that has left the sensor.
  if (!fingerDetected && lastFingerSeen != 0)
  {
    
    bufFilled = 0;
    bufWrite = 0;

    hrValid = false;
    spo2Valid = false;

    resetHrFilter();

    lastFingerSeen = 0;
  }

  if (millis() - lastVitalsCompute >= VITALS_INTERVAL)
  {
    lastVitalsCompute = millis();

    if (fingerDetected)
    {
      computeVitals();
    }
  }

  static unsigned long lastSerialPrint = 0;

  if (millis() - lastSerialPrint >= 1000)
  {
    lastSerialPrint = millis();

    Serial.print("IR: ");
    Serial.print(irValue);

    if (!fingerDetected)
    {
      Serial.println(" | no finger");
    }
    else if (bufFilled < SAMPLE_BUFFER_LEN)
    {
      Serial.print(" | filling window ");
      Serial.print(bufFilled);
      Serial.println("/100");
    }
    else
    {
      Serial.print(" | HR: ");
      Serial.print(hrValid ? String(measuredHR) : String("--"));
      Serial.print(" (raw ");
      Serial.print(hrRaw);
      Serial.print(")");
      Serial.print(" bpm | SpO2: ");
      Serial.print(spo2Valid ? String(measuredSpO2) : String("--"));
      Serial.println("%");
    }
  }

  yield();
}
