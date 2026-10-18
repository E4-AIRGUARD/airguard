"""USB Serial로 ESP32의 DATA 프레임을 받아 SensorSample로 변환합니다."""

from __future__ import annotations

import glob
import time
from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Callable

try:
    import serial
except ImportError as error:
    raise SystemExit("USB Serial 수신에는 pyserial이 필요합니다. pip install pyserial") from error

from sensor_buffer import SensorSample


DATA_FIELDS = 13


@dataclass(frozen=True)
class SerialConfig:
    port: str | None = None
    baudrate: int = 115200
    timeout_s: float = 0.2


def find_serial_port(explicit_port: str | None) -> str:
    if explicit_port:
        return explicit_port
    ports = sorted(glob.glob("/dev/serial/by-id/*"))
    ports += sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*"))
    if not ports:
        raise FileNotFoundError(
            "ESP32 시리얼 포트를 찾지 못했습니다. USB 데이터 케이블 연결 후 "
            "--serial-port /dev/ttyUSB0 또는 /dev/serial/by-id/...를 지정하세요."
        )
    return ports[0]


def data_line_to_sample(line: str, received_at: float | None = None) -> SensorSample | None:
    """ESP32의 DATA CSV 한 줄을 런타임 표준 센서 샘플로 바꿉니다."""
    if not line.startswith("DATA,"):
        return None
    fields = line[5:].split(",")
    if len(fields) != DATA_FIELDS:
        return None
    try:
        bme_error = int(fields[11])
        pms_stale = int(fields[12])
        if bme_error:
            return None
        return SensorSample(
            # ESP elapsed_ms는 재부팅 때 0으로 돌아가므로 Jetson 수신 시각을 기준 시각으로 사용합니다.
            timestamp=time.time() if received_at is None else received_at,
            temperature_c=float(fields[5]),
            humidity_pct=float(fields[6]),
            pm25_ug_m3=None if pms_stale else float(fields[9]),
            # 절대기압(hPa)만 수신하며 차압·음압 판단에는 사용하지 않습니다.
            pressure_pa=float(fields[7]) * 100.0,
            intake_rpm=float(fields[2]),
            exhaust_rpm=float(fields[4]),
            intake_pwm=float(fields[1]),
            exhaust_pwm=float(fields[3]),
        )
    except ValueError:
        return None


class SerialSensorReceiver:
    """ESP32가 1초마다 보내는 DATA 프레임을 백그라운드에서 수신합니다."""

    def __init__(self, config: SerialConfig, on_sample: Callable[[SensorSample], None]):
        self.config = config
        self.on_sample = on_sample
        self.stop_event = Event()
        self.serial_port: serial.Serial | None = None
        self.serial_lock = Lock()
        self.thread = Thread(target=self._read_loop, name="serial-sensor", daemon=True)

    def start(self) -> None:
        self._open()
        self.thread.start()

    def _open(self) -> None:
        port = find_serial_port(self.config.port)
        connection = serial.Serial()
        connection.port = port
        connection.baudrate = self.config.baudrate
        connection.timeout = self.config.timeout_s
        connection.dtr = False
        connection.rts = False
        connection.open()
        with self.serial_lock:
            self.serial_port = connection
        print(f"[SERIAL] ESP32 센서 수신: {port} @ {self.config.baudrate} bps")

    def _read_loop(self) -> None:
        while not self.stop_event.is_set():
            with self.serial_lock:
                connection = self.serial_port
            if connection is None:
                return
            try:
                raw = connection.readline()
            except (serial.SerialException, OSError) as error:
                print(f"[SERIAL] 수신 중단: {error}")
                return
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            sample = data_line_to_sample(line)
            if sample is not None:
                self.on_sample(sample)

    def send_command(self, command: str) -> None:
        """수신 스레드와 같은 USB Serial 연결로 ESP32 명령 한 줄을 보냅니다."""
        with self.serial_lock:
            if self.serial_port is None or not self.serial_port.is_open:
                raise ConnectionError("ESP32 USB Serial 연결이 열려 있지 않습니다.")
            try:
                self.serial_port.write((command + "\n").encode("utf-8"))
                self.serial_port.flush()
            except (serial.SerialException, OSError) as error:
                raise ConnectionError(f"ESP32 명령 전송 실패: {error}") from error

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=self.config.timeout_s + 1.0)
        with self.serial_lock:
            if self.serial_port is not None:
                self.serial_port.close()
                self.serial_port = None


class DummySensorPublisher:
    """USB Serial 없이 버퍼·제어·대시보드 흐름을 확인합니다."""

    def __init__(self, on_sample: Callable[[SensorSample], None], stop_event: Event):
        self.on_sample = on_sample
        self.stop_event = stop_event
        self.thread = Thread(target=self._run, name="dummy-sensor", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        temperature = 22.5
        direction = 1.0
        while not self.stop_event.wait(1.0):
            temperature += 0.09 * direction
            if temperature >= 27.0:
                direction = -1.0
            elif temperature <= 22.5:
                direction = 1.0
            self.on_sample(SensorSample(timestamp=time.time(), temperature_c=temperature))
