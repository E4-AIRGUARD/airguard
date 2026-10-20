"""실행 순서: 탐색 → 로드 → 공통 전처리 → 분석 → 새 결과 폴더 저장."""
import argparse
import json
from pathlib import Path

from .analysis import evaluate
from .common import number, read_json, unique_output
from .preprocess import process
from .readers import discover, read_runs
from .reports import write_report
from .reference import read_reference, write_reference

ROOT = Path(__file__).resolve().parent.parent


def load_config(path):
    config = read_json(path)
    required = {'median_window', 'gap_factor', 'hold_seconds', 'pm_cap', 'pm_target', 'invalid_rows', 'exclude_runs', 'review_runs'}
    if set(config) != required:
        raise ValueError('설정 키 확인 필요: '+str(sorted(set(config) ^ required)))
    if type(config['median_window']) is not int or config['median_window'] not in (1, 3, 5):
        raise ValueError('median_window는 1(무필터), 3, 5 중 선택하세요.')
    for key in ('gap_factor', 'hold_seconds'):
        if number(config[key]) is None or config[key] <= 0:
            raise ValueError(f'{key}는 양수여야 합니다.')
    if config['pm_cap'] != 500 or config['pm_target'] != 50:
        raise ValueError('출력 스키마 1.0은 PM500→50 기준입니다. 다른 기준은 분석 코드/문서도 함께 변경해야 합니다.')
    if config['invalid_rows'] not in ('legacy', 'keep', 'drop'):
        raise ValueError('invalid_rows는 legacy, keep, drop 중 선택하세요.')
    for key in ('exclude_runs', 'review_runs'):
        if not isinstance(config[key], list):
            raise ValueError(f'{key}는 목록이어야 합니다.')
        for rule in config[key]:
            if not isinstance(rule, dict) or set(rule) != {'dates', 'series', 'ids', 'reason'}:
                raise ValueError(f'{key}: dates/series/ids/reason 필수')
            if not isinstance(rule['dates'], list) or not rule['dates'] or not all(isinstance(d, str) and len(d) == 8 and d.isdigit() for d in rule['dates']):
                raise ValueError(f'{key}: dates는 YYYYMMDD 문자열 목록')
            if not isinstance(rule['ids'], list) or not rule['ids'] or not all(isinstance(v, str) and v for v in rule['ids']):
                raise ValueError(f'{key}: ids는 실험 ID 문자열 목록')
            if rule['series'] not in ('csv', 'space', 'intermittent') or not rule['reason']:
                raise ValueError(f'{key}: 측정 방식과 사유를 명시하세요.')
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(prog='전처리_SMOKE.py', description='전처리_SMOKE: E4-AIRGUARD CSV/JSONL 통합 전처리')
    parser.add_argument('--input', type=Path, help='RAW 폴더. 생략하면 폴더 선택 창')
    parser.add_argument('--output', type=Path, help='새 결과 폴더. 기존 폴더가 있으면 번호 추가')
    parser.add_argument('--reference', type=Path, help='이전 분석 결과 폴더. 팬 기준 CSV로 참고 비교')
    parser.add_argument('--format', choices=('auto', 'csv', 'jsonl'), default='auto')
    parser.add_argument('--series', choices=('auto', 'space', 'intermittent'), default='auto',
                        help='JSONL 측정 방식. auto는 space가 있으면 우선 선택')
    parser.add_argument('--config', type=Path, default=ROOT/'config.json')
    parser.add_argument('--check-input', action='store_true', help='선택/제외 목록만 확인. 결과 폴더 생성 없음')
    args = parser.parse_args(argv)
    try:
        if args.input is None:
            try:
                import tkinter as tk
                from tkinter import filedialog
                window = tk.Tk(); window.withdraw()
                selected = filedialog.askdirectory(title='전처리_SMOKE · RAW 폴더 선택')
                window.destroy()
            except Exception as exc:
                raise ValueError('폴더 선택 창을 사용할 수 없습니다. --input "RAW 경로"로 실행하세요.') from exc
            if not selected:
                print('폴더 선택 취소. 결과를 만들지 않았습니다.')
                return 0
            args.input = Path(selected)
        root = args.input.resolve()
        if not root.is_dir():
            raise ValueError('입력 폴더를 찾을 수 없습니다: '+str(root))
        config = load_config(args.config)
        fmt, paths = discover(root, args.format, args.series)
        runs, attempts, selected = read_runs(root, fmt, paths, config, args.series)
        print(f'Input: {root}\nFormat: {fmt} | Series: {selected} | Selected: {len(runs)}')
        for run in runs:
            print(f'  {run["date"]} / {run["trial"]} / I{run["pair"][0]:g} E{run["pair"][1]:g} / {run["group"]}')
        if args.check_input:
            for entry in attempts:
                if entry.get('Reason'):
                    print(f'  제외: {entry["Source"]} | {entry["Reason"]}')
        if not runs:
            reasons = sorted({r.get('Reason', '') for r in attempts})
            raise ValueError('처리할 회차가 없습니다. 결과 폴더 생성 없음. 사유: '+'; '.join(reasons))
        if args.check_input:
            print('입력 확인 완료. 파일 생성 없음.')
            return 0
        previous = read_reference(args.reference.resolve(), config) if args.reference else None
        for run in runs:
            evaluate(process(run, config), config)
            if not run['fan']:
                run['group'] = '조건변경_참고용'
        # 모든 입력 처리에 성공한 뒤에만 새 폴더를 만듭니다.
        base = args.output or ROOT/'E4_AIRGUARD_SMOKE_분석결과'
        out = unique_output(base, root)
        write_report(out, runs, attempts, config, selected)
        if previous:
            write_reference(out, runs, previous)
            index = out/'00_분석결과_여기부터.html'
            text = index.read_text(encoding='utf-8')
            text = text.replace('</html>', '<p><a href="04_데이터분석_요약/이전자료_참고비교/00_이전현재_비교.html">이전/현재 참고 비교</a></p></html>')
            index.write_text(text, encoding='utf-8')
        print(f'Completed: {out}\nFull rows: {sum(r["full_rows"] for r in runs)} | Fan rows: {sum(r["fan_rows"] for r in runs)}')
        print('열기: '+str(out/'00_분석결과_여기부터.html'))
        return 0
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        parser.exit(2, 'ERROR: '+str(exc)+'\n')
    return 2
