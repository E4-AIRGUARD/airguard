// T08_cleanbox_fw — CB-02 R8.1 통합 뼈대 (ESP32 1대)
//   LED: 단일 적색 LED 대신 신호등 모듈(R13·Y14·G25, 공통 GND) 사용 — NORMAL=초록 · LOCAL=초록 점멸 · WARNING=노랑 점멸 · DANGER/LIMIT=빨강
//   1 s: BME280 + PMS5003 + 팬 Tach(rpm, 선택) 읽어 JSON 두 줄 USB 송신
//   100 ms: 안전 우선순위표 → 팬·가열 적용   (LIMIT > DANGER > WARNING > Jetson 명령 > LOCAL)
//   USB 수신: "C fi fo ht"  (예 "C 20 20 35" = 흡기 20 % 배기 20 % 가열 35 %) · 유효시간 3 s
//             "T pm25"      (예 "T 600" = 시험용 PM2.5 값 주입 · "T -1"로 해제)
//             "E type [note]" (실험 이벤트 마커, 예 "E disturb_humid" · "E fan_set 50" · README「실험 이벤트 스키마」참고)
// 라이브러리: Adafruit BME280 + Adafruit Unified Sensor + Adafruit BusIO · PMS5003은 직접 파싱 · JSON은 printf로 생성
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <HardwareSerial.h>

// ---- 핀 (CB-02 R8.1 G장) ----
const int PIN_SDA = 21, PIN_SCL = 22;
const int PIN_PMS_RX = 16, PIN_PMS_TX = 17;
const int PIN_FAN_IN = 18, PIN_FAN_OUT = 19;
const int PIN_TACH_IN = 26, PIN_TACH_OUT = 27;   // 선택 — 연결 전 멀티미터로 전압 확인(5V면 분압 필요), T05와 동일
const int PIN_HEAT = 32, PIN_BUZZ = 33;
const int PIN_LED_R = 13, PIN_LED_Y = 14, PIN_LED_G = 25;   // 신호등 LED 모듈(공통 GND) · 2026-09-16 단일 LED에서 교체

// ---- 기준 (D·E장 · W2 실측 뒤 조정) ----
const float    T_LIMIT   = 40.0f;
const uint16_t PM_WARN   = 50;
const uint16_t PM_DANGER = 500;
const uint32_t CMD_TTL_MS = 3000;
const uint32_t PMS_STALE_MS = 6000; // PMS5003 안정 모드 출력 간격이 최대 2.3 s → 2회 연속 누락(≈4.6 s)까지 정상으로 봄. 3 s면 체크섬 1회 실패에 LIMIT(팬 100 %·부저)로 튐
const int      HEAT_CAP  = 60;      // 가열 상한 % (안전 여유)

// ---- 임시 테스트 플래그 (2026-09-16) ----
// PMS5003 인터페이스 보드·가열패드 점퍼케이블 배송 대기 중 — 센서 없이 BME280·팬·부저·LED·JSON만 먼저 확인하기 위함.
// PMS_REQUIRED=false면 PMS 무응답을 LIMIT 사유에서 제외(안 그러면 미배선 상태로 6초 뒤 영구 LIMIT·팬100%에 갇힘).
// !! PMS5003 배선 완료 후에는 반드시 true로 되돌릴 것 — false로 두고 실사용하면 연기 감지가 죽은 채로 동작함.
const bool PMS_REQUIRED = false;   // TODO: PMS5003 배선 완료 후 true로 복구

Adafruit_BME280 bme;
HardwareSerial pms(2);

volatile uint32_t tachCntIn = 0, tachCntOut = 0;
void IRAM_ATTR isrTachIn()  { tachCntIn++; }
void IRAM_ATTR isrTachOut() { tachCntOut++; }

struct Sens { float t = NAN, rh = NAN, p = NAN; uint16_t pm1 = 0, pm25 = 0, pm10 = 0; uint8_t err = 0; uint32_t pmsAt = 0; } S;
struct Cmd  { int fi = 0, fo = 0, ht = 0; uint32_t at = 0; } C;
int testPm25 = -1;
enum State { NORMAL, LOCAL, WARNING, DANGER, LIMIT } st = LOCAL;
const char *stName[] = {"NORMAL", "LOCAL", "WARNING", "DANGER", "LIMIT"};
int outFi = 0, outFo = 0, outHt = 0;
uint32_t seq = 0;

// ---- PMS5003 (T04와 동일) ----
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
  const uint32_t FAN_MAX = 2047;
  ledcWrite(PIN_FAN_IN,  FAN_MAX * outFi / 100);
  ledcWrite(PIN_FAN_OUT, FAN_MAX * outFo / 100);
  ledcWrite(PIN_HEAT,    255 * outHt / 100);
  bool alarm = (st == DANGER || st == LIMIT);
  bool blink = (millis() / 500) & 1;        // 0.5 s 간격 — WARNING·LOCAL용
  bool blinkFast = (millis() / 200) & 1;    // 0.2 s 간격 — 부저 미채택으로 DANGER·LIMIT을 더 눈에 띄게 함
  digitalWrite(PIN_LED_R, alarm && blinkFast);                        // DANGER·LIMIT = 빨강 빠른 점멸(0.2 s)
  digitalWrite(PIN_LED_Y, st == WARNING && blink);                    // WARNING = 노랑 점멸(0.5 s)
  digitalWrite(PIN_LED_G, st == NORMAL || (st == LOCAL && blink));    // NORMAL = 초록 상시 · LOCAL = 초록 점멸(0.5 s)
  digitalWrite(PIN_BUZZ, alarm);   // 부저 미채택(배선 불량, LED 단독으로 대체) · 신호 자체는 유지(무해)
}

void decide() {
  uint16_t pm = (testPm25 >= 0) ? (uint16_t)testPm25 : S.pm25;
  bool pmsStale = PMS_REQUIRED && (millis() - S.pmsAt > PMS_STALE_MS);
  bool cmdFresh = C.at && (millis() - C.at <= CMD_TTL_MS);
  if (isnan(S.t) || S.t >= T_LIMIT || pmsStale)   { st = LIMIT;   outFi = 100; outFo = 100; outHt = 0; }
  else if (pm >= PM_DANGER)                        { st = DANGER;  outFi = 100; outFo = 100; outHt = 0; }
  else if (pm > PM_WARN)                           { st = WARNING; outFi = 50;  outFo = 50;  outHt = 0; }
  else if (cmdFresh)                               { st = NORMAL;  outFi = C.fi; outFo = C.fo; outHt = min(C.ht, HEAT_CAP); }
  else                                             { st = LOCAL;   outFi = 20;  outFo = 20;  outHt = 0; }  // 명령 없음 → 순환만
  applyOut();
}

void readLine() {
  static char buf[64]; static int n = 0;
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      buf[n] = 0; n = 0;
      int a, b, c;
      if (sscanf(buf, "C %d %d %d", &a, &b, &c) == 3) { C.fi = constrain(a, 0, 100); C.fo = constrain(b, 0, 100); C.ht = constrain(c, 0, 100); C.at = millis(); }
      else if (sscanf(buf, "T %d", &a) == 1) testPm25 = a;
      else if (buf[0] == 'E' && buf[1] == ' ') {
        // E <type> [note...] — 실험 이벤트 마커. type 목록은 README「실험 이벤트 스키마」 참고.
        // 지금 없는 센서(PMS5003·가열패드·비전)용 type도 코드 변경 없이 그대로 찍힘 — 나중에 그 하드웨어가 붙으면 같은 명령을 그 시점에 보내면 됨.
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
  pinMode(PIN_LED_R, OUTPUT); pinMode(PIN_LED_Y, OUTPUT); pinMode(PIN_LED_G, OUTPUT); pinMode(PIN_BUZZ, OUTPUT);
  ledcAttach(PIN_FAN_IN, 25000, 11); ledcAttach(PIN_FAN_OUT, 25000, 11);
  ledcAttach(PIN_HEAT, 500, 8);
  ledcWrite(PIN_HEAT, 0);
  Wire.begin(PIN_SDA, PIN_SCL, 100000);
  if (bme.begin(0x76, &Wire) || bme.begin(0x77, &Wire)) bme.setSampling(Adafruit_BME280::MODE_FORCED);
  else S.err |= 1;
  pms.begin(9600, SERIAL_8N1, PIN_PMS_RX, PIN_PMS_TX);
  pinMode(PIN_TACH_IN, INPUT_PULLUP); pinMode(PIN_TACH_OUT, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_TACH_IN),  isrTachIn,  FALLING);
  attachInterrupt(digitalPinToInterrupt(PIN_TACH_OUT), isrTachOut, FALLING);
  Serial.printf("{\"k\":\"boot\",\"fw\":\"T08\",\"pins\":\"sda21 scl22 pms16 fan18/19 tach26/27 heat32 buzz33 ledR13/Y14/G25\",\"pmsRequired\":%s}\n", PMS_REQUIRED ? "true" : "false");
}

void loop() {
  static uint32_t t100 = 0, t1000 = 0;
  readLine();
  pmsRead();
  if (millis() - t100 >= 100) { t100 = millis(); decide(); }
  if (millis() - t1000 >= 1000) {
    t1000 = millis();
    if (!(S.err & 1)) { bme.takeForcedMeasurement(); S.t = bme.readTemperature(); S.rh = bme.readHumidity(); S.p = bme.readPressure(); }
    uint8_t err = S.err | ((millis() - S.pmsAt > PMS_STALE_MS) ? 2 : 0);
    // Tach: 이 1초 동안 쌓인 펄스 수를 읽고 즉시 리셋 (일반 PC 팬은 회전 1회당 펄스 2개 → rpm = 펄스/초 × 30). Tach 미연결이면 0.
    uint32_t rpmIn = tachCntIn * 30, rpmOut = tachCntOut * 30;
    tachCntIn = 0; tachCntOut = 0;
    // NaN은 JSON에 없으므로 센서 오류 시 -99로 표시 (err 비트 1과 함께 봄)
    Serial.printf("{\"k\":\"sens\",\"seq\":%lu,\"t\":%.2f,\"rh\":%.1f,\"p\":%.0f,\"pm1\":%u,\"pm25\":%u,\"pm10\":%u,\"err\":%u}\n",
                  (unsigned long)seq, isnan(S.t) ? -99.0f : S.t, isnan(S.rh) ? -99.0f : S.rh, isnan(S.p) ? -99.0f : S.p,
                  (unsigned)S.pm1, (unsigned)S.pm25, (unsigned)S.pm10, (unsigned)err);
    Serial.printf("{\"k\":\"stat\",\"seq\":%lu,\"st\":\"%s\",\"fi\":%d,\"fo\":%d,\"ht\":%d,\"rpmIn\":%lu,\"rpmOut\":%lu,\"test\":%d}\n",
                  (unsigned long)seq, stName[st], outFi, outFo, outHt, (unsigned long)rpmIn, (unsigned long)rpmOut, testPm25);
    seq++;
  }
}
