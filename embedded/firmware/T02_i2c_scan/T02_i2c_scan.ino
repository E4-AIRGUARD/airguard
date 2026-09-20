// T02_i2c_scan — I2C 버스(SDA 21 · SCL 22) 스캔
// 합격 기준: BME280이 0x76(또는 0x77)으로 잡히고 chipID=0x60 · (선택) ACD10은 레벨 컨버터 뒤 0x2A
#include <Wire.h>

const int SDA_PIN = 21, SCL_PIN = 22;

// Bosch 칩 ID 레지스터(0xD0): 0x60 = BME280 · 0x56/0x57/0x58 = BMP280(습도 없음) · 0x61 = BME680
// Adafruit BME280 라이브러리는 0x60만 받아들이므로, BMP280이 오면 T03·T06·T08이 "not found"로 멈춘다.
void printBoschId(uint8_t addr) {
  Wire.beginTransmission(addr);
  Wire.write(0xD0);
  if (Wire.endTransmission(false) != 0 || Wire.requestFrom((int)addr, 1) != 1) {
    Serial.print("  <- 0x76/0x77 응답은 있으나 칩 ID 읽기 실패");
    return;
  }
  int id = Wire.read();
  Serial.printf("  <- chipID=0x%02X ", id);
  if (id == 0x60)                                  Serial.print("BME280 OK (T03 진행)");
  else if (id == 0x58 || id == 0x56 || id == 0x57) Serial.print("BMP280 !! 습도 없음 · Adafruit BME280 라이브러리로는 begin 실패 → 부품 확인");
  else if (id == 0x61)                             Serial.print("BME680 !! 다른 라이브러리 필요");
  else                                             Serial.print("알 수 없는 ID");
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Wire.begin(SDA_PIN, SCL_PIN, 100000);   // 100 kHz
  Serial.println("\n[T02] I2C scan on SDA=21 SCL=22");
}

void loop() {
  int found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    uint8_t err = Wire.endTransmission();
    if (err == 0) {
      Serial.printf("[T02] found 0x%02X", addr);
      if (addr == 0x76 || addr == 0x77) printBoschId(addr);   // BME280인지 BMP280인지 칩 ID로 구분
      if (addr == 0x2A) Serial.print("  <- ACD10 CO2 (선택)");
      Serial.println();
      found++;
    }
  }
  if (found == 0) Serial.println("[T02] no device  -> VCC 3V3 · GND · SDA/SCL 배선과 케이블 끝 확인");
  Serial.println("[T02] ---- 3초 뒤 다시 스캔");
  delay(3000);
}
