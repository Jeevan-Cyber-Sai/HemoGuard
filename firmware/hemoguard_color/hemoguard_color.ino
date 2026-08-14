#include <math.h>
#include <Wire.h>
#include <WiFi.h>
#include <WebServer.h>
#include "Adafruit_TCS34725.h"
#include "secrets.h"

#define TCS_SDA 18
#define TCS_SCL 19

#define RED_LED   26
#define GREEN_LED 25
#define IR_LED    27

static const unsigned long LED_DWELL_MS = 1000;   // averaged across, not idled
static const unsigned long WIFI_CHECK_MS = 5000;

static const float BL_EPSILON = 1.0f;
static const float BL_PATH_LENGTH = 1.0f;

static const unsigned long CAL_DURATION_MS = 10000;
static const unsigned long CAL_PHASE_MS = CAL_DURATION_MS / 3;
static const uint8_t CAL_SAMPLES = 15;

Adafruit_TCS34725 tcs(
  TCS34725_INTEGRATIONTIME_50MS,
  TCS34725_GAIN_4X
);

bool haveColorimeter = false;

// Re-initialisation backoff for a colorimeter believed absent. Long enough that
// a genuinely missing sensor cannot stall the phase cycle with begin() calls.
static const unsigned long COLOR_RETRY_MS = 2000;

unsigned long lastColorRetryMs = 0;

WebServer server(80);

unsigned long lastWifiCheck = 0;

uint16_t rawR = 0;
uint16_t rawG = 0;
uint16_t rawB = 0;
uint16_t rawC = 0;

int displayR = 0;
int displayG = 0;
int displayB = 0;

String hexColor = "#000000";

static const char *LED_NAMES[3] = { "RED", "GREEN", "IR" };

uint8_t ledPhase = 0;

// Normal phase-cycle state. Declared with the other globals because setup()
// initialises them: the IDE generates function prototypes automatically, but
// never variable declarations, so anything used before its definition has to
// live up here.
//
// Readings are accumulated across the whole dwell and averaged at the end of
// it. Previously one conversion was taken at the top of each phase and the
// remaining ~95% of the dwell was spent idle, which threw away the only thing
// that time was good for. Absorbance is a logarithm of a ratio, so noise in the
// raw counts is amplified worst exactly where the green reading sits when blood
// is in the cuvette - averaging is worth more here than anywhere else.
unsigned long phaseStartMs = 0;
bool phaseSettled = false;

uint32_t phaseSumR = 0;
uint32_t phaseSumG = 0;
uint32_t phaseSumB = 0;
uint32_t phaseSumC = 0;

uint16_t phaseSamples = 0;

struct Baseline
{
  float R;
  float G;
  float B;
  float C;
  bool valid;
};

Baseline baseline[3] = {
  {0.0f, 0.0f, 0.0f, 0.0f, false},
  {0.0f, 0.0f, 0.0f, 0.0f, false},
  {0.0f, 0.0f, 0.0f, 0.0f, false},
};

bool calibrated = false;

float absPhase[3] = {0.0f, 0.0f, 0.0f};
float concPhase[3] = {0.0f, 0.0f, 0.0f};

uint16_t snapRawR = 0;
uint16_t snapRawG = 0;
uint16_t snapRawB = 0;
uint16_t snapRawC = 0;

int snapNormR = 0;
int snapNormG = 0;
int snapNormB = 0;

char snapHex[8] = "#000000";

uint8_t snapPhase = 0;

bool snapValid = false;

unsigned long snapUptime = 0;

float snapAbs = 0.0f;
float snapConc = 0.0f;

String rgbToHex(int r, int g, int b)
{
  char hex[8];

  sprintf(
    hex,
    "#%02X%02X%02X",
    r,
    g,
    b
  );

  return String(hex);
}

void blankColor()
{
  rawR = 0;
  rawG = 0;
  rawB = 0;
  rawC = 0;

  displayR = 0;
  displayG = 0;
  displayB = 0;

  hexColor = "#000000";
}

// Recovery for a sensor that was missing at boot or came loose mid-run.
//
// There is deliberately NO per-read bus probe here. Probing with a zero-length
// I2C write - beginTransmission() straight into endTransmission() - is not
// reliably supported on the ESP32 and can report a NACK for a device that is
// present and answering. Wired into the read path it declared the colorimeter
// absent on every call, which blanked every channel and left calibration with
// nothing to average. The sensor works; the probe was wrong.
//
// So: trust begin(), and only retry it when we already believe the sensor is
// gone, at a rate that cannot stall the phase cycle.
bool readColor()
{
  if (!haveColorimeter)
  {
    unsigned long now = millis();

    if (now - lastColorRetryMs < COLOR_RETRY_MS)
    {
      blankColor();

      return false;
    }

    lastColorRetryMs = now;

    haveColorimeter = tcs.begin();

    if (!haveColorimeter)
    {
      blankColor();

      return false;
    }

    Serial.println("TCS34725 reinitialised.");
  }

  tcs.getRawData(
    &rawR,
    &rawG,
    &rawB,
    &rawC
  );

  deriveDisplayFromRaw();

  return true;
}

// Display values follow from whatever is in rawR/G/B/C, so this is called both
// after a live read and after the phase average is written back into them.
void deriveDisplayFromRaw()
{
  if (rawC > 0)
  {
    float r = ((float)rawR / rawC) * 255.0f;
    float g = ((float)rawG / rawC) * 255.0f;
    float b = ((float)rawB / rawC) * 255.0f;

    displayR = constrain((int)r, 0, 255);
    displayG = constrain((int)g, 0, 255);
    displayB = constrain((int)b, 0, 255);
  }
  else
  {
    displayR = 0;
    displayG = 0;
    displayB = 0;
  }

  hexColor = rgbToHex(
    displayR,
    displayG,
    displayB
  );
}

float absorbanceFor(uint8_t phase, float intensity)
{
  if (!calibrated) return 0.0f;
  if (phase > 2) return 0.0f;
  if (!baseline[phase].valid) return 0.0f;

  float i0 = baseline[phase].C;

  if (!(i0 > 0.0f) || !(intensity > 0.0f)) return 0.0f;

  return log10f(i0 / intensity);
}

float concentrationFor(float absorbance)
{
  float denom = BL_EPSILON * BL_PATH_LENGTH;

  if (!(denom > 0.0f)) return 0.0f;

  return absorbance / denom;
}

// =====================================================
// LED SIGNAL
// =====================================================
// One LED at a time, never several together. Lighting all three at once draws
// a current spike on top of whatever the Wi-Fi radio is doing, and on a board
// powered over USB that is enough to brown out and reset the ESP32 - which
// looks exactly like a calibration that accepted the connection and then never
// answered. The blink pattern carries the meaning; the count does the work.
//
// Blinks sit either side of the measurement window, never inside it: stray
// light during a baseline read would corrupt the reference being captured.

void blinkSignal(uint8_t times, unsigned long onMs, unsigned long offMs)
{
  for (uint8_t i = 0; i < times; i++)
  {
    allLEDsOff();

    digitalWrite(RED_LED, HIGH);

    delay(onMs);

    allLEDsOff();

    delay(offMs);
  }
}

// =====================================================
// CALIBRATION  (non-blocking state machine)
// =====================================================
// Driven a slice at a time from loop() rather than run to completion inside the
// HTTP handler.
//
// Blocking for the full sweep meant the board went 11 s without servicing the
// network stack or answering anything, so a caller could only sit and hope. Any
// hiccup in that window - a reset, a dropped packet, a slow phase - surfaced as
// a read timeout with no way to tell what had actually happened. Here the
// request is acknowledged immediately and progress is published on /cal_status,
// so the sweep can take as long as it needs and the caller always knows where
// it got to.

bool calibrating = false;

uint8_t calPhase = 0;
uint8_t calSample = 0;
uint8_t calTaken = 0;
bool    calSettled = false;

unsigned long calPhaseStart = 0;

float calSumR = 0.0f;
float calSumG = 0.0f;
float calSumB = 0.0f;
float calSumC = 0.0f;

void resetCalPhaseAccumulators()
{
  calSumR = 0.0f;
  calSumG = 0.0f;
  calSumB = 0.0f;
  calSumC = 0.0f;

  calSample = 0;
  calTaken = 0;
  calSettled = false;

  calPhaseStart = millis();
}

void startCalibration()
{
  Serial.println("{\"cmd\":\"calibrate\",\"status\":\"started\"}");

  // Handshake: three fast flashes mean "command received, starting now". If you
  // press CALIBRATE and never see this, the request did not reach the board.
  blinkSignal(3, 100, 100);

  calibrated = false;

  for (uint8_t phase = 0; phase < 3; phase++)
  {
    baseline[phase].valid = false;
  }

  calPhase = 0;
  calibrating = true;

  applyLed(calPhase);

  resetCalPhaseAccumulators();
}

void finishCalibration()
{
  calibrating = false;

  calibrated =
    baseline[0].valid &&
    baseline[1].valid &&
    baseline[2].valid;

  for (uint8_t phase = 0; phase < 3; phase++)
  {
    absPhase[phase] = 0.0f;
    concPhase[phase] = 0.0f;
  }

  snapAbs = 0.0f;
  snapConc = 0.0f;

  // Result signal, readable off the board itself:
  //   two slow flashes  -> baseline stored
  //   six fast flashes  -> failed, no usable light on at least one phase
  if (calibrated)
  {
    blinkSignal(2, 250, 180);
  }
  else
  {
    blinkSignal(6, 70, 70);
  }

  Serial.printf(
    "{\"cmd\":\"calibrate\",\"status\":\"%s\"}\n",
    calibrated ? "complete" : "error"
  );

  applyLed(ledPhase);
}

void calibrationStep()
{
  // Discard the conversion straddling the LED switch - it integrated under the
  // previous LED.
  if (!calSettled)
  {
    readColor();

    calSettled = true;

    return;
  }

  unsigned long slotEnd =
    calPhaseStart + (CAL_PHASE_MS * (unsigned long)(calSample + 1)) / CAL_SAMPLES;

  if ((long)(millis() - slotEnd) < 0) return;

  if (readColor())
  {
    calSumR += (float)rawR;
    calSumG += (float)rawG;
    calSumB += (float)rawB;
    calSumC += (float)rawC;

    calTaken++;
  }

  calSample++;

  if (calSample < CAL_SAMPLES) return;

  if (calTaken > 0)
  {
    baseline[calPhase].R = calSumR / (float)calTaken;
    baseline[calPhase].G = calSumG / (float)calTaken;
    baseline[calPhase].B = calSumB / (float)calTaken;
    baseline[calPhase].C = calSumC / (float)calTaken;

    // A zero CLEAR average is an unusable reference: log10(0/I) is undefined,
    // and every absorbance drawn from it would be meaningless.
    baseline[calPhase].valid = (baseline[calPhase].C > 0.0f);
  }
  else
  {
    baseline[calPhase].valid = false;
  }

  calPhase++;

  if (calPhase >= 3)
  {
    finishCalibration();

    return;
  }

  applyLed(calPhase);

  resetCalPhaseAccumulators();
}

// 0.0 -> 1.0 across the whole sweep, so the caller can show real progress
// instead of guessing from a fixed countdown.
float calibrationProgress()
{
  if (!calibrating) return calibrated ? 1.0f : 0.0f;

  float perPhase = 1.0f / 3.0f;

  return ((float)calPhase + ((float)calSample / (float)CAL_SAMPLES)) * perPhase;
}

void capturePhaseSnapshot(bool ok)
{
  snapRawR = rawR;
  snapRawG = rawG;
  snapRawB = rawB;
  snapRawC = rawC;

  snapNormR = displayR;
  snapNormG = displayG;
  snapNormB = displayB;

  strncpy(
    snapHex,
    hexColor.c_str(),
    sizeof(snapHex) - 1
  );

  snapHex[sizeof(snapHex) - 1] = '\0';

  snapPhase = ledPhase;

  snapValid = ok;

  snapUptime = millis();

  if (ok)
  {
    float a = absorbanceFor(
      ledPhase,
      (float)rawC
    );

    absPhase[ledPhase] = a;
    concPhase[ledPhase] = concentrationFor(a);
  }

  snapAbs = absPhase[ledPhase];
  snapConc = concPhase[ledPhase];
}

void buildSnapshotJSON(char *out, size_t len)
{
  snprintf(
    out,
    len,
    "{\"uptime_ms\":%lu"
    ",\"weight\":null"
    ",\"spo2\":null"
    ",\"pulse\":null"
    ",\"red\":%u"
    ",\"green\":%u"
    ",\"blue\":%u"
    ",\"clear\":%u"
    ",\"norm_r\":%d"
    ",\"norm_g\":%d"
    ",\"norm_b\":%d"
    ",\"hex\":\"%s\""
    ",\"led\":\"%s\""
    ",\"valid\":%s"
    ",\"calibrated\":%s"
    ",\"absorbance\":%.4f"
    ",\"concentration\":%.4f"
    ",\"abs_red\":%.4f"
    ",\"abs_green\":%.4f"
    ",\"abs_ir\":%.4f"
    ",\"conc_red\":%.4f"
    ",\"conc_green\":%.4f"
    ",\"conc_ir\":%.4f}",
    snapUptime,
    snapRawR,
    snapRawG,
    snapRawB,
    snapRawC,
    snapNormR,
    snapNormG,
    snapNormB,
    snapHex,
    LED_NAMES[snapPhase],
    snapValid ? "true" : "false",
    calibrated ? "true" : "false",
    (double)snapAbs,
    (double)snapConc,
    (double)absPhase[0],
    (double)absPhase[1],
    (double)absPhase[2],
    (double)concPhase[0],
    (double)concPhase[1],
    (double)concPhase[2]
  );
}

void buildBaselineJSON(char *out, size_t len)
{
  if (!calibrated)
  {
    snprintf(out, len, "null");
    return;
  }

  snprintf(
    out,
    len,
    "{\"red\":{\"R\":%.1f,\"G\":%.1f,\"B\":%.1f,\"C\":%.1f}"
    ",\"green\":{\"R\":%.1f,\"G\":%.1f,\"B\":%.1f,\"C\":%.1f}"
    ",\"ir\":{\"R\":%.1f,\"G\":%.1f,\"B\":%.1f,\"C\":%.1f}}",
    (double)baseline[0].R,
    (double)baseline[0].G,
    (double)baseline[0].B,
    (double)baseline[0].C,
    (double)baseline[1].R,
    (double)baseline[1].G,
    (double)baseline[1].B,
    (double)baseline[1].C,
    (double)baseline[2].R,
    (double)baseline[2].G,
    (double)baseline[2].B,
    (double)baseline[2].C
  );
}

void printSerialJSON()
{
  char json[640];

  buildSnapshotJSON(json, sizeof(json));

  Serial.println(json);
}

void handleSensor()
{
  char json[640];

  buildSnapshotJSON(json, sizeof(json));

  server.sendHeader(
    "Access-Control-Allow-Origin",
    "*"
  );

  server.send(
    200,
    "application/json",
    json
  );
}

void handleCalibrate()
{
  server.sendHeader(
    "Access-Control-Allow-Origin",
    "*"
  );

  if (calibrating)
  {
    server.send(
      409,
      "application/json",
      "{\"cmd\":\"calibrate\",\"status\":\"busy\","
      "\"message\":\"Calibration already running\"}"
    );

    return;
  }

  // Acknowledge and return immediately. The sweep runs from loop() and the
  // caller follows it on /cal_status - holding this connection open for the
  // full 10 s is what made the whole operation hostage to one HTTP timeout,
  // with no way to tell a slow phase from a board that had reset.
  char json[240];

  snprintf(
    json,
    sizeof(json),
    "{\"cmd\":\"calibrate\",\"status\":\"started\""
    ",\"duration_ms\":%lu"
    ",\"message\":\"Calibration started - poll /cal_status\"}",
    CAL_DURATION_MS
  );

  server.send(
    200,
    "application/json",
    json
  );

  // Kicked off only after the response is on the wire, so the acknowledgement
  // is never delayed by the handshake blink or the first phase switch.
  startCalibration();
}

void handleCalStatus()
{
  char baselineJson[320];

  buildBaselineJSON(
    baselineJson,
    sizeof(baselineJson)
  );

  char json[420];

  snprintf(
    json,
    sizeof(json),
    "{\"calibrated\":%s"
    ",\"calibrating\":%s"
    ",\"progress\":%.2f"
    ",\"baseline\":%s"
    ",\"uptime_ms\":%lu}",
    calibrated ? "true" : "false",
    calibrating ? "true" : "false",
    (double)calibrationProgress(),
    baselineJson,
    millis()
  );

  server.sendHeader(
    "Access-Control-Allow-Origin",
    "*"
  );

  server.send(
    200,
    "application/json",
    json
  );
}

void allLEDsOff()
{
  digitalWrite(RED_LED, LOW);
  digitalWrite(GREEN_LED, LOW);
  digitalWrite(IR_LED, LOW);
}

void applyLed(uint8_t phase)
{
  digitalWrite(
    RED_LED,
    phase == 0 ? HIGH : LOW
  );

  digitalWrite(
    GREEN_LED,
    phase == 1 ? HIGH : LOW
  );

  digitalWrite(
    IR_LED,
    phase == 2 ? HIGH : LOW
  );

  ledPhase = phase;
}

void connectWifi()
{
  Serial.println();
  Serial.println("Connecting to Wi-Fi...");

  WiFi.mode(WIFI_STA);

  WiFi.setSleep(false);

  WiFi.begin(
    WIFI_SSID,
    WIFI_PASSWORD
  );

  int attempts = 0;

  while (
    WiFi.status() != WL_CONNECTED &&
    attempts < 40
  )
  {
    delay(500);

    Serial.print(".");

    attempts++;
  }

  Serial.println();

  if (WiFi.status() == WL_CONNECTED)
  {
    Serial.println("Wi-Fi connected!");

    Serial.print("IP address: ");
    Serial.println(WiFi.localIP());

    Serial.println();
    Serial.print("HEMOGUARD_ESP32_IP = ");
    Serial.println(WiFi.localIP());

    Serial.print("Sensor endpoint: http://");
    Serial.print(WiFi.localIP());
    Serial.println("/sensor");
  }
  else
  {
    Serial.println(
      "Wi-Fi connection FAILED - will keep retrying."
    );
  }
}

void maintainWifi()
{
  if (WiFi.status() == WL_CONNECTED) return;

  unsigned long now = millis();

  if (now - lastWifiCheck < WIFI_CHECK_MS) return;

  lastWifiCheck = now;

  Serial.println("Wi-Fi lost - reconnecting...");

  WiFi.disconnect();
  WiFi.begin(
    WIFI_SSID,
    WIFI_PASSWORD
  );
}

void handleRoot()
{
  String html = "";

  html += "<!DOCTYPE html>";
  html += "<html>";
  html += "<head>";
  html += "<meta name='viewport' content='width=device-width, initial-scale=1'>";
  html += "<title>HemoGuard Color Sensor</title>";

  html += "<style>";

  html += "body{";
  html += "font-family:Arial,sans-serif;";
  html += "text-align:center;";
  html += "background:#f2f2f2;";
  html += "margin:0;";
  html += "padding:20px;";
  html += "}";

  html += ".container{";
  html += "max-width:500px;";
  html += "margin:auto;";
  html += "background:white;";
  html += "padding:25px;";
  html += "border-radius:20px;";
  html += "box-shadow:0 3px 15px rgba(0,0,0,0.15);";
  html += "}";

  html += "h1{margin-bottom:5px;}";

  html += "#colorBox{";
  html += "width:220px;";
  html += "height:220px;";
  html += "margin:20px auto;";
  html += "border-radius:20px;";
  html += "border:2px solid #222;";
  html += "}";

  html += ".value{";
  html += "font-size:22px;";
  html += "font-weight:bold;";
  html += "margin:10px;";
  html += "}";

  html += ".raw{";
  html += "font-size:17px;";
  html += "margin:7px;";
  html += "}";

  html += "#hex{";
  html += "font-size:22px;";
  html += "font-weight:bold;";
  html += "}";

  html += "#led{";
  html += "font-size:24px;";
  html += "font-weight:bold;";
  html += "margin:15px;";
  html += "}";

  html += "#status{";
  html += "font-size:18px;";
  html += "font-weight:bold;";
  html += "}";

  html += "</style>";
  html += "</head>";

  html += "<body>";
  html += "<div class='container'>";

  html += "<h1>HemoGuard</h1>";
  html += "<h2>TCS34725 Color Sensor</h2>";

  html += "<p>Sensor: <span id='status'>...</span></p>";

  html += "<div id='led'>LED: --</div>";

  html += "<div id='colorBox'></div>";

  html += "<div id='hex'>#000000</div>";

  html += "<hr>";

  html += "<div class='value'>";
  html += "R: <span id='r'>0</span>";
  html += "</div>";

  html += "<div class='value'>";
  html += "G: <span id='g'>0</span>";
  html += "</div>";

  html += "<div class='value'>";
  html += "B: <span id='b'>0</span>";
  html += "</div>";

  html += "<div class='value'>";
  html += "Clear: <span id='c'>0</span>";
  html += "</div>";

  html += "<hr>";

  html += "<h3>Raw Sensor Values</h3>";

  html += "<div class='raw'>";
  html += "Raw R: <span id='rr'>0</span>";
  html += "</div>";

  html += "<div class='raw'>";
  html += "Raw G: <span id='gg'>0</span>";
  html += "</div>";

  html += "<div class='raw'>";
  html += "Raw B: <span id='bb'>0</span>";
  html += "</div>";

  html += "<div class='raw'>";
  html += "Raw Clear: <span id='cc'>0</span>";
  html += "</div>";

  html += "</div>";

  html += "<script>";

  html += "function updateData(){";

  html += "fetch('/data')";
  html += ".then(response=>response.json())";
  html += ".then(data=>{";

  html += "document.getElementById('status').innerText=data.ok?'CONNECTED':'NOT DETECTED';";

  html += "document.getElementById('status').style.color=data.ok?'#0a0':'#c00';";

  html += "document.getElementById('led').innerText='LED: '+data.led;";

  html += "document.getElementById('r').innerText=data.r;";
  html += "document.getElementById('g').innerText=data.g;";
  html += "document.getElementById('b').innerText=data.b;";
  html += "document.getElementById('c').innerText=data.c;";

  html += "document.getElementById('rr').innerText=data.rawR;";
  html += "document.getElementById('gg').innerText=data.rawG;";
  html += "document.getElementById('bb').innerText=data.rawB;";
  html += "document.getElementById('cc').innerText=data.rawC;";

  html += "document.getElementById('hex').innerText=data.hex;";

  html += "document.getElementById('colorBox').style.backgroundColor=data.hex;";

  html += "});";

  html += "}";

  html += "setInterval(updateData,500);";

  html += "updateData();";

  html += "</script>";

  html += "</body>";
  html += "</html>";

  server.send(
    200,
    "text/html",
    html
  );
}

void handleData()
{
  bool ok = readColor();

  String json = "{";

  json += "\"ok\":";
  json += ok ? "true" : "false";

  json += ",\"led\":\"";
  json += LED_NAMES[ledPhase];
  json += "\"";

  json += ",\"r\":";
  json += String(displayR);

  json += ",\"g\":";
  json += String(displayG);

  json += ",\"b\":";
  json += String(displayB);

  json += ",\"c\":";
  json += String(rawC);

  json += ",\"rawR\":";
  json += String(rawR);

  json += ",\"rawG\":";
  json += String(rawG);

  json += ",\"rawB\":";
  json += String(rawB);

  json += ",\"rawC\":";
  json += String(rawC);

  json += ",\"hex\":\"";
  json += hexColor;
  json += "\"";

  json += "}";

  server.send(
    200,
    "application/json",
    json
  );
}

void setup()
{
  Serial.begin(115200);

  delay(1000);

  Serial.println();
  Serial.println("================================");
  Serial.println("HEMOGUARD COLOR SYSTEM");
  Serial.println("================================");

  pinMode(RED_LED, OUTPUT);
  pinMode(GREEN_LED, OUTPUT);
  pinMode(IR_LED, OUTPUT);

  allLEDsOff();

  Wire.begin(
    TCS_SDA,
    TCS_SCL
  );

  Serial.println("I2C started.");

  Serial.println("SDA = GPIO 18");
  Serial.println("SCL = GPIO 19");

  haveColorimeter = tcs.begin();

  if (haveColorimeter)
  {
    Serial.println("TCS34725 detected!");
  }
  else
  {
    Serial.println();
    Serial.println("ERROR: TCS34725 NOT DETECTED!");

    Serial.println();
    Serial.println("Check:");

    Serial.println("TCS VCC -> ESP32 3.3V");
    Serial.println("TCS GND -> ESP32 GND");
    Serial.println("TCS SDA -> GPIO 18");
    Serial.println("TCS SCL -> GPIO 19");
  }

  connectWifi();

  server.on(
    "/",
    handleRoot
  );

  server.on(
    "/data",
    handleData
  );

  server.on(
    "/sensor",
    handleSensor
  );

  server.on(
    "/calibrate",
    handleCalibrate
  );

  server.on(
    "/cal_status",
    handleCalStatus
  );

  server.begin();

  Serial.println();
  Serial.println("Web server started.");

  Serial.println();
  Serial.println("Open the IP address above");
  Serial.println("on your phone.");

  Serial.println();
  Serial.println("================================");

  applyLed(0);

  phaseStartMs = millis();

  resetPhaseAccumulators();
}

// =====================================================
// NORMAL PHASE CYCLE
// =====================================================
// One slice per loop() pass. Previously this was a nested for/while that owned
// the CPU for a full 6 s rotation, which meant a calibration request could only
// be noticed at a dwell boundary and nothing else could interleave with it.

void resetPhaseAccumulators()
{
  phaseSumR = 0;
  phaseSumG = 0;
  phaseSumB = 0;
  phaseSumC = 0;

  phaseSamples = 0;
  phaseSettled = false;
}

void normalStep()
{
  unsigned long now = millis();

  // 1. Settle. The first conversion after an LED switch integrated under the
  //    PREVIOUS LED, so it is read and thrown away.
  if (!phaseSettled)
  {
    readColor();

    phaseSettled = true;

    return;
  }

  // 2. Accumulate for the rest of the dwell. At 50 ms integration a 1 s phase
  //    yields ~17 conversions, cutting random noise by about 4x.
  if (now - phaseStartMs < LED_DWELL_MS)
  {
    if (readColor())
    {
      phaseSumR += rawR;
      phaseSumG += rawG;
      phaseSumB += rawB;
      phaseSumC += rawC;

      phaseSamples++;
    }

    return;
  }

  // 3. Dwell over: average back into the raw registers, publish, advance.
  bool ok = (phaseSamples > 0);

  if (ok)
  {
    // Rounded rather than truncated - at these counts a consistent downward
    // bias would show up as a small fixed absorbance offset.
    rawR = (uint16_t)((phaseSumR + phaseSamples / 2) / phaseSamples);
    rawG = (uint16_t)((phaseSumG + phaseSamples / 2) / phaseSamples);
    rawB = (uint16_t)((phaseSumB + phaseSamples / 2) / phaseSamples);
    rawC = (uint16_t)((phaseSumC + phaseSamples / 2) / phaseSamples);

    deriveDisplayFromRaw();
  }
  else
  {
    blankColor();
  }

  // A colorimeter that appears after a bad boot, or drops out mid-run, is
  // picked up here rather than needing a reset.
  haveColorimeter = ok;

  // Snapshot before advancing: capturePhaseSnapshot() indexes absPhase[] by
  // ledPhase, and these averages belong to the phase that is ending.
  capturePhaseSnapshot(ok);

  printSerialJSON();

  ledPhase = (ledPhase + 1) % 3;

  applyLed(ledPhase);

  phaseStartMs = now;

  resetPhaseAccumulators();
}

// =====================================================
// LOOP
// =====================================================
// Network first, every pass. The web server is serviced whatever else is going
// on - including for the whole of a calibration sweep - so the node stays
// answerable at all times and nothing can be left waiting on it.

void loop()
{
  server.handleClient();

  maintainWifi();

  if (calibrating)
  {
    calibrationStep();
  }
  else
  {
    normalStep();
  }

  delay(5);
}