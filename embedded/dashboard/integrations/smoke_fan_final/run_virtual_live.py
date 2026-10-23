"""가상 연기 상황을 실시간으로 돌리면서 대시보드를 함께 띄운다(센서/팬 없이 화면과 제어를 눈으로 확인하는 용도).
  python3 run_virtual_live.py              # 1배속, 브라우저에서 http://localhost:8080
  python3 run_virtual_live.py --speed 5    # 5배속
  옵션: --deadline 70  --port 8080  --smoke-at 40(연기 시작 초)
평소 PM 36에서 연기가 올라왔다 내려오는 가상 플랜트를 쓴다. 로그는 가상시험/라이브_에너지로그.csv 에 쌓인다."""
import argparse, json, math, os, random, subprocess, sys, time
import test_virtual as tv
from fan_controller import Planner, FanController, EnergyMeter

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--speed', type=float, default=1.0)
    ap.add_argument('--deadline', type=float, default=70.0)
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--smoke-at', type=float, default=40.0)
    a = ap.parse_args()
    tv.T_SMOKE = a.smoke_at
    log = os.path.join(HERE, '가상시험', '라이브_에너지로그.csv')
    os.makedirs(os.path.dirname(log), exist_ok=True)
    P = Planner(tv.M, prune=False)
    ctl, meter = FanController(P, a.deadline or None), EnergyMeter(P, log)
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, 'dashboard', 'serve_dashboard.py'), log, str(a.port)])
    print('대시보드: http://localhost:%d  (Ctrl+C로 종료)' % a.port)
    rnd, pm, t, combo, dt, period, next_s = random.Random(1), tv.BG, 0.0, '35,35', 0.5, 2.0, 0.0
    try:
        while True:
            src = tv.SRC if tv.T_SMOKE <= t < tv.T_SMOKE + tv.T_SRC else 0.0
            pm += (src - tv.k_of(combo) * (pm - tv.BG)) * dt
            t += dt
            if t >= next_s:
                next_s += period
                raw = max(0.0, round(pm * (1 + rnd.gauss(0, 0.02)) + rnd.gauss(0, 1.0)))
                ctl.feed(t, raw); d = ctl.tick(t); meter.update(d); combo = d.combo
            if t > tv.T_SMOKE + 260:           # 연기가 걸히고 한참 지나면 새 연기를 한 번 더 일으킨다
                tv.T_SMOKE = t + 30
            time.sleep(dt / a.speed)
    except KeyboardInterrupt:
        pass
    finally:
        meter.close(); srv.terminate()


if __name__ == '__main__':
    main()
