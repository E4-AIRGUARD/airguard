#!/usr/bin/env bash
# jetson_setup.sh — Jetson Orin Nano(JetPack 6 / Ubuntu 22.04)에서 ESP32 USB 시리얼을 받기 위한 준비.
# VNC는 이미 세팅되어 있다고 가정한다. 이 스크립트는 "나머지" 중 Jetson 쪽 = USB 시리얼 인식 + pyserial + picocom.
# 근거: CB-T1 A장 · NVIDIA 포럼(brltty 간섭 / cp210x.ko 포함).
# 실행:  chmod +x jetson_setup.sh && ./jetson_setup.sh
set -euo pipefail

echo "== [1/5] brltty 제거 (USB 시리얼을 가로채는 사례) =="
# brltty-udev.service가 CP210x를 점자 단말로 오인해 /dev/ttyUSB를 못 잡게 하는 문제 방지
sudo systemctl mask brltty-udev.service 2>/dev/null || true
sudo systemctl stop brltty-udev.service 2>/dev/null || true
sudo apt-get remove -y brltty 2>/dev/null || true

echo "== [2/5] dialout 그룹에 현재 사용자 추가 (재로그인 후 적용) =="
sudo usermod -aG dialout "$USER"

echo "== [3/5] pyserial · picocom 설치 =="
sudo apt-get update -y
sudo apt-get install -y python3-serial picocom

echo "== [4/5] cp210x 커널 모듈 확인 =="
if lsmod | grep -q cp210x; then
  echo "  cp210x 이미 로드됨"
else
  sudo modprobe cp210x 2>/dev/null && echo "  modprobe cp210x OK" \
    || echo "  [주의] cp210x 모듈 없음 → 보드를 꽂은 뒤 dmesg | grep cp210x 확인, 안 되면 CB-02 J장 Wi-Fi 대체 경로"
fi

echo "== [5/5] 현재 USB 시리얼 상태 =="
echo "-- lsusb (Silicon Labs CP210x가 보여야 함) --"
lsusb | grep -i -E "silicon labs|cp210|ch34|ftdi" || echo "  (보드 미연결 or 미인식)"
echo "-- /dev/ttyUSB* --"
ls -l /dev/ttyUSB* 2>/dev/null || echo "  (아직 없음 — 보드를 데이터 케이블로 연결)"

cat <<'NEXT'

완료. 다음 순서:
  1) 로그아웃 후 다시 로그인 (dialout 그룹 반영). 확인: id | tr ',' '\n' | grep dialout
  2) ESP32를 데이터 케이블로 연결 → ls -l /dev/ttyUSB0 확인
  3) 빠른 확인:   picocom -b 115200 /dev/ttyUSB0     (종료 Ctrl-A Ctrl-X)
  4) T08 왕복:    python3 cb_serial_check.py          (실행 중 C 20 20 / T 600 / T -1 / q)
NEXT
