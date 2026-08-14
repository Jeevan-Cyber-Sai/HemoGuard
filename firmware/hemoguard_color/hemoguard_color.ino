/*
 * HemoGuard colour node - ESP32 + TCS34725 over Wi-Fi
 * ---------------------------------------------------------------------------
 * Serves the current LED-phase snapshot as JSON on GET /sensor, which
 * backend/server.py polls once a second.
 *
 * Wire format (see backend REQUIRED_FIELDS):
 *   red/green/blue/clear   RAW TCS34725 counts - these are what the backend
 *                          scores, and they must stay raw so hb_ratio matches
 *                          BASELINE_HB. Normalised 0-255 values ride along as
 *                          norm_r/norm_g/norm_b purely for display.
 *   weight/spo2/pulse      null - not fitted on this node. The backend reads
 *                          null as "absent" and renormalises the risk weights
 *                          over the channels that are present, so wiring the
 *                          load cell and oximeter in later needs no backend
 *                          change: just emit real numbers here.
 *   valid                  true when the colorimeter answered this phase.
 *                          Absent sensors do NOT make a reading invalid;
 *                          only a sensor that was supposed to answer failing.
 */

#include <Wire.h>
#include <WiFi.h>
#include <WebServer.h>
#include "Adafruit_TCS34725.h"
#include "secrets.h"

// =====================================================
// TCS34725 CONNECTION
// =====================================================

#define TCS_SDA 18
#define TCS_SCL 19

// =====================================================
// LED CONNECTIONS
// =====================================================
// NOTE: RED and GREEN are the reverse of firmware/hemoguard/hemoguard.ino,
// which uses RED=25 / GREEN=26. These values match the rig this sketch is
// actually wired to - do not "align" them without re-wiring the board first.
// The backend gates the haemoglobin score on led == "RED", so a swap would
// silently measure blood colour under green illumination.

#define RED_LED   26
#define GREEN_LED 25
#define IR_LED    27

// =====================================================
// TIMING
// =====================================================

static const unsigned long LED_DWELL_MS   = 2000;   // per illumination phase
static const unsigned long WIFI_CHECK_MS  = 5000;   // reconnect poll interval

// =====================================================
// TCS34725
// =====================================================

Adafruit_TCS34725 tcs(
  TCS34725_INTEGRATIONTIME_50MS,
  TCS34725_GAIN_4X
);

bool haveColorimeter = false;

// =====================================================
// WIFI
// =====================================================

WebServer server(80);

unsigned long lastWifiCheck = 0;

// =====================================================
// SENSOR VALUES
// =====================================================

uint16_t rawR = 0;
uint16_t rawG = 0;
uint16_t rawB = 0;
uint16_t rawC = 0;

int displayR = 0;
int displayG = 0;
int displayB = 0;

String hexColor = "#000000";

// =====================================================
// LED PHASE
// =====================================================

static const char *LED_NAMES[3] = { "RED", "GREEN", "IR" };

uint8_t ledPhase = 0;

// =====================================================
// PHASE SNAPSHOT
// =====================================================
// Values measured at the start of the current LED phase.
// /sensor serves these rather than reading live, so the RGB
// always matches the led field. Needed because /data calls
// readColor() on every browser poll, which would otherwise
// overwrite the phase values mid-phase.

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

// =====================================================
// RGB -> HEX
// =====================================================

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

// =====================================================
// COLORIMETER PRESENCE
// =====================================================
// getRawData() returns void and happily reports zeros on a dead bus, so a
// yanked cable would otherwise look like a legitimate pitch-black reading.
// An address probe is the only way to tell the two apart.

bool colorimeterPresent()
{
  Wire.beginTransmission(TCS34725_ADDRESS);

  return Wire.endTransmission() == 0;
}

// A device that is present but was never begun() answers its address while
// returning whatever the ADC last held - begin() is what writes the
// integration time, gain and enable bits. So a sensor missing at boot, or
// unplugged and reconnected, has to be reinitialised and not merely detected.
bool ensureColorimeter()
{
  if (!colorimeterPresent())
  {
    haveColorimeter = false;

    return false;
  }

  if (!haveColorimeter)
  {
    haveColorimeter = tcs.begin();

    if (haveColorimeter)
    {
      Serial.println("TCS34725 reattached - reinitialised.");
    }
  }

  return haveColorimeter;
}

// =====================================================
// READ COLOR
// =====================================================

bool readColor()
{
  if (!ensureColorimeter())
  {
    rawR = 0;
    rawG = 0;
    rawB = 0;
    rawC = 0;

    displayR = 0;
    displayG = 0;
    displayB = 0;

    hexColor = "#000000";

    return false;
  }

  tcs.getRawData(
    &rawR,
    &rawG,
    &rawB,
    &rawC
  );

  if (rawC > 0)
  {
    float r = ((float)rawR / rawC) * 255.0;
    float g = ((float)rawG / rawC) * 255.0;
    float b = ((float)rawB / rawC) * 255.0;

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

  return true;
}

// =====================================================
// SETTLE AND READ
// =====================================================
// The TCS34725 integrates over 50 ms, and getRawData() returns
// whatever the ADC registers already hold. A read taken straight
// after switching LEDs therefore returns light from the PREVIOUS
// LED. Discard that conversion and take the next one, which is
// integrated under the LED now lit.

bool settleAndRead()
{
  readColor();

  server.handleClient();

  return readColor();
}

// =====================================================
// CAPTURE PHASE SNAPSHOT
// =====================================================

void capturePhaseSnapshot(bool ok)
{
  snapRawR = rawR;
  snapRawG = rawG;
  snapRawB = rawB;
  snapRawC = rawC;

  snapNormR = displayR;
  snapNormG = displayG;
  snapNormB = displayB;

  strncpy(snapHex, hexColor.c_str(), sizeof(snapHex) - 1);
  snapHex[sizeof(snapHex) - 1] = '\0';

  snapPhase = ledPhase;

  snapValid = ok;

  snapUptime = millis();
}

// =====================================================
// BUILD SNAPSHOT JSON
// =====================================================
// One definition shared by /sensor and the serial line, so the two feeds can
// never drift apart.

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
    ",\"valid\":%s}",
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
    snapValid ? "true" : "false"
  );
}

// =====================================================
// SERIAL JSON OUTPUT
// =====================================================
// One line per LED phase. Kept for bench debugging with the board on USB;
// the backend consumes GET /sensor, not this.

void printSerialJSON()
{
  char json[320];

  buildSnapshotJSON(json, sizeof(json));

  Serial.println(json);
}

// =====================================================
// SENSOR ENDPOINT  ->  GET /sensor
// =====================================================
// Serves the phase snapshot to backend/server.py.

void handleSensor()
{
  char json[320];

  buildSnapshotJSON(json, sizeof(json));

  // The dashboard is served from the backend's origin, not the node's, so the
  // browser treats a direct fetch here as cross-origin.
  server.sendHeader("Access-Control-Allow-Origin", "*");

  server.send(
    200,
    "application/json",
    json
  );
}

// =====================================================
// ALL LEDS OFF
// =====================================================

void allLEDsOff()
{
  digitalWrite(RED_LED, LOW);
  digitalWrite(GREEN_LED, LOW);
  digitalWrite(IR_LED, LOW);
}

// =====================================================
// APPLY LED PHASE
// =====================================================
// Exactly one illumination LED on at a time, so the colorimeter only ever
// sees a single known source.

void applyLed(uint8_t phase)
{
  digitalWrite(RED_LED,   phase == 0 ? HIGH : LOW);
  digitalWrite(GREEN_LED, phase == 1 ? HIGH : LOW);
  digitalWrite(IR_LED,    phase == 2 ? HIGH : LOW);

  ledPhase = phase;
}

// =====================================================
// WIFI
// =====================================================

void connectWifi()
{
  Serial.println();
  Serial.println("Connecting to Wi-Fi...");

  WiFi.mode(WIFI_STA);

  // Modem sleep adds hundreds of ms of latency to inbound requests, which is
  // most of the backend's 2 s poll timeout.
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

    // Copy this line's value into the backend environment variable.
    Serial.println();
    Serial.print("HEMOGUARD_ESP32_IP = ");
    Serial.println(WiFi.localIP());

    Serial.print("Sensor endpoint: http://");
    Serial.print(WiFi.localIP());
    Serial.println("/sensor");
  }
  else
  {
    Serial.println("Wi-Fi connection FAILED - will keep retrying.");
  }
}

// A dropped association otherwise leaves the node serving nothing until
// somebody power-cycles it, which the backend can only report as a timeout.
void maintainWifi()
{
  if (WiFi.status() == WL_CONNECTED) return;

  unsigned long now = millis();

  if (now - lastWifiCheck < WIFI_CHECK_MS) return;

  lastWifiCheck = now;

  Serial.println("Wi-Fi lost - reconnecting...");

  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

// =====================================================
// WEB PAGE
// =====================================================

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

  // Current LED
  html += "<div id='led'>LED: --</div>";

  // Color display
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

  // ===================================================
  // JAVASCRIPT
  // ===================================================

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

// =====================================================
// SEND DATA TO PHONE
// =====================================================

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

// =====================================================
// SETUP
// =====================================================

void setup()
{
  Serial.begin(115200);

  delay(1000);

  Serial.println();
  Serial.println("================================");
  Serial.println("HEMOGUARD COLOR SYSTEM");
  Serial.println("================================");

  // ===================================================
  // LED SETUP
  // ===================================================

  pinMode(RED_LED, OUTPUT);
  pinMode(GREEN_LED, OUTPUT);
  pinMode(IR_LED, OUTPUT);

  allLEDsOff();

  // ===================================================
  // I2C
  // ===================================================

  Wire.begin(
    TCS_SDA,
    TCS_SCL
  );

  Serial.println("I2C started.");

  Serial.println("SDA = GPIO 18");
  Serial.println("SCL = GPIO 19");

  // ===================================================
  // TCS34725
  // ===================================================
  // A missing colorimeter is reported and retried in the loop rather than
  // halting: the node still answers /sensor with valid:false, which the
  // backend surfaces on the dashboard as a sensor fault. Spinning forever
  // here would instead look identical to a dead board.

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

  // ===================================================
  // WIFI
  // ===================================================

  connectWifi();

  // ===================================================
  // WEB SERVER
  // ===================================================

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

  server.begin();

  Serial.println();
  Serial.println("Web server started.");

  Serial.println();
  Serial.println("Open the IP address above");
  Serial.println("on your phone.");

  Serial.println();
  Serial.println("================================");

  applyLed(0);
}

// =====================================================
// LOOP
// =====================================================
// RED -> GREEN -> IR, each for LED_DWELL_MS.
//
// Read and snapshot at the START of each phase so /sensor serves values
// captured while that LED is the one lit. The settle read costs ~100 ms of
// the dwell; the phase length is unchanged because startTime is taken at
// the switch.

void loop()
{
  for (uint8_t phase = 0; phase < 3; phase++)
  {
    applyLed(phase);

    unsigned long startTime = millis();

    // readColor() reattaches a colorimeter that appears after a bad boot or
    // drops out mid-run, so neither case needs a reset to recover.
    bool ok = settleAndRead();

    capturePhaseSnapshot(ok);

    printSerialJSON();

    while (millis() - startTime < LED_DWELL_MS)
    {
      server.handleClient();

      maintainWifi();

      delay(10);
    }
  }
}
