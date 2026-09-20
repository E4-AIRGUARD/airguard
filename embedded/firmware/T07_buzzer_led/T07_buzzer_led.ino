// T07_buzzer_led — 부저(GPIO33) · 신호등 LED 모듈(공통 GND, R=13 · Y=14 · G=25) 확인 · 부저 능동/수동 판별
// 배선: 모듈 GND → 공통 GND · R → P13 · Y → P14 · G → P25 (모듈에 저항 내장 안 돼 있으면 각 선에 220~330 Ω 직렬)
// 합격 기준: 0) R→Y→G가 1초씩 순서대로 점등 · (A) digitalWrite HIGH 구간에서 소리가 나면 능동형
//           (B) 2 kHz 톤 구간에서만 소리가 나면 수동형
const int BUZZ = 33, LED_R = 13, LED_Y = 14, LED_G = 25;

void allOff() {
  digitalWrite(LED_R, LOW);
  digitalWrite(LED_Y, LOW);
  digitalWrite(LED_G, LOW);
}

void setup() {
  Serial.begin(115200);
  delay(300);
  pinMode(LED_R, OUTPUT);
  pinMode(LED_Y, OUTPUT);
  pinMode(LED_G, OUTPUT);
  pinMode(BUZZ, OUTPUT);
  digitalWrite(BUZZ, LOW);
  allOff();
}

void loop() {
  // 0) 신호등 3색 배선 확인
  Serial.println("[T07] 0) R -> Y -> G 1초씩 점등 (배선·색 확인)");
  allOff(); digitalWrite(LED_R, HIGH); delay(1000);
  allOff(); digitalWrite(LED_Y, HIGH); delay(1000);
  allOff(); digitalWrite(LED_G, HIGH); delay(1000);
  allOff();

  // (A) 능동형 판별: 단순 ON 2초 (빨강으로 점멸 표시)
  Serial.println("[T07] A) digitalWrite HIGH 2s  -> 소리 나면 능동형");
  pinMode(BUZZ, OUTPUT); digitalWrite(BUZZ, HIGH);
  for (int i = 0; i < 4; i++) { digitalWrite(LED_R, i & 1); delay(500); }
  digitalWrite(BUZZ, LOW);
  allOff();
  delay(1000);

  // (B) 수동형 판별: LEDC 2 kHz 톤 2초 (3.x: ledcAttach 후 duty 50 %) (초록으로 점멸 표시)
  Serial.println("[T07] B) 2 kHz tone 2s        -> 여기서만 소리 나면 수동형");
  ledcAttach(BUZZ, 2000, 8);
  ledcWrite(BUZZ, 128);
  for (int i = 0; i < 4; i++) { digitalWrite(LED_G, i & 1); delay(500); }
  ledcWrite(BUZZ, 0);
  ledcDetach(BUZZ);
  allOff();
  delay(2000);
}
