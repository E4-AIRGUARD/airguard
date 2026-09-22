// new_fan_t_wirecheck — 팬 선이 실제로 어느 GPIO에 붙어 있는지 전수 조사
//
// 쓰는 법: 팬에 전원을 넣어 돌아가는 상태로 두고 이 스케치를 올린다.
//   · "뭔가 붙음"으로 뜨는 핀  → 팬 PWM 입력(파란선). 팬 내부 풀업 때문에 풀다운을 이긴다.
//   · "펄스 나옴"으로 뜨는 핀  → 팬 Tach 출력(녹색선). 회전 1회당 2펄스.
//   · 아무 데도 안 뜨면 그 선은 팬까지 이어져 있지 않다(점퍼 헐거움·다른 줄·GND 미공통).
//
// 제외한 핀: 0·2·12·15(부팅 스트랩), 6~11(내장 플래시), 1·3(USB 시리얼).
const int PINS[] = {4, 5, 13, 14, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33, 34, 35, 36, 39};
const int NPIN = sizeof(PINS) / sizeof(PINS[0]);
const uint32_t COUNT_MS = 3000;

volatile uint32_t pulses[NPIN];

bool hasPulls(int pin) { return !(pin == 34 || pin == 35 || pin == 36 || pin == 39); }

void IRAM_ATTR onEdge(void *arg) {
  volatile uint32_t *c = (volatile uint32_t *)arg;
  *c = *c + 1;
}

void scanLevels() {
  Serial.println("[1] 핀별 연결 상태");
  bool any = false;
  for (int i = 0; i < NPIN; i++) {
    int pin = PINS[i];
    if (!hasPulls(pin)) {
      pinMode(pin, INPUT);
      delay(5);
      int hi = 0;
      for (int k = 0; k < 20; k++) { if (digitalRead(pin)) hi++; delay(1); }
      Serial.printf("   %2d: 입력전용 — HIGH %d/20 (내부 풀업이 없어 판정 불가, 참고용)\n", pin, hi);
      continue;
    }
    pinMode(pin, INPUT_PULLUP);   delay(5); int up = digitalRead(pin);
    pinMode(pin, INPUT_PULLDOWN); delay(5); int dn = digitalRead(pin);
    pinMode(pin, INPUT);
    if (dn == HIGH) { Serial.printf("   %2d: >> 뭔가 붙음 (풀다운을 이기고 HIGH) — 팬 PWM 입력일 가능성\n", pin); any = true; }
    else if (up == LOW) { Serial.printf("   %2d: >> GND에 물림\n", pin); any = true; }
  }
  if (!any) Serial.println("   (전부 떠 있음 — 붙어 있는 선이 하나도 없다)");
}

void scanPulses() {
  Serial.printf("[2] %lu초간 펄스 세기 — 팬이 도는 중이어야 의미가 있다\n", (unsigned long)(COUNT_MS / 1000));
  for (int i = 0; i < NPIN; i++) {
    pulses[i] = 0;
    pinMode(PINS[i], hasPulls(PINS[i]) ? INPUT_PULLUP : INPUT);
  }
  for (int i = 0; i < NPIN; i++)
    attachInterruptArg(digitalPinToInterrupt(PINS[i]), onEdge, (void *)&pulses[i], FALLING);
  delay(COUNT_MS);
  for (int i = 0; i < NPIN; i++) detachInterrupt(digitalPinToInterrupt(PINS[i]));

  bool any = false;
  uint32_t sec = COUNT_MS / 1000;
  for (int i = 0; i < NPIN; i++) {
    if (pulses[i] == 0) continue;
    any = true;
    Serial.printf("   %2d: 펄스 %lu (rpm 환산 %lu)%s\n", PINS[i],
                  (unsigned long)pulses[i], (unsigned long)(pulses[i] * 30 / sec),
                  pulses[i] > 2000 ? "  ← 너무 많다, 노이즈일 수 있음" : "  ← Tach 후보");
  }
  if (!any) Serial.println("   (어느 핀에서도 펄스 없음 — 팬이 멈췄거나 Tach 선이 안 닿아 있다)");
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println("[WIRE] 팬 선 찾기 — 팬을 돌아가는 상태로 두세요. 10초마다 반복합니다.");
}

void loop() {
  Serial.println("======== 측정 ========");
  scanLevels();
  scanPulses();
  Serial.println();
  delay(5000);
}
