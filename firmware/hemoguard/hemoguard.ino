/*
 * HemoGuard sensor node - ESP32
 * ---------------------------------------------------------------------------
 * Emits one JSON line per second at 115200 baud, consumed by serial_reader.py.
 *
 * Wiring
 *   HX711 load cell     DOUT -> GPIO4    SCK  -> GPIO5
 *   MAX30102 oximeter   SDA  -> GPIO21   SCL  -> GPIO22   (Wire,  0x57)
 *   TCS34725 colour     SDA  -> GPIO18   SCL  -> GPIO19   (Wire1, 0x29)
 *   Illumination LEDs   RED  -> GPIO25   GREEN-> GPIO26   IR -> GPIO27
 *
 * Serial commands (newline terminated)
 *   TARE        re-zero the load cell
 *   CAL:191.0   calibrate against a 191 g reference resting on the cell
 *   STATUS      dump sensor/state JSON
 */

#include <Wire.h>
#include <Preferences.h>
#include <HX711.h>
#include <MAX30105.h>
#include <spo2_algorithm.h>
#include <Adafruit_TCS34725.h>
#include <esp_task_wdt.h>

// --------------------------------------------------------------------------
// pins & constants
// --------------------------------------------------------------------------
#define HX711_DOUT   4
#define HX711_SCK    5

#define OXI_SDA     21
#define OXI_SCL     22
#define COL_SDA     18
#define COL_SCL     19

#define LED_RED     25
#define LED_GREEN   26
#define LED_IR      27

static const float    DEFAULT_SCALE_FACTOR = 430.0f;
static const char    *NVS_NAMESPACE        = "hemoguard";
static const char    *NVS_KEY_FACTOR       = "scale_factor";
static const float    WEIGHT_DEADBAND_G    = 0.5f;   // below this, report 0.0
static const uint32_t EMIT_INTERVAL_MS     = 1000;   // one JSON line per second
static const uint32_t LED_DWELL_MS         = 2000;   // 2 s per illumination LED
static const uint8_t  WDT_TIMEOUT_S        = 5;

// The Maxim SpO2 routine needs a 4 s window: 100 samples at 25 Hz.
static const int32_t  SAMPLE_BUFFER_LEN    = 100;
static const uint32_t FINGER_IR_THRESHOLD  = 50000;  // below this, nothing on the sensor

// --------------------------------------------------------------------------
// globals
// --------------------------------------------------------------------------
HX711 scale;
MAX30105 oximeter;
Adafruit_TCS34725 colorimeter(TCS34725_INTEGRATIONTIME_50MS, TCS34725_GAIN_4X);
Preferences prefs;

bool haveScale = false;
bool haveOximeter = false;
bool haveColorimeter = false;

float scaleFactor = DEFAULT_SCALE_FACTOR;

// Rolling PPG window, filled continuously so a reading is always ready.
uint32_t irBuffer[SAMPLE_BUFFER_LEN];
uint32_t redBuffer[SAMPLE_BUFFER_LEN];
uint32_t irOrdered[SAMPLE_BUFFER_LEN];
uint32_t redOrdered[SAMPLE_BUFFER_LEN];
int32_t  bufWrite = 0;
int32_t  bufFilled = 0;

int32_t lastSpo2 = 0;
int32_t lastPulse = 0;
bool    lastPpgValid = false;

uint8_t  ledPhase = 0;                 // 0 = RED, 1 = GREEN, 2 = IR
uint32_t lastEmitMs = 0;
uint32_t lastLedSwitchMs = 0;

char cmdBuffer[48];
uint8_t cmdLen = 0;

static const char *LED_NAMES[3] = { "RED", "GREEN", "IR" };

// --------------------------------------------------------------------------
// helpers
// --------------------------------------------------------------------------
static void feedWatchdog() { esp_task_wdt_reset(); }

static void emitError(const char *sensor, const char *message, bool fatal) {
  Serial.printf("{\"error\":\"%s\",\"detail\":\"%s\",\"fatal\":%s}\n",
                sensor, message, fatal ? "true" : "false");
}

static void applyLed(uint8_t phase) {
  // Exactly one illumination LED on at a time, so the colorimeter only ever
  // sees a single known source.
  digitalWrite(LED_RED,   phase == 0 ? HIGH : LOW);
  digitalWrite(LED_GREEN, phase == 1 ? HIGH : LOW);
  digitalWrite(LED_IR,    phase == 2 ? HIGH : LOW);
}

// --------------------------------------------------------------------------
// load cell
// --------------------------------------------------------------------------
// Calibration lives in NVS so a reboot does not silently revert the cell to the
// 430.0 default and mis-scale every weight from then on.
static void loadCalibration() {
  prefs.begin(NVS_NAMESPACE, false);
  scaleFactor = prefs.getFloat(NVS_KEY_FACTOR, DEFAULT_SCALE_FACTOR);
  if (!(scaleFactor > 0.0f)) scaleFactor = DEFAULT_SCALE_FACTOR;  // also catches NaN
}

static bool saveCalibration() {
  return prefs.putFloat(NVS_KEY_FACTOR, scaleFactor) > 0;
}

static bool initScale() {
  scale.begin(HX711_DOUT, HX711_SCK);
  if (!scale.wait_ready_timeout(1500)) return false;
  scale.set_scale(scaleFactor);
  // Tare is deliberately NOT persisted: the zero point depends on what is
  // sitting on the cell right now, so it is re-measured every boot.
  scale.tare(20);
  return true;
}

static bool readWeight(float &grams) {
  grams = 0.0f;
  if (!haveScale) return false;
  if (!scale.wait_ready_timeout(400)) return false;

  float value = scale.get_units(3);
  // Drift and negative excursions both mean "nothing meaningful on the cell".
  if (value < WEIGHT_DEADBAND_G) value = 0.0f;
  grams = value;
  return true;
}

static void doTare() {
  if (!haveScale) {
    Serial.println("{\"cmd\":\"tare\",\"status\":\"error\",\"detail\":\"no load cell\"}");
    return;
  }
  scale.tare(20);
  Serial.println("{\"cmd\":\"tare\",\"status\":\"ok\"}");
}

static void doCalibrate(float knownGrams) {
  if (!haveScale) {
    Serial.println("{\"cmd\":\"cal\",\"status\":\"error\",\"detail\":\"no load cell\"}");
    return;
  }
  if (knownGrams <= 0.0f) {
    Serial.println("{\"cmd\":\"cal\",\"status\":\"error\",\"detail\":\"reference must be > 0\"}");
    return;
  }

  // Raw counts per gram, measured against the reference weight currently
  // resting on the cell. Tare must already be correct for this to mean anything.
  scale.set_scale();
  float raw = scale.get_units(20);
  if (raw <= 0.0f) {
    Serial.println("{\"cmd\":\"cal\",\"status\":\"error\",\"detail\":\"no load detected\"}");
    scale.set_scale(scaleFactor);
    return;
  }

  scaleFactor = raw / knownGrams;
  scale.set_scale(scaleFactor);
  bool saved = saveCalibration();
  Serial.printf("{\"cmd\":\"cal\",\"factor\":%.2f,\"reference_g\":%.2f,"
                "\"saved\":%s,\"status\":\"ok\"}\n",
                scaleFactor, knownGrams, saved ? "true" : "false");
}

// --------------------------------------------------------------------------
// oximeter
// --------------------------------------------------------------------------
static bool initOximeter() {
  Wire.begin(OXI_SDA, OXI_SCL);
  if (!oximeter.begin(Wire, I2C_SPEED_FAST)) return false;

  // 100 Hz with 4x averaging -> 25 Hz effective, which is what the Maxim
  // algorithm's 100-sample window assumes.
  oximeter.setup(60, 4, 2, 100, 411, 4096);
  oximeter.setPulseAmplitudeRed(0x0A);
  oximeter.setPulseAmplitudeIR(0x0A);
  return true;
}

// Drains whatever the FIFO has. Called every loop so the window stays current
// without ever blocking the 1 s emit cadence.
static void pollOximeter() {
  if (!haveOximeter) return;

  oximeter.check();
  while (oximeter.available()) {
    redBuffer[bufWrite] = oximeter.getFIFORed();
    irBuffer[bufWrite]  = oximeter.getFIFOIR();
    oximeter.nextSample();
    bufWrite = (bufWrite + 1) % SAMPLE_BUFFER_LEN;
    if (bufFilled < SAMPLE_BUFFER_LEN) bufFilled++;
  }
}

static bool computeVitals(int32_t &spo2, int32_t &pulse) {
  spo2 = 0;
  pulse = 0;
  if (!haveOximeter || bufFilled < SAMPLE_BUFFER_LEN) return false;

  // Unroll the circular buffer into chronological order for the algorithm.
  int32_t start = bufWrite;
  uint64_t irSum = 0;
  for (int32_t i = 0; i < SAMPLE_BUFFER_LEN; i++) {
    int32_t src = (start + i) % SAMPLE_BUFFER_LEN;
    irOrdered[i] = irBuffer[src];
    redOrdered[i] = redBuffer[src];
    irSum += irBuffer[src];
  }

  // No finger, no perfusion - the algorithm would still return a number.
  if ((irSum / SAMPLE_BUFFER_LEN) < FINGER_IR_THRESHOLD) return false;

  int32_t spo2Value = 0, pulseValue = 0;
  int8_t spo2Valid = 0, pulseValid = 0;
  maxim_heart_rate_and_oxygen_saturation(
      irOrdered, SAMPLE_BUFFER_LEN, redOrdered,
      &spo2Value, &spo2Valid, &pulseValue, &pulseValid);

  if (!spo2Valid || !pulseValid) return false;
  if (spo2Value < 50 || spo2Value > 100) return false;
  if (pulseValue < 25 || pulseValue > 250) return false;

  spo2 = spo2Value;
  pulse = pulseValue;
  return true;
}

// --------------------------------------------------------------------------
// colorimeter
// --------------------------------------------------------------------------
static bool initColorimeter() {
  Wire1.begin(COL_SDA, COL_SCL);
  return colorimeter.begin(TCS34725_ADDRESS, &Wire1);
}

static bool readColour(uint16_t &r, uint16_t &g, uint16_t &b, uint16_t &c) {
  r = g = b = c = 0;
  if (!haveColorimeter) return false;
  colorimeter.getRawData(&r, &g, &b, &c);
  return true;
}

// --------------------------------------------------------------------------
// serial commands
// --------------------------------------------------------------------------
static void printStatus() {
  Serial.printf(
      "{\"cmd\":\"status\",\"uptime_ms\":%lu,\"load_cell\":%s,\"oximeter\":%s,"
      "\"colorimeter\":%s,\"scale_factor\":%.2f,\"led\":\"%s\","
      "\"ppg_window\":%ld,\"ppg_valid\":%s,\"status\":\"ok\"}\n",
      (unsigned long)millis(),
      haveScale ? "true" : "false",
      haveOximeter ? "true" : "false",
      haveColorimeter ? "true" : "false",
      scaleFactor,
      LED_NAMES[ledPhase],
      (long)bufFilled,
      lastPpgValid ? "true" : "false");
}

static void handleCommand(char *line) {
  // Trim trailing CR / whitespace left by the terminal.
  size_t len = strlen(line);
  while (len > 0 && (line[len - 1] == '\r' || line[len - 1] == ' ')) line[--len] = '\0';
  if (len == 0) return;

  if (strcasecmp(line, "TARE") == 0) {
    doTare();
  } else if (strcasecmp(line, "STATUS") == 0) {
    printStatus();
  } else if (strncasecmp(line, "CAL:", 4) == 0) {
    doCalibrate(atof(line + 4));
  } else {
    Serial.printf("{\"cmd\":\"unknown\",\"input\":\"%s\",\"status\":\"error\"}\n", line);
  }
}

static void pollSerial() {
  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\n') {
      cmdBuffer[cmdLen] = '\0';
      handleCommand(cmdBuffer);
      cmdLen = 0;
    } else if (cmdLen < sizeof(cmdBuffer) - 1) {
      cmdBuffer[cmdLen++] = ch;
    } else {
      cmdLen = 0;   // overlong input, discard rather than overflow
    }
  }
}

// --------------------------------------------------------------------------
// setup
// --------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);

  pinMode(LED_RED, OUTPUT);
  pinMode(LED_GREEN, OUTPUT);
  pinMode(LED_IR, OUTPUT);
  digitalWrite(LED_RED, LOW);
  digitalWrite(LED_GREEN, LOW);
  digitalWrite(LED_IR, LOW);

  // Watchdog first, so a sensor that hangs during init still trips a reset.
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  // Core 3.x brings its own task watchdog up before setup() runs, so calling
  // esp_task_wdt_init() here returns ESP_ERR_INVALID_STATE and silently leaves
  // the default timeout in place. Reconfigure the running one instead, and only
  // fall back to init() on a build where the core left it disabled.
  esp_task_wdt_config_t wdtConfig = {
      .timeout_ms = WDT_TIMEOUT_S * 1000,
      .idle_core_mask = 0,
      .trigger_panic = true,
  };
  if (esp_task_wdt_reconfigure(&wdtConfig) != ESP_OK) {
    esp_task_wdt_init(&wdtConfig);
  }
#else
  esp_task_wdt_init(WDT_TIMEOUT_S, true);
#endif
  esp_task_wdt_add(NULL);
  feedWatchdog();

  haveOximeter = initOximeter();
  if (!haveOximeter) emitError("MAX30102", "not found on Wire (21/22)", false);
  feedWatchdog();

  haveColorimeter = initColorimeter();
  if (!haveColorimeter) emitError("TCS34725", "not found on Wire1 (18/19)", false);
  feedWatchdog();

  loadCalibration();   // must precede initScale() so the stored factor is applied
  haveScale = initScale();
  if (!haveScale) {
    emitError("HX711", "not found on GPIO4/GPIO5 - halted", true);
    // Halt as specified, but keep feeding the watchdog and repeating the error
    // so the reason stays visible instead of the board silently rebooting.
    for (;;) {
      feedWatchdog();
      delay(5000);
      emitError("HX711", "not found on GPIO4/GPIO5 - halted", true);
    }
  }

  applyLed(ledPhase);
  lastLedSwitchMs = millis();
  lastEmitMs = millis();
}

// --------------------------------------------------------------------------
// loop
// --------------------------------------------------------------------------
void loop() {
  feedWatchdog();

  pollOximeter();   // keep the PPG window fed every pass
  pollSerial();     // commands are handled between readings

  uint32_t now = millis();
  if (now - lastEmitMs < EMIT_INTERVAL_MS) return;
  lastEmitMs = now;

  float weight = 0.0f;
  bool weightOk = readWeight(weight);
  feedWatchdog();

  int32_t spo2 = 0, pulse = 0;
  bool ppgOk = computeVitals(spo2, pulse);
  lastSpo2 = spo2;
  lastPulse = pulse;
  lastPpgValid = ppgOk;
  feedWatchdog();

  uint16_t r = 0, g = 0, b = 0, c = 0;
  bool colourOk = readColour(r, g, b, c);
  feedWatchdog();

  bool valid = weightOk && ppgOk && colourOk;

  // "uptime_ms", not "timestamp": serial_reader.py stamps its own wall-clock
  // "timestamp" onto every reading, which would overwrite this and lose it.
  Serial.printf(
      "{\"uptime_ms\":%lu,\"weight\":%.2f,\"spo2\":%ld,\"pulse\":%ld,"
      "\"red\":%u,\"green\":%u,\"blue\":%u,\"clear\":%u,"
      "\"led\":\"%s\",\"valid\":%s}\n",
      (unsigned long)now, weight, (long)spo2, (long)pulse,
      r, g, b, c, LED_NAMES[ledPhase], valid ? "true" : "false");

  // Advance the illumination phase after emitting, so the next colour read
  // happens a full second after the switch and never straddles two LEDs.
  if (now - lastLedSwitchMs >= LED_DWELL_MS) {
    ledPhase = (ledPhase + 1) % 3;
    applyLed(ledPhase);
    lastLedSwitchMs = now;
  }
}
