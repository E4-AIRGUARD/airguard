// T04_pms5003 — Plantower PMS5003 32바이트 프레임 수신 · 체크섬 검사
// 배선(2026-09-27 변경, GPIO2는 부팅 스트래핑 충돌로 폐기): PMS5003 5번(TX) → ESP32 GPIO14 (UART2 RX) · 4번(RX) ← GPIO27 (선택)
//       1번 VCC → 브레드보드 5 V 줄 · 2번 GND → 공통 GND (ESP32 GND와 반드시 같은 줄)
//       SET(3) · RESET(6)은 비워 둠 → 자동 출력 모드
// 라이브러리 없음 (직접 파싱) · 합격 기준: 체크섬 OK가 계속 찍히고 PM2.5가 실내 0~50 부근
//       센서 입구 근처에서 향/라이터 연기를 살짝 흘리면 PM2.5가 급상승 후 회복
#include <HardwareSerial.h>

HardwareSerial pms(2);
const int PMS_RX = 14, PMS_TX = 27;

struct PmsFrame {
  uint16_t pm1_cf, pm25_cf, pm10_cf;      // CF=1 (공장 보정)
  uint16_t pm1_atm, pm25_atm, pm10_atm;   // 대기 환경용 → 경보 기준에 쓰는 값
  uint16_t n03, n05, n10, n25, n50, n100; // 0.1 L당 입자 개수 (>0.3 µm … >10 µm)
};

// 프레임: 0x42 0x4D · 길이(2) · 데이터 13워드(26) · 체크섬(2) = 32바이트
// 체크섬 = 앞 30바이트 합 (매뉴얼 v2.3 기준)
bool readFrame(PmsFrame &f, uint32_t timeoutMs = 1500) {
  uint32_t t0 = millis();
  while (millis() - t0 < timeoutMs) {
    if (pms.available() < 32) { delay(5); continue; }
    if (pms.read() != 0x42) continue;
    if (pms.peek() != 0x4D) continue;
    pms.read();
    uint8_t buf[30];
    buf[0] = 0x42; buf[1] = 0x4D;
    if (pms.readBytes(buf + 2, 28) != 28) return false;
    uint16_t sumRx = ((uint16_t)pms.read() << 8) | pms.read();
    uint16_t sum = 0;
    for (int i = 0; i < 30; i++) sum += buf[i];
    if (sum != sumRx) { Serial.printf("[T04] checksum FAIL calc=%u rx=%u\n", sum, sumRx); return false; }
    uint16_t len = (buf[2] << 8) | buf[3];
    if (len != 28) { Serial.printf("[T04] bad length %u\n", len); return false; }
    uint16_t *w = (uint16_t *)&f;
    for (int i = 0; i < 12; i++) w[i] = (buf[4 + i * 2] << 8) | buf[5 + i * 2];
    return true;
  }
  return false;
}

void setup() {
  Serial.begin(115200);
  delay(300);
  pms.begin(9600, SERIAL_8N1, PMS_RX, PMS_TX);
  Serial.println("[T04] PMS5003 on UART2 RX=14 TX=27 · 9600 8N1");
  Serial.println("[T04] 전원 후 약 30초는 팬 안정화 시간 → 값이 0이거나 흔들려도 정상");
}

void loop() {
  PmsFrame f;
  if (readFrame(f)) {
    Serial.printf("[T04] OK  PM1=%u PM2.5=%u PM10=%u (atm)  | CF1 %u/%u/%u | n0.3=%u n2.5=%u\n",
                  f.pm1_atm, f.pm25_atm, f.pm10_atm, f.pm1_cf, f.pm25_cf, f.pm10_cf, f.n03, f.n25);
    if (f.pm25_atm >= 500) Serial.println("[T04] PM2.5 >= 500 : 측정 상한 (DANGER 조건)");
  } else {
    Serial.println("[T04] no frame  -> 5 V 전원 · TX→14 배선 · GND 공통 확인");
  }
  delay(1000);
}
