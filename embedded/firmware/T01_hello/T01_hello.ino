// T01_hello — 보드 인식 · 시리얼 · MAC 주소 · 내장 LED 확인
// 보드: ESP32 DevKitC WROOM-32D (CP2102) · Arduino-ESP32 코어 3.x
// 합격 기준: 시리얼 모니터(115200)에 1초마다 uptime과 MAC이 찍힘
// 참고: Espressif 공식 DevKitC V4에는 전원 LED만 있고 사용자 LED가 없음 → GPIO2 점멸은 호환 보드에서만 보임
#include <WiFi.h>

const int LED_BUILTIN_PIN = 2;   // 호환 보드의 GPIO2 LED (공식 V4에는 없음 · 시리얼만 봐도 됨)

void setup() {
  Serial.begin(115200);
  delay(300);
  pinMode(LED_BUILTIN_PIN, OUTPUT);
  WiFi.mode(WIFI_STA);           // MAC을 읽기 위해 STA 모드만 켬 (공유기 접속 안 함)
  delay(100);                    // WiFi 드라이버 초기화 대기 — 없으면 macAddress()가 00:00:00:00:00:00을 반환함(실기 확인, 2026-09-16)
  Serial.println();
  Serial.println("[T01] CleanBox ESP32 hello");
  Serial.printf("[T01] chip=%s rev=%d cores=%d cpu=%lu MHz\n",
                ESP.getChipModel(), (int)ESP.getChipRevision(), (int)ESP.getChipCores(), (unsigned long)ESP.getCpuFreqMHz());
  Serial.printf("[T01] flash=%lu KB  free heap=%lu B\n", (unsigned long)(ESP.getFlashChipSize() / 1024), (unsigned long)ESP.getFreeHeap());
  Serial.printf("[T01] MAC(STA)=%s   <- 보드 뒷면 스티커에 적어 둘 것\n", WiFi.macAddress().c_str());
}

void loop() {
  static uint32_t n = 0;
  digitalWrite(LED_BUILTIN_PIN, n & 1);
  Serial.printf("[T01] uptime=%lu s\n", (unsigned long)(millis() / 1000));
  n++;
  delay(1000);
}
