// DATA_log — CleanBox 데이터셋 로깅 전용 (Arduino IDE에서 직접 열어 쓰는 버전)
// 측정 항목: 경과시간(ms) · 내부 온도/습도 · 부팅 시 외부 기준 온도/습도 ·
//           목표 온도/습도 · 환경 상태 · 팬 PWM 목표(흡기/배기)·실측 rpm · 연기발생(O/X) · PM2.5(㎍/㎥)
// !! 날짜·시각은 ESP32에 실시간 시계가 없어 여기서 못 찍음 — 이 줄을 받는 PC/Jetson이 수신 시점의 시스템 시각을 붙여서 저장할 것
// 외부 기준값: 별도 외부 센서가 없으므로 부팅 직후(팬 0 %) BME280 값으로 한 번만 고정한다.
// 팬 제어: 시리얼로 "C fi fo" 입력 (예 "C 50 50") — 실험 중 팬 조건 변경용, 만료 없음(다음 명령까지 유지)
// 로깅 제어: 시리얼로 "STOP" → CSV 출력 중지 · "START" 또는 "RESUME" → 재개 (센서 측정 자체는 계속됨)
// PMS5003 미연결 시 pm25=0 · smoke=X로 고정 출력(정상 동작) — 연결되면 코드 수정 없이 실제 값이 그대로 나옴
// 배선: BME280 SDA21·SCL22 · PMS5003 RX16(→ESP32)·TX17 · Tach(선택) 26/27
//       흡기팬 속도(가변): GPIO18 -> DFR0457 D(신호), DFR0457 +/- -> 3V3/GND,
//                DFR0457 VIN <- 팬 전원(+), GND <- 팬 전원(-)+흡기팬GND, VOUT -> 흡기팬 VCC
//                (팬 내장 PWM 핀은 무반응 확인돼 미사용 · MOSFET로 VCC 자체를 초핑 · 500Hz, DFR0457 상한 1kHz 이하)
//       배기팬(ON/OFF만, 속도 조절 불가): VCC는 DFR0457 경유 안 하고 5V에 직결 · GND는 공통 GND ·
//                "동시" 조건일 때만 VCC를 5V에 물리고, "흡기단독/무팬" 조건일 땐 물리적으로 분리(뽑기).
//                GPIO19(PIN_FAN_OUT)는 어디에도 안 물려 있어 코드가 뭘 보내든 무해하게 무시됨 —
//                exhaust_fan_pwm 필드는 실제 속도가 아니라 "이 구간에서 배기를 꽂아둬야 함"을 표시하는 라벨일 뿐.
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <HardwareSerial.h>

// ---- 핀 ----
const int PIN_SDA = 21, PIN_SCL = 22;
const int PIN_PMS_RX = 16, PIN_PMS_TX = 17;
const int PIN_FAN_IN = 18, PIN_FAN_OUT = 19;
const int PIN_TACH_IN = 26, PIN_TACH_OUT = 27;

const uint16_t PM_SMOKE_THRESHOLD = 50;   // 이 값(㎍/㎥) 넘으면 연기발생(O) — T08 WARNING 기준과 동일
const uint32_t PMS_STALE_MS = 6000;       // PMS5003 무응답 판단 기준(T08과 동일 근거)
const uint32_t FAN_PWM_MAX = 2047;         // 11-bit PWM
const uint32_t FAN_PWM_FREQ_HZ = 500;      // DFR0457 PWM 상한 1 kHz 이하(T06 가열패드와 동일 근거) · 25kHz는 못 따라가 항상 켜진 것처럼 동작함

// 제조 환경 참고 기준: 목표 23 ℃ / 45 %RH, NORMAL 20~25 ℃ / 35~55 %RH.
// 실제 무팬 측정값 약 24 ℃는 이 NORMAL 범위에 포함된다.
const float TARGET_TEMP_C = 23.0f;
const float TARGET_RH_PCT = 45.0f;
const float NORMAL_TEMP_MIN_C = 20.0f, NORMAL_TEMP_MAX_C = 25.0f;
const float NORMAL_RH_MIN_PCT = 35.0f, NORMAL_RH_MAX_PCT = 55.0f;
const float MILD_TEMP_MAX_C = 28.0f, MILD_RH_MAX_PCT = 65.0f;
const float WARNING_TEMP_MAX_C = 32.0f, WARNING_RH_MAX_PCT = 75.0f;

Adafruit_BME280 bme;
HardwareSerial pms(2);
bool bmeOk = false;

const uint32_t TACH_MIN_PULSE_US = 1000;  // 1ms 미만 간격은 노이즈로 무시(약 3만RPM 상당까지는 허용, PWM 크로스토크 필터)
volatile uint32_t tachCntIn = 0, tachCntOut = 0;
volatile uint32_t lastPulseInUs = 0, lastPulseOutUs = 0;
void IRAM_ATTR isrIn()  { uint32_t now = micros(); if (now - lastPulseInUs  >= TACH_MIN_PULSE_US) { tachCntIn++;  lastPulseInUs  = now; } }
void IRAM_ATTR isrOut() { uint32_t now = micros(); if (now - lastPulseOutUs >= TACH_MIN_PULSE_US) { tachCntOut++; lastPulseOutUs = now; } }

int fanIn = 0, fanOut = 0;
float outsideTempBaseline = NAN, outsideRhBaseline = NAN;
uint16_t pm25 = 0;
uint32_t pmsAt = 0;
bool logging = true;

// 팬 내장 PWM 핀이 실제로는 속도에 영향을 못 주는 것으로 확인돼(VCC만 있으면 항상 최대 회전),
// DFR0457 MOSFET 모듈로 팬 VCC 라인 자체를 초핑하는 방식으로 전환. 모듈은 signal HIGH=켜짐(active-high) 가정 —
// pct=0 -> duty 0(꺼짐), pct=100 -> duty 최대(최고속)
void setFan(int pin, int pct) {
  pct = constrain(pct, 0, 100);
  ledcWrite(pin, FAN_PWM_MAX * pct / 100);
}

void captureOutsideBaseline() {
  if (!bmeOk) return;
  bme.takeForcedMeasurement();
  float t = bme.readTemperature();
  float rh = bme.readHumidity();
  if (!isnan(t) && !isnan(rh)) {
    outsideTempBaseline = t;
    outsideRhBaseline = rh;
  }
}

void readAndResetTach(uint32_t &rpmIn, uint32_t &rpmOut) {
  noInterrupts();
  uint32_t pulsesIn = tachCntIn, pulsesOut = tachCntOut;
  tachCntIn = 0;
  tachCntOut = 0;
  interrupts();
  // 일반 PC 팬은 1회전에 Tach 펄스 2개 → 펄스/초 × 30 = rpm
  rpmIn = pulsesIn * 30;
  rpmOut = pulsesOut * 30;
}

const char *environmentState(float tempC, float rhPct) {
  if (isnan(tempC) || isnan(rhPct)) return "SENSOR_ERROR";
  if (tempC > WARNING_TEMP_MAX_C || rhPct > WARNING_RH_MAX_PCT) return "DANGER";
  if (tempC > MILD_TEMP_MAX_C || rhPct > MILD_RH_MAX_PCT) return "WARNING";
  if (tempC > NORMAL_TEMP_MAX_C || rhPct > NORMAL_RH_MAX_PCT) return "MILD";
  if (tempC >= NORMAL_TEMP_MIN_C && rhPct >= NORMAL_RH_MIN_PCT) return "NORMAL";
  return "LOW";  // 이미지의 NORMAL 하한(20 ℃ / 35 %RH) 미만
}

const char *fanMode() {
  if (fanIn == 0 && fanOut == 0) return "OFF";
  if (fanIn > 0 && fanOut == 0) return "INTAKE_ONLY";
  if (fanIn == 0 && fanOut > 0) return "EXHAUST_ONLY";
  return "INTAKE_EXHAUST";
}

bool pmsRead() {
  while (pms.available() >= 32) {
    if (pms.read() != 0x42 || pms.peek() != 0x4D) continue;
    pms.read();
    uint8_t b[30] = {0x42, 0x4D};
    if (pms.readBytes(b + 2, 28) != 28) return false;
    uint16_t rx = ((uint16_t)pms.read() << 8) | pms.read(), sum = 0;
    for (int i = 0; i < 30; i++) sum += b[i];
    if (sum != rx) return false;
    pm25 = (b[12] << 8) | b[13];   // 대기환경용 PM2.5
    pmsAt = millis();
    return true;
  }
  return false;
}

void readCmd() {
  static char buf[32]; static int n = 0;
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      buf[n] = 0; n = 0;
      int a, b;
      if (sscanf(buf, "C %d %d", &a, &b) == 2) {
        fanIn = constrain(a, 0, 100); fanOut = constrain(b, 0, 100);
        setFan(PIN_FAN_IN, fanIn); setFan(PIN_FAN_OUT, fanOut);
        Serial.printf("[DATA_log] fan set -> in=%d%% out=%d%%\n", fanIn, fanOut);
      } else if (strcmp(buf, "STOP") == 0) {
        logging = false;
        Serial.println("[DATA_log] logging stopped (재개: START 또는 RESUME)");
      } else if (strcmp(buf, "START") == 0 || strcmp(buf, "RESUME") == 0) {
        logging = true;
        Serial.println("[DATA_log] logging resumed");
      }
    } else if (n < 31) buf[n++] = ch;
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);

  Wire.begin(PIN_SDA, PIN_SCL, 100000);
  bmeOk = bme.begin(0x76, &Wire) || bme.begin(0x77, &Wire);
  if (bmeOk) bme.setSampling(Adafruit_BME280::MODE_FORCED);

  pms.begin(9600, SERIAL_8N1, PIN_PMS_RX, PIN_PMS_TX);

  ledcAttach(PIN_FAN_IN, FAN_PWM_FREQ_HZ, 11);
  ledcAttach(PIN_FAN_OUT, FAN_PWM_FREQ_HZ, 11);
  setFan(PIN_FAN_IN, 0);
  setFan(PIN_FAN_OUT, 0);

  pinMode(PIN_TACH_IN, INPUT_PULLUP);
  pinMode(PIN_TACH_OUT, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_TACH_IN),  isrIn,  FALLING);
  attachInterrupt(digitalPinToInterrupt(PIN_TACH_OUT), isrOut, FALLING);

  // 팬이 모두 꺼진 상태에서 기준값을 고정한다.
  captureOutsideBaseline();

  Serial.println("[DATA_log] ready — 'C fi fo'로 팬 속도 설정 (예: C 50 50, 정지는 C 0 0) · 'STOP'/'START'(또는 'RESUME')로 로깅 출력 제어");
  if (!bmeOk) Serial.println("[DATA_log] !! BME280 not found -> SDA21/SCL22/3V3/GND 배선 확인");
  Serial.println("elapsed_ms,inside_temp_c,inside_rh_pct,outside_temp_c_baseline,outside_rh_pct_baseline,target_temp_c,target_rh_pct,environment_state,fan_mode,intake_fan_pwm,intake_fan_rpm,exhaust_fan_pwm,exhaust_fan_rpm,smoke,pm25_ugm3");
}

void loop() {
  readCmd();
  pmsRead();

  static uint32_t t1000 = 0;
  if (millis() - t1000 >= 1000) {
    t1000 = millis();

    float t = NAN, rh = NAN;
    if (bmeOk) {
      bme.takeForcedMeasurement();
      t = bme.readTemperature();
      rh = bme.readHumidity();
    }

    uint32_t rpmIn, rpmOut;
    readAndResetTach(rpmIn, rpmOut);  // Tach 미연결이면 0

    bool pmsStale = millis() - pmsAt > PMS_STALE_MS;
    uint16_t pmVal = pmsStale ? 0 : pm25;                        // PMS5003 미연결이면 항상 0 (정상)
    const char *smoke = (pmVal > PM_SMOKE_THRESHOLD) ? "O" : "X";
    const char *envState = environmentState(t, rh);
    const char *mode = fanMode();

    if (logging) {
      Serial.printf("%lu,%.2f,%.1f,%.2f,%.1f,%.1f,%.1f,%s,%s,%d,%lu,%d,%lu,%s,%u\n",
                    (unsigned long)millis(),
                    isnan(t) ? -99.0f : t, isnan(rh) ? -99.0f : rh,
                    isnan(outsideTempBaseline) ? -99.0f : outsideTempBaseline,
                    isnan(outsideRhBaseline) ? -99.0f : outsideRhBaseline,
                    TARGET_TEMP_C, TARGET_RH_PCT, envState, mode,
                    fanIn, (unsigned long)rpmIn,
                    fanOut, (unsigned long)rpmOut,
                    smoke, (unsigned)pmVal);
    }
  }
}
