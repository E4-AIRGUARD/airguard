// new_fan_t_diag — 살아있는 Tach(33)를 피드백으로 써서 PWM이 실제로 속도를 바꾸는지 판정
// 배선: PWM 흡기=18 · 배기=19 / Tach 배기=33 (흡기 Tach 35는 내부 풀업이 없어 못 읽는다 → 32로 옮길 것)
//
// 읽는 법: 18만 올렸을 때 rpm이 변하면 18이 이 팬의 PWM이고,
//          19만 올렸을 때 변하면 19다. 둘 다 올려도 rpm이 그대로면 PWM 선이 팬에 안 닿아 있다.
const int FAN_IN = 18, FAN_OUT = 19;
const int TACH_OUT = 33;
const uint32_t PWM_FREQ = 25000;
const uint8_t  PWM_RES  = 11;
const uint32_t DUTY_MAX = (1u << PWM_RES) - 1;

int pctIn = 0, pctOut = 0;
volatile uint32_t tachCnt = 0;
void IRAM_ATTR isrTach() { tachCnt = tachCnt + 1; }

char cmdBuf[16];
uint8_t cmdLen = 0;

void setFan(int pin, int &slot, int pct) {
  slot = constrain(pct, 0, 100);
  ledcWrite(pin, (uint32_t)DUTY_MAX * slot / 100);
}

void handleCmd(const char *cmd) {
  switch (cmd[0]) {
    case 'i': case 'I':
      setFan(FAN_IN, pctIn, atoi(cmd + 1));
      Serial.printf("[DIAG] 흡기18 duty=%d %%\n", pctIn);
      break;
    case 'o': case 'O':
      setFan(FAN_OUT, pctOut, atoi(cmd + 1));
      Serial.printf("[DIAG] 배기19 duty=%d %%\n", pctOut);
      break;
    case 's': case 'S':
      setFan(FAN_IN, pctIn, 0); setFan(FAN_OUT, pctOut, 0);
      Serial.println("[DIAG] 둘 다 0 %");
      break;
    default:
      if (isdigit((unsigned char)cmd[0])) {
        int pct = atoi(cmd);
        setFan(FAN_IN, pctIn, pct);
        setFan(FAN_OUT, pctOut, pct);
        Serial.printf("[DIAG] 둘 다 duty=%d %%\n", pct);
      }
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
  pinMode(TACH_OUT, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(TACH_OUT), isrTach, FALLING);
  setFan(FAN_IN, pctIn, 0);
  setFan(FAN_OUT, pctOut, 0);
  Serial.println("[DIAG] PWM 18/19 · Tach 33 — 명령: 0~100 · i<n> · o<n> · s");
}

void loop() {
  static uint32_t last = 0;
  readSerial();
  uint32_t now = millis();
  if (now - last >= 1000) {
    last = now;
    uint32_t p = tachCnt;
    tachCnt = 0;
    // 회전 1회당 Tach 펄스 2개 → rpm = 펄스/초 × 30
    Serial.printf("[DIAG] 흡기18=%3d %% 배기19=%3d %% | tach33 펄스 %lu → rpm %lu\n",
                  pctIn, pctOut, (unsigned long)p, (unsigned long)(p * 30));
  }
}
