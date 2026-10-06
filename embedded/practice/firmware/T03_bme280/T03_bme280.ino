// T03_bme280 — 온도 · 습도 · 기압 1초 읽기 (강제 측정 모드)
// 라이브러리: Adafruit BME280 Library + Adafruit Unified Sensor + Adafruit BusIO (라이브러리 매니저 · 「Install all」)
// 합격 기준: 실온 근처 온도(예 20~30 ℃) · 습도 20~80 % · 기압 950~1050 hPa가 1초마다 갱신
//           센서에 손가락을 대면 온도가 오르고 떼면 내려가야 함
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>

Adafruit_BME280 bme;
const int SDA_PIN = 21, SCL_PIN = 23;

void setup() {
  Serial.begin(115200);
  delay(300);
  Wire.begin(SDA_PIN, SCL_PIN, 100000);
  bool ok = bme.begin(0x77, &Wire);                // 이 보드는 T02로 0x77 확인됨 — 먼저 시도
  if (!ok) ok = bme.begin(0x76, &Wire);           // Waveshare 보드는 점퍼로 주소가 바뀔 수 있음
  if (!ok) {
    Serial.println("[T03] BME280 not found (0x76/0x77) -> T02로 주소 확인");
    while (true) delay(1000);
  }
  // 강제 측정: 필요할 때만 한 번 재고 잠듦 → 자기 발열이 적어 챔버 온도 제어에 유리
  bme.setSampling(Adafruit_BME280::MODE_FORCED,
                  Adafruit_BME280::SAMPLING_X1,   // 온도
                  Adafruit_BME280::SAMPLING_X1,   // 기압
                  Adafruit_BME280::SAMPLING_X1,   // 습도
                  Adafruit_BME280::FILTER_OFF);
  Serial.println("[T03] BME280 ready (forced mode)");
}

void loop() {
  bme.takeForcedMeasurement();
  float t = bme.readTemperature();        // ℃
  float rh = bme.readHumidity();          // %
  float p = bme.readPressure() / 100.0f;  // hPa
  bool bad = isnan(t) || isnan(rh) || isnan(p) || t < -40 || t > 85;
  Serial.printf("[T03] T=%.2f C  RH=%.1f %%  P=%.1f hPa %s\n", t, rh, p, bad ? "<< ERR" : "");
  delay(1000);
}
