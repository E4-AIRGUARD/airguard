// T06_heater — DFR0457 MOSFET 스위치로 가열 패드 PWM 500 Hz + BME280 온도 감시 + 40 ℃ 차단
// 배선: DFR0457 D(신호) ← GPIO32 · +/- ← 3V3/GND · 신호선–GND 사이 10 kΩ 풀다운
//       부하 단자: 5 V 줄 → DFR0457 → 가열 패드 2장 병렬 → GND
// !! 모기향·연기 실험과 절대 동시에 하지 않음 · 사람이 자리를 지킴 · BME280(T03 통과)이 붙어 있어야 실행
// 합격 기준: 30 %에서 60초 동안 온도가 서서히 오르고, 0 %로 내리면 내려감 · T ≥ 40 ℃면 즉시 OFF 유지
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>

Adafruit_BME280 bme;
const int HEAT_PIN = 32;
const uint32_t HEAT_FREQ = 500;   // DFR0457 PWM 상한 1 kHz 이하
const uint8_t  HEAT_RES  = 8;     // 0~255
const float    T_LIMIT   = 40.0f; // 가열 패드 저온형 40 ℃ · 설계 LIMIT
bool latchedOff = false;

void setHeat(int pct) {
  if (latchedOff) pct = 0;
  pct = constrain(pct, 0, 100);
  ledcWrite(HEAT_PIN, 255 * pct / 100);
}

void setup() {
  Serial.begin(115200);
  delay(300);
  ledcAttach(HEAT_PIN, HEAT_FREQ, HEAT_RES);
  setHeat(0);
  Wire.begin(21, 22, 100000);
  if (!bme.begin(0x76, &Wire) && !bme.begin(0x77, &Wire)) {
    Serial.println("[T06] BME280 없음 → 온도 감시 불가 · 가열 시험 중단");
    while (true) delay(1000);
  }
  bme.setSampling(Adafruit_BME280::MODE_FORCED);
  Serial.println("[T06] heater test: 0% 10s → 30% 60s → 0% 60s (온도 매초 출력)");
}

float readT() { bme.takeForcedMeasurement(); return bme.readTemperature(); }

void hold(int pct, int seconds) {
  setHeat(pct);
  for (int s = 0; s < seconds; s++) {
    float t = readT();
    if (isnan(t) || t >= T_LIMIT) {           // 센서 오류도 LIMIT로 취급
      latchedOff = true; setHeat(0);
      Serial.printf("[T06] !! LIMIT T=%.2f -> heater OFF (latched)\n", t);
    }
    Serial.printf("[T06] heat=%d%%  T=%.2f C  t=%ds\n", latchedOff ? 0 : pct, t, s);
    delay(1000);
  }
}

void loop() {
  hold(0, 10);
  hold(30, 60);
  hold(0, 60);
  Serial.println("[T06] cycle done · 다시 반복 (그만두려면 전원 분리)");
}
