"""입력 어댑터. 폴더 배치/이벤트 의미만 해석하고 평활화는 preprocess에 맡깁니다."""
import hashlib
import json
from pathlib import Path

from .common import EXHAUST, INTAKE, PM, digest, interval, number, read_csv, read_json, safe_name, timestamp, valid

ALIASES = {
    PM: (PM, 'PM2.5', 'pm2_5', 'pm25'),
    INTAKE: (INTAKE, 'Intake_PWM', 'duty_in_pct'),
    EXHAUST: (EXHAUST, 'Exhaust_PWM', 'duty_out_pct'),
}


def generated(path, root):
    return any((p / '.e4_airguard_generated').exists() for p in (path.parent, *path.parents)
               if p == root or root in p.parents)


def discover(root, requested='auto', series='auto'):
    """여러 형식이 섞이면 선택을 요구합니다. 모르는 구조를 임의로 해석하지 않습니다."""
    json_paths = sorted(p for p in root.rglob('trial.json') if not generated(p, root))
    csv_paths = []
    for p in sorted(root.rglob('*.csv')):
        if generated(p, root):
            continue
        try:
            rows = read_csv(p)
        except (OSError, ValueError):
            continue
        if rows and 'PM_Capped[0/1]' not in rows[0] and 'Experiment_ID' not in rows[0]:
            # JSONL 자료의 pm_observation.csv는 요약본이며 RAW CSV가 아닙니다.
            has_signals = all(any(k in rows[0] for k in names) for names in ALIASES.values())
            has_time = any(k in rows[0] for k in ('Time', 'pc_timestamp', 'Timestamp', 'elapsed_ms'))
            if has_signals and has_time:
                csv_paths.append(p)
    available = [name for name, paths in [('csv', csv_paths), ('jsonl', json_paths)] if paths]
    if requested == 'auto':
        if len(available) != 1:
            raise ValueError(f'입력 형식 자동 판별 불가: {available}. --format csv 또는 jsonl로 선택하세요.')
        requested = available[0]
    paths = csv_paths if requested == 'csv' else json_paths
    if not paths:
        raise ValueError(f'{requested} 원본이 없습니다. CSV 센서 칼럼 또는 trial.json 구조를 확인하세요.')
    return requested, paths


def family(directory, root, protocol):
    parts = directory.relative_to(root).parts
    if 'fail_test' in parts or 'fail_test' in directory.name:
        return 'fail_test'
    if 'space_runs' in parts or protocol.get('operator_input_channel') == 'local_space':
        return 'space'
    if 'intermittent_runs' in parts or all(k in protocol for k in ('start_stable_sec', 'background_off_sec', 'background_on_sec')):
        return 'intermittent'
    return 'unknown'


def match_rule(config, key, date, series, trial):
    for rule in config[key]:
        if date in rule['dates'] and series == rule['series'] and trial in rule['ids']:
            return rule['reason']
    return ''


def identity(source, root, date, series, trial):
    relative = source.relative_to(root).as_posix()
    signature = hashlib.sha256((relative + digest(source)).encode()).hexdigest()[:8]
    return safe_name(f'{date}_{series}_{trial}_{signature}')


def manifest(paths, root):
    return [{'Source': p.relative_to(root).as_posix(), 'SHA256': digest(p), 'Bytes': p.stat().st_size}
            for p in paths if p.is_file()]


def base_run(source, root, date, series, trial, format_name, meta):
    return dict(id=identity(source, root, date, series, trial), trial=trial, date=date, series=series,
                format=format_name, source=source.relative_to(root).as_posix(), meta=meta,
                samples=[], issues=[], telemetry=[], group='기본비교용', fan_start=None,
                fan_end=None, fan_valid=False, fan_reason='팬 시작 이벤트 없음', pair=(None, None))


def load_csv(path, root):
    raw = read_csv(path)
    header = raw[0] if raw else {}
    mapping = {k: next((a for a in names if a in header), None) for k, names in ALIASES.items()}
    if None in mapping.values():
        raise ValueError('CSV 필수 칼럼 누락: ' + ', '.join(k for k, v in mapping.items() if v is None))
    time_key = next((k for k in ('Time', 'pc_timestamp', 'Timestamp') if k in header), None)
    if not time_key and 'elapsed_ms' not in header:
        raise ValueError('측정 시각 칼럼 누락')
    status_path = path.parent / 'status.json'
    status = read_json(status_path) if status_path.is_file() else {}
    date = 'undated'
    # 절대시각은 첫 시각과의 차이로 계산하여 운영체제 시간대/DST 의존성을 피합니다.
    first_dt = None
    if time_key:
        for row in raw:
            try:
                first_dt = timestamp(row.get(time_key, ''))
                break
            except (ValueError, TypeError):
                continue
    if first_dt:
        date = first_dt.strftime('%Y%m%d')
    trial = safe_name(path.parent.name if path.parent != root else path.stem)
    run = base_run(path, root, date, 'csv', trial, 'csv', {'status': status})
    run['input_count'] = len(raw)
    seen = set()
    last_line = None
    for line, r in enumerate(raw, 2):
        signature = json.dumps(r, sort_keys=True, ensure_ascii=False)
        if signature in seen:
            run['issues'].append({'Line': line, 'Reason': 'exact_duplicate_removed'})
            continue
        seen.add(signature)
        stamp, t, issues = r.get(time_key, '') if time_key else '', None, []
        try:
            t = (timestamp(stamp) - first_dt).total_seconds() if time_key else number(r['elapsed_ms'])
            if not time_key and t is not None:
                t /= 1000
        except (ValueError, TypeError):
            pass
        if t is None:
            issues.append('invalid_time')
        pm, intake, exhaust = valid(r[mapping[PM]]), valid(r[mapping[INTAKE]], 100), valid(r[mapping[EXHAUST]], 100)
        if 'pms_stale' in r and number(r['pms_stale']) != 0:
            pm = None
            issues.append('pms_stale')
        for key, value in [('pm', pm), ('intake', intake), ('exhaust', exhaust)]:
            if value is None:
                issues.append('missing_or_invalid_' + key)
        run['samples'].append(dict(t=t, stamp=stamp, pm=pm, intake=intake, exhaust=exhaust,
                                   line=line, phase='', issues=issues,
                                   reset=last_line is not None and line != last_line+1))
        last_line = line
    good = [r for r in run['samples'] if r['t'] is not None and r['pm'] is not None
            and r['intake'] is not None and r['exhaust'] is not None]
    if not good:
        raise ValueError('유효 센서 표본 없음')
    pair = (good[-1]['intake'], good[-1]['exhaust'])
    run['pair'] = pair
    if status.get('ventilation_started') and first_dt:
        run['fan_start'] = (timestamp(status['ventilation_started']) - first_dt).total_seconds()
        transitions = [b['t'] for a, b in zip(good, good[1:])
                       if (a['intake'], a['exhaust']) == (0, 0) and (b['intake'], b['exhaust']) != (0, 0)]
        nearest = min(transitions, key=lambda t: abs(t-run['fan_start'])) if transitions else None
        run['fan_valid'] = nearest is not None and abs(nearest-run['fan_start']) <= max(2, 2*interval(run['samples']))
        run['fan_reason'] = 'status 이벤트 + PWM 교차확인' if run['fan_valid'] else '환기 이벤트와 PWM 전환 불일치'
        run['meta']['pwm_transition_s'] = nearest
        after = [r for r in good if r['t'] >= run['fan_start'] and (r['intake'], r['exhaust']) != (0, 0)]
        if after:
            run['pair'] = (after[0]['intake'], after[0]['exhaust'])
            if len({(r['intake'], r['exhaust']) for r in after}) > 1:
                run['group'] = '조건변경_참고용'
                run['meta']['review_reason'] = '팬 시작 이후 비영 PWM 조합 변경'
    if 'smoke_off90' in path.parts:
        run.update(fan_valid=False, fan_reason='연기 발생 관찰: 팬 회복 분석 대상 아님')
    if status.get('status') not in (None, 'complete'):
        run['group'] = '조건변경_참고용'
        run['meta']['review_reason'] = '측정 상태: ' + str(status['status'])
    run['files'] = manifest([path, status_path], root)
    return run


def load_jsonl(path, root, tr, status, protocol, series):
    source = path.parent / 'records.jsonl'
    date = path.parent.name[:8]
    run = base_run(source, root, date, series, tr['id'], 'jsonl',
                   {'trial': tr, 'status': status, 'protocol': protocol})
    run.update(pair=tuple(float(v) for v in tr['pair']), fan_start=float(status['eval_start']), fan_end=float(status['eval_end']))
    command, previous_seq = (None, None), None
    command_match = False
    for line, text in enumerate(source.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not text.strip():
            continue
        try:
            r = json.loads(text)
            if not isinstance(r, dict):
                raise ValueError('JSON object required')
        except (ValueError, TypeError) as exc:
            run['issues'].append({'Line': line, 'Reason': 'invalid_json', 'Detail': str(exc)})
            continue
        if r.get('type') == 'command':
            command = (valid(r.get('intake'), 100), valid(r.get('exhaust'), 100))
            t = number(r.get('t'))
            if t is not None and abs(t-run['fan_start']) < 1e-6 and command == run['pair']:
                command_match = True
            continue
        if r.get('type') == 'fan':
            run['telemetry'].append({'Line': line, 'Measurement_Timestamp': r.get('wall_time'),
                                     'Session_Time[s]': number(r.get('t')),
                                     'Intake_RPM[rpm]': number(r.get('rpm_in')),
                                     'Exhaust_RPM[rpm]': number(r.get('rpm_out')),
                                     'PMS_Stale': r.get('pms_stale')})
            continue
        if r.get('type') != 'pms':
            continue
        t, pm, seq = number(r.get('t')), valid(r.get('pm25')), valid(r.get('seq'))
        issues = []
        for key, value in [('time', t), ('pm', pm), ('seq', seq)]:
            if value is None:
                issues.append('missing_or_invalid_' + key)
        if None in command:
            issues.append('missing_or_invalid_pwm_command')
        invalid_pm = seq is None
        if previous_seq is not None and seq is not None:
            if seq <= previous_seq:
                issues.append('seq_duplicate_or_reset')
                invalid_pm = True
            elif seq > previous_seq+1:
                issues.append('seq_gap')
        try:
            timestamp(r.get('wall_time', ''))
        except (ValueError, TypeError):
            # 원시 단조 시각 t는 별도 보존합니다. 벽시계 오류는 점검 기록에 남깁니다.
            run['issues'].append({'Line': line, 'Reason': 'invalid_wall_time'})
        run['samples'].append(dict(t=t, stamp=r.get('wall_time', ''), pm=pm,
                                   intake=command[0], exhaust=command[1], line=line,
                                   phase=r.get('phase', ''), issues=issues, invalid_pm=invalid_pm))
        previous_seq = seq
    run['input_count'] = len(run['samples'])
    if not run['samples']:
        raise ValueError('pms 유형 표본 없음')
    # 기존 JSONL처럼 시각이 있으나 명령 불일치인 회차는 참고용으로만 보존합니다.
    run.update(fan_valid=True, fan_reason='command + eval_start 확인' if command_match else 'eval_start와 command 불일치')
    if not command_match:
        run['group'] = '조건변경_참고용'
    expected = ({'start_stable_sec': 3, 'background_off_sec': 0, 'background_on_sec': 0,
                 'post_removal_wait_sec': 30, 'start_max_pm25': 40, 'end_max_pm25': 40, 'smoke_sec': 2}
                if series == 'space' else {'start_stable_sec': 30, 'background_off_sec': 60, 'background_on_sec': 60})
    changed = any(protocol.get(k) != v for k, v in expected.items())
    if series != 'space':
        changed = changed or bool(protocol.get('preparation_protocol_changed'))
    if series == 'space' and protocol.get('end_policy') != 'pm40_stable':
        changed = True
    if changed:
        run['group'] = '조건변경_참고용'
    run['files'] = manifest([source, path, path.parent/'status.json', path.parent/'protocol.json'], root)
    return run


def read_runs(root, format_name, paths, config, requested_series='auto'):
    runs, attempts, seen = [], [], set()
    if format_name == 'csv':
        for path in paths:
            entry = {'Source': path.relative_to(root).as_posix(), 'Format': 'csv'}
            try:
                signature = digest(path)
                if signature in seen:
                    raise ValueError('완전히 동일한 CSV: 중복 제외')
                seen.add(signature)
                run = load_csv(path, root)
                excluded = match_rule(config, 'exclude_runs', run['date'], 'csv', run['trial'])
                review = match_rule(config, 'review_runs', run['date'], 'csv', run['trial'])
                if excluded:
                    entry.update(Experiment=run['id'], Group='제외', Reason=excluded)
                else:
                    if review:
                        run['group'] = '조건변경_참고용'
                        run['meta']['review_reason'] = review
                    runs.append(run)
                    entry.update(Experiment=run['id'], Group=run['group'], Reason='')
            except (OSError, ValueError, TypeError, KeyError) as exc:
                entry.update(Group='제외', Reason=str(exc))
            attempts.append(entry)
        return runs, attempts, 'csv'
    candidates = []
    for path in paths:
        try:
            tr, status, protocol = (read_json(p) for p in (path, path.parent/'status.json', path.parent/'protocol.json'))
            candidates.append((path, tr, status, protocol, family(path.parent, root, protocol)))
        except (OSError, ValueError) as exc:
            attempts.append({'Source': path.relative_to(root).as_posix(), 'Group': '제외', 'Reason': str(exc)})
    families = {r[-1] for r in candidates}
    selected = ('space' if 'space' in families else 'intermittent') if requested_series == 'auto' else requested_series
    for path, tr, status, protocol, series in candidates:
        date, trial = path.parent.name[:8], tr.get('id')
        reason = match_rule(config, 'exclude_runs', date, series, trial)
        entry = dict(Source=path.relative_to(root).as_posix(), Trial=trial, Date=date, Series=series)
        pair = tr.get('pair')
        if not reason:
            if 'excluded_runs' in path.relative_to(root).parts or series == 'fail_test':
                reason = '제외/실패 시험 폴더'
            elif series != selected:
                reason = f'선택하지 않은 측정 방식: {series}'
            elif status.get('status') != 'complete':
                reason = '미완료 또는 준비 실패'
            elif not isinstance(trial, str) or not trial:
                reason = '실험 ID 누락'
            elif not isinstance(pair, list) or len(pair) != 2 or any(valid(v, 100) is None for v in pair):
                reason = '팬 조건 오류'
            elif number(status.get('eval_start')) is None or number(status.get('eval_end')) is None:
                reason = '팬 시작/종료 시각 누락'
            elif float(status['eval_end']) <= float(status['eval_start']):
                reason = '팬 시작/종료 시각 순서 오류'
        if not reason:
            try:
                run = load_jsonl(path, root, tr, status, protocol, series)
                # 동일 ID의 재시도 중 무엇을 쓸지 추측하지 않습니다. 날짜/방식별 완료 회차 중복은 중단합니다.
                key = (date, series, trial)
                if key in seen:
                    raise RuntimeError(f'같은 날짜/방식/ID의 완료 회차 중복: {key}. 입력에서 회차를 명시적으로 선택하세요.')
                seen.add(key)
                review = match_rule(config, 'review_runs', date, series, trial)
                if review:
                    run.update(group='조건변경_참고용')
                    run['meta']['review_reason'] = review
                runs.append(run)
                entry.update(Experiment=run['id'], Group=run['group'])
            except (OSError, ValueError, TypeError, KeyError) as exc:
                reason = str(exc)
        entry.update(Reason=reason)
        if reason:
            entry['Group'] = '제외'
        attempts.append(entry)
    return runs, attempts, selected
