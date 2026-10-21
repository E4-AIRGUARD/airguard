"""검증된 ESP32 USB Serial 명령으로 흡기·배기 팬 PWM을 제어합니다."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class FanCommand:
    intake_percent: int
    exhaust_percent: int
    reason: str
    timestamp: float


class EspFanController:
    """포트는 SerialSensorReceiver만 열고, 이 객체는 그 연결에 명령만 씁니다."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self._send_command: Callable[[str], None] | None = None

    def bind_serial_sender(self, send_command: Callable[[str], None]) -> None:
        self._send_command = send_command

    def apply(self, intake_percent: int, exhaust_percent: int, reason: str) -> FanCommand:
        command = FanCommand(
            intake_percent=int(max(0, min(100, intake_percent))),
            exhaust_percent=int(max(0, min(100, exhaust_percent))),
            reason=reason,
            timestamp=time.time(),
        )
        if self.dry_run:
            print(f"[DRY RUN] 팬 명령: {command}")
        elif self._send_command is None:
            raise RuntimeError("ESP32 Serial 명령 연결이 준비되지 않았습니다.")
        elif command.intake_percent == command.exhaust_percent == 0:
            self._send_command("0")
        else:
            self._send_command(f"F {command.intake_percent} {command.exhaust_percent}")
        return command

