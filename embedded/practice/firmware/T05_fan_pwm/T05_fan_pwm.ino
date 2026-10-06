// T05_fan_pwm — 흡기(GPIO18) · 배기(GPIO19) 팬 PWM 25 kHz 11비트 + 회전수(Tach, 선택)
// 배선: 팬 Vcc·GND → 브레드보드 5 V 줄 (ESP32 5V 핀 아님) · PWM → 18/19 · GND 공통
//       Tach(있으면) → 26/27 · 연결 전에 멀티미터로 Tach 선 전압 확인 → 5 V가 걸리면 분압(10k+20k) 뒤 연결
// Arduino-ESP32 3.x API: ledcAttach(pin, freq, res) / ledcWrite(pin, duty)
// 합격 기준: 0 → 50 → 100 % 순서로 소리·바람이 바뀜 · Tach가 있으면 rpm이 duty에 비례
// 시리얼 명령: 's' = 즉시 정지(팬 0 %, 진행 중인 단계에서 그대로 멈춤) · 'r' = 정지한 지점부터 재개
const int FAN_IN = 18, FAN_OUT = 19;
const int TACH_IN = 26, TACH_OUT = 27;
const uint32_t PWM_FREQ = 25000;   // 4핀 PC 팬 규격 25 kHz
const uint8_t  PWM_RES  = 11;      // 80 MHz / 25 kHz = 3200 → 11비트(2048)까지 가능
const uint32_t DUTY_MAX = (1u << PWM_RES) - 1;

volatile uint32_t tachCntIn = 0, tachCntOut = 0;
void IRAM_ATTR isrIn()  { tachCntIn++; }
void IRAM_ATTR isrOut() { tachCntOut++; }

bool stopped = false;
int  curPct = 0;

void setFan(int pin, int pct) {
  pct = constrain(pct, 0, 100);
  ledcWrite(pin, (uint32_t)DUTY_MAX * pct / 100);
}

void applyFans(int pct) {           // 현재 목표 duty를 기억해 뒀다가 재개 시 그대로 복원
  curPct = pct;
  setFan(FAN_IN, pct);
  setFan(FAN_OUT, pct);
}

void checkSerial() {                // 's' 정지 / 'r' 재개 — 언제든 즉시 반응하도록 자주 호출
  while (Serial.available()) {
    char c = Serial.read();
    if ((c == 's' || c == 'S') && !stopped) {
      stopped = true;
      setFan(FAN_IN, 0); setFan(FAN_OUT, 0);
      Serial.println("[T05] !! STOP -> 팬 0 % (재개: 'r')");
    } else if ((c == 'r' || c == 'R') && stopped) {
      stopped = false;
      applyFans(curPct);           // 정지 전 duty로 복귀
      Serial.printf("[T05] >> RESUME duty=%d %%\n", curPct);
    }
  }
}

void waitMs(uint32_t total) {       // 정지 중에는 시간이 안 흐르게 일시정지 — 재개하면 남은 시간부터 이어감
  uint32_t last = millis();
  int32_t remain = (int32_t)total;
  while (remain > 0) {
    checkSerial();
    uint32_t now = millis();
    if (!stopped) remain -= (int32_t)(now - last);
    last = now;
    delay(20);
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);
  bool a = ledcAttach(FAN_IN,  PWM_FREQ, PWM_RES);
  bool b = ledcAttach(FAN_OUT, PWM_FREQ, PWM_RES);
  Serial.printf("[T05] ledcAttach in=%d out=%d (1=OK)\n", a, b);
  pinMode(TACH_IN,  INPUT_PULLUP);   // 오픈 컬렉터 Tach 기준 · 3.3 V 풀업
  pinMode(TACH_OUT, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(TACH_IN),  isrIn,  FALLING);
  attachInterrupt(digitalPinToInterrupt(TACH_OUT), isrOut, FALLING);
  applyFans(0);
  Serial.println("[T05] 's' 입력 -> 정지 · 'r' 입력 -> 재개");
}

void loop() {
  const int steps[] = {0, 50, 100, 50, 0};
  for (int pct : steps) {
    while (stopped) { checkSerial(); delay(20); }   // 정지 중이면 같은 단계에서 대기(다음 단계로 건너뛰지 않음)
    applyFans(pct);
    Serial.printf("[T05] duty=%d %% ... 5초 유지\n", pct);
    for (int s = 0; s < 5; s++) {
      tachCntIn = 0; tachCntOut = 0;
      waitMs(1000);
      // 일반 PC 팬은 회전 1회당 Tach 펄스 2개 → rpm = 펄스/초 × 60 / 2
      uint32_t rpmIn = tachCntIn * 30, rpmOut = tachCntOut * 30;
      Serial.printf("[T05]   rpm in=%lu out=%lu  (Tach 미연결이면 0 또는 잡음)\n", (unsigned long)rpmIn, (unsigned long)rpmOut);
    }
  }
  Serial.println("[T05] PWM 선을 뽑았을 때 팬이 최대로 도는지도 한 번 확인 (재부팅 중 동작 가정)");
}
