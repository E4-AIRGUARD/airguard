// new_fan_t — 정지·약풍·중풍·강풍 4단 팬 제어(25 kHz 11비트 PWM + Tach 회전수) + BME280(온습압) + PMS5003(미세먼지)
//
// 팬 배선(2026-09-22 정정): 팬 Vcc·GND → 브레드보드 5 V 줄 (ESP32 5V 핀 아님)
//       PWM(노란선)  흡기 → 13 · 배기 → 19
//       Tach(파란선) 흡기 → 32 · 배기 → 33
// !! GND는 ESP32·팬·BME280·PMS5003 전부 반드시 공통으로 묶을 것 — 기준 전위가 다르면
//    Tach 펄스가 튀거나 I2C·UART가 아예 안 붙는다.
// !! Tach를 GPIO34·35·36·39에 꽂으면 안 된다. 입력 전용이라 내부 풀업 회로가 없고,
//    오픈 컬렉터인 팬 Tach는 풀업 없이는 라인이 떠서 노이즈만 읽힌다.
//
// 센서 배선(T08_cleanbox_fw와 동일 핀, 팬 핀과 겹치지 않음 — 2026-09-22 추가):
//       BME280   SDA → 21 · SCL → 22 (I2C, 0x76/0x77 자동 탐색)
//       PMS5003  RX(ESP32측, PMS TX에 연결) → 16 · TX(ESP32측, PMS RX에 연결) → 17 (UART2, 9600bps)
// 라이브러리: Adafruit BME280 + Adafruit Unified Sensor + Adafruit BusIO
//
// 단계값은 실측 duty→rpm 곡선에서 간격이 가장 크게 벌어지도록 골랐다:
//   15%→900 · 20%→1410 · 25%→1800 · 30%→2300 · 40%→3270 · 60%→4770 · 80%→6210 · 100%→6930
//   80 % 위로는 곡선이 평평해져(6210→6930) 더 올려도 체감이 거의 없다 → 강풍은 100 %.
//   콜드 스타트는 15 %에서도 뜨는 걸 확인해서 시작 부스트는 넣지 않았다.
//
// 시리얼 명령 (115200, 줄 끝에 Enter):
//   0 정지 · 1 약풍 · 2 중풍 · 3 강풍 · d<n> 수동 duty(흡기·배기 동일) · ? 상태
//   F <in> <out>  흡기·배기 독립 duty(%) 지정 — 예: "F 35 0" = 흡기만 약풍, 배기 정지
//
// 출력: 1초마다 사람이 읽는 [FAN] 줄 + PC 로깅용 "DATA,..." CSV 줄을 같이 찍는다.
//   DATA,elapsed_ms,duty_in,rpm_in,duty_out,rpm_out,temp_c,rh_pct,pressure_hpa,pm1_0,pm2_5,pm10,bme_err,pms_stale
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <HardwareSerial.h>

const int FAN_IN = 13, FAN_OUT = 19;
const int TACH_IN = 32, TACH_OUT = 33;
const uint32_t PWM_FREQ = 25000;   // 4핀 PC 팬 규격 25 kHz
const uint8_t  PWM_RES  = 11;
const uint32_t DUTY_MAX = (1u << PWM_RES) - 1;
const uint32_t REPORT_MS = 1000;

const int PIN_SDA = 21, PIN_SCL = 22;
const int PIN_PMS_RX = 16, PIN_PMS_TX = 17;
const uint32_t PMS_STALE_MS = 6000;   // PMS5003 안정 모드 출력 간격 최대 2.3 s → 2회 연속 누락까지 정상으로 봄 (T08과 동일 기준)

struct Level { const char *name; int pct; };
const Level LEVELS[] = {
  {"정지", 0},
  {"약풍", 35},
  {"중풍", 60},
  {"강풍", 100},
};
const int NLEVEL = sizeof(LEVELS) / sizeof(LEVELS[0]);

// 25 kHz PWM 스위칭이 Tach 선에 실려 가짜 엣지가 섞인다(중간 duty에서 특히 심함).
// 진짜 펄스 간격은 7000 rpm에서도 4.3 ms라, 1 ms보다 빨리 온 엣지는 노이즈로 버린다
// (= 30,000 rpm까지는 정상으로 통과시킨다).
const uint32_t MIN_PULSE_US = 1000;

volatile uint32_t tachCntIn = 0, tachCntOut = 0;
volatile uint32_t lastEdgeIn = 0, lastEdgeOut = 0;

void IRAM_ATTR isrIn() {
  uint32_t now = micros();
  if (now - lastEdgeIn < MIN_PULSE_US) return;
  lastEdgeIn = now;
  tachCntIn = tachCntIn + 1;
}

void IRAM_ATTR isrOut() {
  uint32_t now = micros();
  if (now - lastEdgeOut < MIN_PULSE_US) return;
  lastEdgeOut = now;
  tachCntOut = tachCntOut + 1;
}

// 흡기·배기 duty/이름을 독립적으로 추적 — 레벨(0~3)·d<n> 명령은 둘을 항상 같은 값으로 같이 설정하고,
// F 명령만 서로 다른 값을 허용한다.
int pctIn = 0, pctOut = 0;
const char *nameIn = "정지", *nameOut = "정지";
uint32_t rpmIn = 0, rpmOut = 0;
char cmdBuf[16];
uint8_t cmdLen = 0;

Adafruit_BME280 bme;
bool bmeOk = false;
HardwareSerial pms(2);
struct Sens { float t = NAN, rh = NAN, p = NAN; uint16_t pm1 = 0, pm25 = 0, pm10 = 0; uint32_t pmsAt = 0; } S;

// ---- PMS5003 (T08_cleanbox_fw와 동일 파싱) ----
bool pmsRead() {
  while (pms.available() >= 32) {
    if (pms.read() != 0x42 || pms.peek() != 0x4D) continue;
    pms.read();
    uint8_t b[30] = {0x42, 0x4D};
    if (pms.readBytes(b + 2, 28) != 28) return false;
    uint16_t rx = ((uint16_t)pms.read() << 8) | pms.read(), sum = 0;
    for (int i = 0; i < 30; i++) sum += b[i];
    if (sum != rx) return false;
    S.pm1  = (b[10] << 8) | b[11];   // data4~6 = 대기환경용 PM1 / PM2.5 / PM10
    S.pm25 = (b[12] << 8) | b[13];
    S.pm10 = (b[14] << 8) | b[15];
    S.pmsAt = millis();
    return true;
  }
  return false;
}

void applyBoth(int pct, const char *name) {
  pctIn = pctOut = constrain(pct, 0, 100);
  nameIn = nameOut = name;
  uint32_t d = (uint32_t)DUTY_MAX * pctIn / 100;
  ledcWrite(FAN_IN, d);
  ledcWrite(FAN_OUT, d);
  Serial.printf("[FAN] %s (duty %d %%)\n", name, pctIn);
}

void applyIndep(int pctI, int pctO) {
  pctIn = constrain(pctI, 0, 100);
  pctOut = constrain(pctO, 0, 100);
  nameIn = "수동(F)";
  nameOut = "수동(F)";
  ledcWrite(FAN_IN,  (uint32_t)DUTY_MAX * pctIn / 100);
  ledcWrite(FAN_OUT, (uint32_t)DUTY_MAX * pctOut / 100);
  Serial.printf("[FAN] 독립제어 흡기 duty=%d %% · 배기 duty=%d %%\n", pctIn, pctOut);
}

void handleCmd(const char *cmd) {
  if (cmd[0] >= '0' && cmd[0] < '0' + NLEVEL && cmd[1] == '\0') {
    const Level &l = LEVELS[cmd[0] - '0'];
    applyBoth(l.pct, l.name);
  } else if (cmd[0] == 'd' || cmd[0] == 'D') {
    applyBoth(atoi(cmd + 1), "수동");
  } else if (cmd[0] == 'F' || cmd[0] == 'f') {
    int a, b;
    if (sscanf(cmd + 1, "%d %d", &a, &b) == 2) {
      applyIndep(a, b);
    } else {
      Serial.println("[FAN] ?? F 형식: F <흡기%> <배기%>  예) F 35 0");
    }
  } else if (cmd[0] == '?') {
    Serial.printf("[FAN] 흡기 %s duty=%d %% %lu rpm · 배기 %s duty=%d %% %lu rpm · T=%.1fC RH=%.1f%% PM2.5=%u\n",
                  nameIn, pctIn, (unsigned long)rpmIn, nameOut, pctOut, (unsigned long)rpmOut,
                  S.t, S.rh, (unsigned)S.pm25);
  } else {
    Serial.printf("[FAN] ?? 명령: %s  (0 정지 · 1 약풍 · 2 중풍 · 3 강풍 · d<n> · F <in> <out> · ?)\n", cmd);
  }
}

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (cmdLen) { cmdBuf[cmdLen] = '\0'; handleCmd(cmdBuf); cmdLen = 0; }
    } else if (cmdLen < sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen++] = c;
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);
  ledcAttach(FAN_IN,  PWM_FREQ, PWM_RES);
  ledcAttach(FAN_OUT, PWM_FREQ, PWM_RES);
  pinMode(TACH_IN,  INPUT_PULLUP);   // 오픈 컬렉터 Tach 기준 · 3.3 V 내부 풀업
  pinMode(TACH_OUT, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(TACH_IN),  isrIn,  FALLING);
  attachInterrupt(digitalPinToInterrupt(TACH_OUT), isrOut, FALLING);

  Wire.begin(PIN_SDA, PIN_SCL, 100000);
  bmeOk = bme.begin(0x76, &Wire) || bme.begin(0x77, &Wire);
  if (bmeOk) bme.setSampling(Adafruit_BME280::MODE_FORCED);
  pms.begin(9600, SERIAL_8N1, PIN_PMS_RX, PIN_PMS_TX);

  applyBoth(0, "정지");
  Serial.printf("[FAN] 0 정지 · 1 약풍(35 %%) · 2 중풍(60 %%) · 3 강풍(100 %%) · d<n> 수동 · F <in> <out> 독립제어 · ? 상태 (BME280 %s)\n",
                bmeOk ? "OK" : "감지 안됨");
}

void loop() {
  static uint32_t lastReport = 0;
  readSerial();
  pmsRead();
  uint32_t now = millis();
  if (now - lastReport >= REPORT_MS) {
    lastReport = now;
    // 일반 PC 팬은 회전 1회당 Tach 펄스 2개 → rpm = 펄스/초 × 30
    rpmIn  = tachCntIn  * 30;
    rpmOut = tachCntOut * 30;
    tachCntIn = 0; tachCntOut = 0;

    if (bmeOk) {
      bme.takeForcedMeasurement();
      S.t = bme.readTemperature();
      S.rh = bme.readHumidity();
      S.p = bme.readPressure() / 100.0f;
    }
    bool pmsStale = (millis() - S.pmsAt > PMS_STALE_MS);

    Serial.printf("[FAN] 흡기 %s duty=%3d %% %4lu rpm · 배기 %s duty=%3d %% %4lu rpm · T=%.1fC RH=%.1f%% P=%.0fhPa PM1=%u PM2.5=%u PM10=%u%s\n",
                  nameIn, pctIn, (unsigned long)rpmIn, nameOut, pctOut, (unsigned long)rpmOut,
                  bmeOk ? S.t : -99.0f, bmeOk ? S.rh : -99.0f, bmeOk ? S.p : -99.0f,
                  (unsigned)S.pm1, (unsigned)S.pm25, (unsigned)S.pm10,
                  pmsStale ? " [PMS stale]" : "");
    Serial.printf("DATA,%lu,%d,%lu,%d,%lu,%.2f,%.1f,%.1f,%u,%u,%u,%d,%d\n",
                  (unsigned long)now, pctIn, (unsigned long)rpmIn, pctOut, (unsigned long)rpmOut,
                  bmeOk ? S.t : -99.0f, bmeOk ? S.rh : -99.0f, bmeOk ? S.p : -99.0f,
                  (unsigned)S.pm1, (unsigned)S.pm25, (unsigned)S.pm10,
                  bmeOk ? 0 : 1, pmsStale ? 1 : 0);
  }
}
