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

static const unsigned long LED_DWELL_MS = 2000;
static const unsigned long WIFI_CHECK_MS = 5000;

static const float BL_EPSILON = 1.0f;
static const float BL_PATH_LENGTH = 1.0f;

static const unsigned long CAL_DURATION_MS = 10000;
static const unsigned long CAL_PHASE_MS = CAL_DURATION_MS / 3;
static const uint8_t CAL_SAMPLES = 5;

Adafruit_TCS34725 tcs(
  TCS34725_INTEGRATIONTIME_50MS,
  TCS34725_GAIN_4X
);

bool haveColorimeter = false;

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

bool colorimeterPresent()
{
  Wire.beginTransmission(TCS34725_ADDRESS);

  return Wire.endTransmission() == 0;
}

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

bool settleAndRead()
{
  readColor();

  server.handleClient();

  return readColor();
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

void runCalibration()
{
  Serial.println("{\"cmd\":\"calibrate\",\"status\":\"started\"}");

  for (uint8_t phase = 0; phase < 3; phase++)
  {
    unsigned long phaseStart = millis();

    applyLed(phase);

    readColor();

    float sumR = 0.0f;
    float sumG = 0.0f;
    float sumB = 0.0f;
    float sumC = 0.0f;

    uint8_t taken = 0;

    for (uint8_t i = 0; i < CAL_SAMPLES; i++)
    {
      if (readColor())
      {
        sumR += (float)rawR;
        sumG += (float)rawG;
        sumB += (float)rawB;
        sumC += (float)rawC;

        taken++;
      }

      unsigned long slotEnd =
        phaseStart + (CAL_PHASE_MS * (unsigned long)(i + 1)) / CAL_SAMPLES;

      while ((long)(millis() - slotEnd) < 0)
      {
        delay(5);
      }
    }

    if (taken > 0)
    {
      baseline[phase].R = sumR / (float)taken;
      baseline[phase].G = sumG / (float)taken;
      baseline[phase].B = sumB / (float)taken;
      baseline[phase].C = sumC / (float)taken;

      baseline[phase].valid = (baseline[phase].C > 0.0f);
    }
    else
    {
      baseline[phase].valid = false;
    }
  }

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

  applyLed(ledPhase);
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
  runCalibration();

  char baselineJson[320];

  buildBaselineJSON(
    baselineJson,
    sizeof(baselineJson)
  );

  char json[480];

  snprintf(
    json,
    sizeof(json),
    "{\"cmd\":\"calibrate\""
    ",\"status\":\"%s\""
    ",\"baseline\":%s"
    ",\"uptime_ms\":%lu"
    ",\"message\":\"%s\"}",
    calibrated ? "complete" : "error",
    baselineJson,
    millis(),
    calibrated
      ? "Water baseline stored"
      : "Calibration failed - colorimeter did not return usable light"
  );

  Serial.println(json);

  server.sendHeader(
    "Access-Control-Allow-Origin",
    "*"
  );

  server.send(
    calibrated ? 200 : 503,
    "application/json",
    json
  );
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
    "{\"calibrated\":%s,\"baseline\":%s,\"uptime_ms\":%lu}",
    calibrated ? "true" : "false",
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
}

void loop()
{
  for (uint8_t phase = 0; phase < 3; phase++)
  {
    applyLed(phase);

    unsigned long startTime = millis();

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