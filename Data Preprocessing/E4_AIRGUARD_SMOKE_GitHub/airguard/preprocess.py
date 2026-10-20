"""공통 시그널 처리: 후행 중앙값 → 상한 500. 보간·시간 재정렬은 하지 않습니다."""
from collections import deque
import statistics

from .common import COLUMNS, EXHAUST, INTAKE, PM, RAW, interval


def process(run, config):
    """형식별 무효 표본 정책은 유지하되, 평활화 및 상한 연산은 한 번만 수행합니다."""
    samples = run['samples']
    step = interval(samples)
    gap = config['gap_factor'] * step
    run.update(step=step, gap=gap, full=[], fan=[], changes=[])
    history = deque(maxlen=config['median_window'])
    previous = None
    first_time = next((r['t'] for r in samples if r['t'] is not None), None)
    if first_time is None:
        raise ValueError('유효한 PM 측정 시각이 없습니다.')
    policy = config['invalid_rows']
    drop = policy == 'drop' or (policy == 'legacy' and run['format'] == 'csv')
    for sample in samples:
        reasons = list(sample.get('issues', []))
        t, pm, pair = sample['t'], sample['pm'], (sample['intake'], sample['exhaust'])
        if previous is not None and t is not None and previous['t'] is not None:
            dt = t - previous['t']
            if dt <= 0:
                reasons.append('time_not_increasing')
            elif dt > gap:
                reasons.append('time_gap')
        bad_pm = pm is None or t is None or sample.get('invalid_pm', False)
        # JSONL 기존 코드는 역행 표본 PM을 무효화합니다. CSV는 기록/구간을 보존합니다.
        if run['format'] == 'jsonl' and 'time_not_increasing' in reasons:
            bad_pm = True
        boundary = (bool(reasons) or sample.get('reset', False) or previous is None
                    or pair != (previous['intake'], previous['exhaust'])
                    or sample.get('phase') != previous.get('phase'))
        if boundary:
            history.clear()
        usable = None if bad_pm else pm
        if drop and (bad_pm or None in pair):
            history.clear()
            run['issues'].append({'Line': sample['line'], 'Reason': 'row_removed',
                                  'Detail': ';'.join(reasons) or 'missing_required_value'})
            previous = None
            continue
        filtered = usable
        if usable is not None:
            history.append(usable)
            # 창이 가득 차기 전의 첫 표본들은 원래 값을 유지합니다.
            if len(history) == history.maxlen:
                filtered = statistics.median(history)
        else:
            history.clear()
        capped = min(filtered, config['pm_cap']) if filtered is not None else None
        row = {'Measurement_Timestamp': sample['stamp'],
               'Time[s]': round(t-first_time, 6) if t is not None else None,
               PM: capped, RAW: pm, 'PM_Capped[0/1]': int(filtered > config['pm_cap']) if filtered is not None else None,
               INTAKE: pair[0], EXHAUST: pair[1], '_t': t, '_line': sample['line'],
               '_phase': sample.get('phase', ''), '_issues': ';'.join(reasons)}
        run['full'].append(row)
        for reason in dict.fromkeys(reasons):
            run['issues'].append({'Line': sample['line'], 'Reason': reason, 'Time[s]': row['Time[s]']})
        for operation, before, after in [('trailing_median', usable, filtered), ('cap_500', filtered, capped)]:
            if before != after:
                run['changes'].append({'Line': sample['line'], 'Time[s]': row['Time[s]'],
                                       'Operation': operation, 'Before': before, 'After': after})
        previous = sample
    if not run['full']:
        raise ValueError('유효 출력 행이 없습니다.')
    # 팬 기준 CSV는 전체 결과에서 시간축만 바꿉니다. 필터를 재적용하지 않습니다.
    if run['fan_start'] is not None and run['fan_valid']:
        for row in run['full']:
            t = row['_t']
            if t is None or t < run['fan_start']:
                continue
            if run['format'] == 'jsonl' and (row['_phase'] != 'observation' or t > run['fan_end']):
                continue
            run['fan'].append(dict(row, **{'Time[s]': round(t-run['fan_start'], 6),
                                          'Original_Time[s]': row['Time[s]']}))
    run['full_rows'] = len(run['full'])
    run['fan_rows'] = len(run['fan'])
    return run
