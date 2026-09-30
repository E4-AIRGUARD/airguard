#!/usr/bin/env python3
"""가열·연기 없이 팬 3x3 조합을 순서대로 측정하고 1초 간격 사진을 저장한다."""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

from measure_thermal_response import main as measure

LEVELS = (('weak', 35), ('medium', 60), ('strong', 100))
COMBINATIONS = [(f'intake_{i_name}_exhaust_{o_name}', i, o)
                for i_name, i in LEVELS for o_name, o in LEVELS]
DEFAULT_ROOT = Path(__file__).resolve().parent / 'data' / 'clean_air_fan_matrix'


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration-sec', type=float, default=300, help='조합당 측정 시간(초, 기본 300)')
    parser.add_argument('--port', default='COM7')
    parser.add_argument('--camera-index', type=int, default=1)
    parser.add_argument('--image-interval-sec', type=float, default=1)
    parser.add_argument('--out-root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--stop-file', type=Path, default=None)
    parser.add_argument('--dry-run', action='store_true', help='측정 순서만 출력; 장비를 열지 않음')
    args = parser.parse_args(argv)
    if not math.isfinite(args.duration_sec) or args.duration_sec <= 0:
        parser.error('--duration-sec은 유한한 양수여야 합니다')
    if not math.isfinite(args.image_interval_sec) or args.image_interval_sec <= 0:
        parser.error('--image-interval-sec은 유한한 양수여야 합니다')
    if args.camera_index < 0:
        parser.error('--camera-index는 0 이상이어야 합니다')
    for number, (name, intake, exhaust) in enumerate(COMBINATIONS, 1):
        print(f'{number}/9 {name}: 흡기 {intake}% / 배기 {exhaust}%, {args.duration_sec:g}초')
    if args.dry_run:
        return

    batch = args.out_root / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    batch.mkdir(parents=True, exist_ok=False)
    state = {'started':datetime.now().isoformat(timespec='seconds'), 'condition':'no heat, no smoke',
             'duration_sec':args.duration_sec, 'image_interval_sec':args.image_interval_sec,
             'camera_index':args.camera_index, 'status':'running', 'completed':[], 'current':None}
    def save():
        (batch / 'batch.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    print(f'[matrix] 결과 폴더: {batch}', flush=True)
    try:
        for number, (name, intake, exhaust) in enumerate(COMBINATIONS, 1):
            state['current'] = name
            save()
            print(f'[matrix] START {number}/9 {name}', flush=True)
            completed = measure([name, '--experiment', 'clean-air',
                                 '--duration-min', str(args.duration_sec / 60),
                                 '--intake', str(intake), '--exhaust', str(exhaust),
                                 '--port', args.port, '--camera-index', str(args.camera_index),
                                 '--image-interval-sec', str(args.image_interval_sec),
                                 '--out-dir', str(batch / 'csv' / name),
                                 '--image-dir', str(batch / 'images'),
                                 *(['--stop-file', str(args.stop_file)] if args.stop_file else [])])
            if not completed:
                state['status'] = 'stopped'
                print('[matrix] 사용자 중단; 남은 조합은 실행하지 않습니다', flush=True)
                break
            state['completed'].append(name)
            save()
        else:
            state['status'] = 'complete'
            state['current'] = None
    except BaseException as exc:
        state['status'] = 'stopped' if isinstance(exc, KeyboardInterrupt) else 'failed'
        state['error'] = str(exc)
        raise
    finally:
        state['ended'] = datetime.now().isoformat(timespec='seconds')
        save()
        print(f"[matrix] {state['status']}: {len(state['completed'])}/9 완료 -> {batch}", flush=True)


if __name__ == '__main__':
    main()
