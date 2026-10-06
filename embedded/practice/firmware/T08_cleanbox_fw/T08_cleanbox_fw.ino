// T08_cleanbox_fw — Jetson Orin Nano 연동용 통합 펌웨어 (ESP32 1대)
//   1 s: BME280 + PMS5003 + 팬 Tach(rpm) 읽어 JSON 두 줄 USB 송신
//   100 ms: 안전 우선순위표 → 팬 적용   (LIMIT > DANGER > WARNING > Jetson 명령 > LOCAL)
//   USB 수신: "C fi fo"     (예 "C 20 20" = 흡기 20 % 배기 20 %) · 유효시간 3 s
//             "T pm25"      (예 "T 600" = 시험용 PM2.5 값 주입 · "T -1"로 해제)
//             "E type [note]" (실험 이벤트 마커, 예 "E supply_start" · "E supply_end" ·
//                              EXPERIMENT_PLAN.md 2절의 공급 시작·종료 마커로 사용)
// 가열패드·부저·LED는 배선하지 않기로 함(2026-09-28) — 관련 코드 전부 제거.
// 라이브러리: Adafruit BME280 + Adafruit Unified Sensor + Adafruit BusIO · PMS5003은 직접 파싱 · JSON은 printf로 생성
//
// 핀맵은 embedded/new_fan/new_fan_t/new_fan_t.ino에서 검증된 것과 동일(2026-09-27 확정):
//   팬 PWM(노란선)  흡기 → 13 · 배기 → 19
//   팬 Tach(파란선) 흡기 → 5 (GPIO32는 흡기+배기 동시 구동 시 rpm 노이즈 있어 이동, GPIO5는
//                            부팅 스트래핑 핀이지만 Tach는 입력이라 실사용 문제 없음 확인됨) · 배기 → 33
//   BME280   SDA(파란선) → 21 · SCL(노란선) → 23 (I2C, 0x76/0x77 자동 탐색)
//   PMS5003  RX(ESP32측, PMS TX에 연결) → 14 · TX(ESP32측, PMS RX에 연결) → 27 (UART2, 9600bps)
//   !! Tach를 GPIO34·35·36·39에 꽂지 말 것(입력 전용, 풀업 없어 노이즈만 읽힘).
//   !! GPIO2는 PMS UART에 쓰지 말 것(부팅 스트래핑, 업로드 막힘).
//   !! GND는 ESP32·팬·BME280·PMS5003 전부 공통으로 묶을 것.
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <HardwareSerial.h>

// ---- 핀 ----
const int PIN_SDA = 21, PIN_SCL = 23;
const int PIN_PMS_RX = 14, PIN_PMS_TX = 27;
const int PIN_FAN_IN = 13, PIN_FAN_OUT = 19;
const int PIN_TACH_IN = 5, PIN_TACH_OUT = 33;
const uint32_t PWM_FREQ = 25000;   // 4핀 PC 팬 규격 25 kHz — new_fan_t와 동일
const uint8_t  PWM_RES  = 11;
const uint32_t DUTY_MAX = (1u << PWM_RES) - 1;

// 25 kHz PWM 스위칭이 Tach 선에 실려 가짜 엣지가 섞인다(new_fan_t에서 실측 확인).
// 진짜 펄스 간격은 7000 rpm에서도 4.3 ms라, 1 ms보다 빨리 온 엣지는 노이즈로 버린다.
const uint32_t MIN_PULSE_US = 1000;

// ---- 기준 (실측 뒤 조정) ----
const float    T_LIMIT   = 40.0f;
const uint16_t PM_WARN   = 50;
const uint16_t PM_DANGER = 500;
const uint32_t CMD_TTL_MS = 3000;
const uint32_t PMS_STALE_MS = 6000; // PMS5003 안정 모드 출력 간격이 최대 2.3 s → 2회 연속 누락(≈4.6 s)까지 정상으로 봄
const bool PMS_REQUIRED = true;     // PMS5003 배선·동작 확인 완료(2026-09-27) — 무응답 시 LIMIT으로 감

Adafruit_BME280 bme;
bool bmeOk = false;
HardwareSerial pms(2);

volatile uint32_t tachCntIn = 0, tachCntOut = 0;
volatile uint32_t lastEdgeIn = 0, lastEdgeOut = 0;
void IRAM_ATTR isrTachIn() {
  uint32_t now = micros();
  if (now - lastEdgeIn < MIN_PULSE_US) return;
  lastEdgeIn = now;
  tachCntIn++;
}
void IRAM_ATTR isrTachOut() {
  uint32_t now = micros();
  if (now - lastEdgeOut < MIN_PULSE_US) return;
  lastEdgeOut = now;
  tachCntOut++;
}

struct Sens { float t = NAN, rh = NAN, p = NAN; uint16_t pm1 = 0, pm25 = 0, pm10 = 0; uint8_t err = 0; uint32_t pmsAt = 0; } S;
struct Cmd  { int fi = 0, fo = 0; uint32_t at = 0; } C;
int testPm25 = -1;
enum State { NORMAL, LOCAL, WARNING, DANGER, LIMIT } st = LOCAL;
const char *stName[] = {"NORMAL", "LOCAL", "WARNING", "DANGER", "LIMIT"};
int outFi = 0, outFo = 0;
uint32_t seq = 0;

// ---- PMS5003 (T04·new_fan_t와 동일 파싱) ----
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

void applyOut() {
  ledcWrite(PIN_FAN_IN,  DUTY_MAX * outFi / 100);
  ledcWrite(PIN_FAN_OUT, DUTY_MAX * outFo / 100);
}

void decide() {
  uint16_t pm = (testPm25 >= 0) ? (uint16_t)testPm25 : S.pm25;
  bool pmsStale = PMS_REQUIRED && (millis() - S.pmsAt > PMS_STALE_MS);
  bool cmdFresh = C.at && (millis() - C.at <= CMD_TTL_MS);
  if (isnan(S.t) || S.t >= T_LIMIT || pmsStale)   { st = LIMIT;   outFi = 100; outFo = 100; }
  else if (pm >= PM_DANGER)                        { st = DANGER;  outFi = 100; outFo = 100; }
  else if (pm > PM_WARN)                           { st = WARNING; outFi = 60;  outFo = 60;  }
  else if (cmdFresh)                               { st = NORMAL;  outFi = C.fi; outFo = C.fo; }
  else                                             { st = LOCAL;   outFi = 20;  outFo = 20;  }  // 명령 없음 → 순환만
  applyOut();
}

void readLine() {
  static char buf[64]; static int n = 0;
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      buf[n] = 0; n = 0;
      int a, b;
      if (sscanf(buf, "C %d %d", &a, &b) == 2) { C.fi = constrain(a, 0, 100); C.fo = constrain(b, 0, 100); C.at = millis(); }
      else if (sscanf(buf, "T %d", &a) == 1) testPm25 = a;
      else if (buf[0] == 'E' && buf[1] == ' ') {
        // E <type> [note...] — 실험 이벤트 마커. EXPERIMENT_PLAN.md 2절 공급 시작·종료 등에 사용.
        char *type = buf + 2;
        while (*type == ' ') type++;
        char *note = type;
        while (*note && *note != ' ') note++;
        if (*note) { *note = 0; note++; while (*note == ' ') note++; }
        Serial.printf("{\"k\":\"evt\",\"seq\":%lu,\"ms\":%lu,\"type\":\"%s\",\"note\":\"%s\"}\n",
                      (unsigned long)seq, (unsigned long)millis(), type, note);
      }
    } else if (n < 63) buf[n++] = ch;
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);
  ledcAttach(PIN_FAN_IN,  PWM_FREQ, PWM_RES);
  ledcAttach(PIN_FAN_OUT, PWM_FREQ, PWM_RES);
  pinMode(PIN_TACH_IN,  INPUT_PULLUP);   // 오픈 컬렉터 Tach 기준 · 3.3 V 내부 풀업
  pinMode(PIN_TACH_OUT, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_TACH_IN),  isrTachIn,  FALLING);
  attachInterrupt(digitalPinToInterrupt(PIN_TACH_OUT), isrTachOut, FALLING);

  Wire.begin(PIN_SDA, PIN_SCL, 100000);
  delay(50);   // 콜드부팅 직후 I2C 첫 트랜잭션이 가끔 실패해서 약간의 정착 시간 + 재시도를 둠
  for (int attempt = 0; attempt < 5 && !bmeOk; attempt++) {
    bmeOk = bme.begin(0x76, &Wire) || bme.begin(0x77, &Wire);
    if (!bmeOk) delay(50);
  }
  if (bmeOk) bme.setSampling(Adafruit_BME280::MODE_FORCED);
  else S.err |= 1;
  pms.begin(9600, SERIAL_8N1, PIN_PMS_RX, PIN_PMS_TX);

  Serial.printf("{\"k\":\"boot\",\"fw\":\"T08\",\"pins\":\"sda21 scl23 pms14/27 fan13/19 tach5/33\",\"pmsRequired\":%s,\"bme\":%s}\n",
                PMS_REQUIRED ? "true" : "false", bmeOk ? "true" : "false");
}

void loop() {
  static uint32_t t100 = 0, t1000 = 0;
  readLine();
  pmsRead();
  if (millis() - t100 >= 100) { t100 = millis(); decide(); }
  if (millis() - t1000 >= 1000) {
    t1000 = millis();
    if (bmeOk) {
      bme.takeForcedMeasurement();
      float t = bme.readTemperature(), rh = bme.readHumidity(), p = bme.readPressure() / 100.0f;
      // I2C 노이즈로 깨진 읽기(예: 183C·습도100%·음압) 필터 — 물리적으로 말이 안 되면 이번 샘플은 버림
      bool valid = !(t < -40 || t > 85 || rh < 0 || rh > 100 || p < 300 || p > 1100);
      if (valid) { S.t = t; S.rh = rh; S.p = p; S.err &= ~(uint8_t)4; }
      else S.err |= 4;
    }
    uint8_t err = S.err | ((millis() - S.pmsAt > PMS_STALE_MS) ? 2 : 0);
    // Tach: 이 1초 동안 쌓인 펄스 수를 읽고 즉시 리셋 (일반 PC 팬은 회전 1회당 펄스 2개 → rpm = 펄스/초 × 30)
    uint32_t rpmIn = tachCntIn * 30, rpmOut = tachCntOut * 30;
    tachCntIn = 0; tachCntOut = 0;
    // NaN은 JSON에 없으므로 센서 오류 시 -99로 표시 (err 비트 1=BME 미감지·4=읽기값 이상과 함께 봄)
    Serial.printf("{\"k\":\"sens\",\"seq\":%lu,\"t\":%.2f,\"rh\":%.1f,\"p\":%.0f,\"pm1\":%u,\"pm25\":%u,\"pm10\":%u,\"err\":%u}\n",
                  (unsigned long)seq, isnan(S.t) ? -99.0f : S.t, isnan(S.rh) ? -99.0f : S.rh, isnan(S.p) ? -99.0f : S.p,
                  (unsigned)S.pm1, (unsigned)S.pm25, (unsigned)S.pm10, (unsigned)err);
    Serial.printf("{\"k\":\"stat\",\"seq\":%lu,\"st\":\"%s\",\"fi\":%d,\"fo\":%d,\"rpmIn\":%lu,\"rpmOut\":%lu,\"test\":%d}\n",
                  (unsigned long)seq, stName[st], outFi, outFo, (unsigned long)rpmIn, (unsigned long)rpmOut, testPm25);
    seq++;
  }
}
