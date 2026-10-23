"""v6 온도 제어 실행부. 센서 수신과 팬 전송은 같은 USB 연결을 사용합니다."""
import argparse
from pathlib import Path
import time

from event_store import EventStore
from fan_control import EspFanController
from sensor_receiver import SerialConfig, SerialSensorReceiver
from temperature_controller import TemperatureController

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial-port', default=None, help='생략 시 ESP32 USB 포트 자동 탐색')
    parser.add_argument('--database', type=Path, default=ROOT/'runtime/edge_events.db')
    parser.add_argument('--dry-run', action='store_true', help='실제 센서는 받되 팬 명령은 전송하지 않음')
    args = parser.parse_args()
    controller = TemperatureController(ROOT/'models/lstm_temperature_trajectory_v6.pt')
    store = EventStore(args.database)
    fans = EspFanController(dry_run=args.dry_run)

    def receive(sample):
        try:
            controller.observe(sample)
            store.sensor(sample, controller.buffer.latest_smoothed_temperature())
        except ValueError as error:
            print(f'[SENSOR] 잘못된 샘플 제외: {error}')

    receiver = SerialSensorReceiver(SerialConfig(args.serial_port), receive)
    fans.bind_serial_sender(receiver.send_command)
    last_command_at = 0.
    connected = False
    try:
        store.metadata(controller, args.dry_run)
        receiver.start()
        connected = True
        while True:
            started = time.monotonic()
            decision = controller.decide()
            if decision is not None:
                sample, result, reason = decision
                print(f'[SENSOR] 현재 온도: {sample.temperature_c:.2f}°C')
                pair = (result.intake_pwm, result.exhaust_pwm)
                changed = pair != controller.current_pair
                sent = changed or time.time() - last_command_at >= controller.config.heartbeat_s
                if sent:
                    command = fans.apply(*pair, reason)
                    last_command_at = command.timestamp
                    controller.confirm_applied(*pair)
                store.decision(sample, result, reason, changed, sent and not args.dry_run, last_command_at)
                if changed:
                    recovery = '추정 불가' if result.recovery_seconds is None else f'{result.recovery_seconds}초'
                    print(f'[CONTROL] {pair[0]}/{pair[1]}% · {result.model_name} · 회복 {recovery} · {reason}')
            elapsed = time.monotonic() - started
            if elapsed > controller.config.period_s:
                print(f'[WARNING] 제어 주기 초과: {elapsed:.3f}초')
            time.sleep(max(0., controller.config.period_s - elapsed))
    except KeyboardInterrupt:
        print('종료 요청을 받았습니다.')
    finally:
        if connected:
            try:
                fans.apply(0, 0, '프로그램 종료: 팬 정지')
            except (ConnectionError, RuntimeError) as error:
                print(f'[SHUTDOWN] 팬 정지 전송 실패: {error}')
        receiver.stop()
        if receiver.thread.is_alive():
            receiver.thread.join()
        store.close()


if __name__ == '__main__':
    main()
