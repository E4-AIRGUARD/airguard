// DATASET_collect — 연기감지 데이터셋 수집 전용 PMS5003 리더
// PC(pc/collect_dataset.py)가 이 CSV를 받아 웹캠 프레임과 묶어
// timestamp,image_filename,pm1_0,pm2_5,pm10,is_smoke 로 저장한다.
// 1초마다 "pm1_0,pm2_5,pm10" 한 줄만 출력 (대기환경용 atm 값). is_smoke 라벨은
// 여기서 정하지 않고 PC 스크립트가 세션 전체(세트 A=0 / 세트 B=1)에 고정해서 붙인다.
// 배선: PMS5003 TX(5번) -> GPIO16(RX2) · RX(4번, 선택) <- GPIO17(TX2) · VCC 5V · GND 공통
// 전원 인가 후 팬 안정화까지 약 30초는 값이 0이거나 흔들릴 수 있음(T04와 동일 근거) —
// 실제 수집 시작 전에 잠깐 기다릴 것.
#include <HardwareSerial.h>

HardwareSerial pms(2);
const int PMS_RX = 16, PMS_TX = 17;

uint16_t pm1_0 = 0, pm2_5 = 0, pm10 = 0;

bool pmsRead() {
  while (pms.available() >= 32) {
    if (pms.read() != 0x42 || pms.peek() != 0x4D) continue;
    pms.read();
    uint8_t b[30] = {0x42, 0x4D};
    if (pms.readBytes(b + 2, 28) != 28) return false;
    uint16_t rx = ((uint16_t)pms.read() << 8) | pms.read(), sum = 0;
    for (int i = 0; i < 30; i++) sum += b[i];
    if (sum != rx) return false;
    pm1_0 = (b[10] << 8) | b[11];  // 대기환경용 PM1.0
    pm2_5 = (b[12] << 8) | b[13];  // 대기환경용 PM2.5
    pm10  = (b[14] << 8) | b[15];  // 대기환경용 PM10
    return true;
  }
  return false;
}

void setup() {
  Serial.begin(115200);
  delay(300);
  pms.begin(9600, SERIAL_8N1, PMS_RX, PMS_TX);
  Serial.println("[DATASET_collect] ready — PMS5003 UART2 RX16/TX17, 1Hz CSV: pm1_0,pm2_5,pm10");
}

void loop() {
  pmsRead();
  static uint32_t t1000 = 0;
  if (millis() - t1000 >= 1000) {
    t1000 = millis();
    Serial.printf("%u,%u,%u\n", pm1_0, pm2_5, pm10);
  }
}
