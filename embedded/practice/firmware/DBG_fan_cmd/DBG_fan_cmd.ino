// DBG_fan_cmd — BME280·PMS5003·LIMIT 로직 전부 배제, 팬만 시리얼 명령으로 직접 제어 (디버그용, CB-T1 정식 순서 밖)
// 목적: T08이 BME280 고장으로 LIMIT에 갇혀 "C" 명령이 실제로 먹히는지 확인이 안 될 때,
//       명령 → 팬 출력 경로만 독립적으로 검증하기 위함. 상태 판단·안전 로직 없음(디버그 전용, 실사용 금지).
// 배선: 흡기 PWM→18 · 배기 PWM→19 · Tach(선택)→26/27 · 팬 전원은 5V 레일(어댑터), ESP32 5V 핀 아님
// 명령: "C fi fo"  (예 "C 50 50" = 흡기 50% 배기 50%, 만료 없이 다음 명령까지 유지) · "C 0 0"으로 정지
#include <Arduino.h>

const int FAN_IN = 18, FAN_OUT = 19;
const int TACH_IN = 26, TACH_OUT = 27;
const uint32_t PWM_FREQ = 25000;
const uint8_t  PWM_RES  = 11;
const uint32_t DUTY_MAX = (1u << PWM_RES) - 1;

volatile uint32_t tachCntIn = 0, tachCntOut = 0;
void IRAM_ATTR isrIn()  { tachCntIn++; }
void IRAM_ATTR isrOut() { tachCntOut++; }

int fi = 0, fo = 0;

void setFan(int pin, int pct) {
  pct = constrain(pct, 0, 100);
  ledcWrite(pin, (uint32_t)DUTY_MAX * pct / 100);
}

void readLine() {
  static char buf[32]; static int n = 0;
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      buf[n] = 0; n = 0;
      int a, b;
      if (sscanf(buf, "C %d %d", &a, &b) == 2) {
        fi = constrain(a, 0, 100); fo = constrain(b, 0, 100);
        setFan(FAN_IN, fi); setFan(FAN_OUT, fo);
        Serial.printf("[FANCMD] applied fi=%d fo=%d\n", fi, fo);
      }
    } else if (n < 31) buf[n++] = ch;
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);
  ledcAttach(FAN_IN, PWM_FREQ, PWM_RES);
  ledcAttach(FAN_OUT, PWM_FREQ, PWM_RES);
  pinMode(TACH_IN, INPUT_PULLUP);
  pinMode(TACH_OUT, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(TACH_IN),  isrIn,  FALLING);
  attachInterrupt(digitalPinToInterrupt(TACH_OUT), isrOut, FALLING);
  setFan(FAN_IN, 0);
  setFan(FAN_OUT, 0);
  Serial.println("[FANCMD] ready — 'C fi fo' 로 팬 제어 (예: C 50 50 / C 0 0)");
}

void loop() {
  readLine();
  static uint32_t t1000 = 0;
  if (millis() - t1000 >= 1000) {
    t1000 = millis();
    uint32_t rpmIn = tachCntIn * 30, rpmOut = tachCntOut * 30;   // Tach 미연결이면 0 또는(접촉 불량 시) 비정상적으로 큰 값
    tachCntIn = 0; tachCntOut = 0;
    Serial.printf("{\"k\":\"fan\",\"fi\":%d,\"fo\":%d,\"rpmIn\":%lu,\"rpmOut\":%lu}\n", fi, fo, (unsigned long)rpmIn, (unsigned long)rpmOut);
  }
}
