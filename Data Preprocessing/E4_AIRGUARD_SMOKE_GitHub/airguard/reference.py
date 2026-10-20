"""이전 전처리 결과와의 선택적 비교. 이미 처리된 PM에 필터를 재적용하지 않습니다."""
from collections import defaultdict
import statistics

from .analysis import held, window
from .common import EXHAUST, INTAKE, PM, digest, interval, number, read_csv, write_csv
from .reports import chart, overview, page, series, table


def read_reference(root, config):
    folder = next((root/name for name in ('02_팬시작0초_회복분석', '04_팬시작0초_회복분석') if (root/name).is_dir()), None)
    if folder is None:
        raise ValueError('--reference에는 팬 시작 기준 CSV 폴더가 포함된 분석 결과를 지정하세요.')
    runs = []
    # 참고용 하위 폴더는 기본 비교에 자동 포함하지 않습니다.
    for path in sorted(folder.glob('*__팬가동이후.csv')):
        source = read_csv(path)
        if not source or not all(k in source[0] for k in ('Time[s]', PM, INTAKE, EXHAUST)):
            continue
        rows = [dict(r, **{k: number(r.get(k)) for k in ('Time[s]', PM, INTAKE, EXHAUST)}) for r in source]
        commands = [(r[INTAKE], r[EXHAUST]) for r in rows if r[INTAKE] is not None and r[EXHAUST] is not None
                    and (r[INTAKE], r[EXHAUST]) != (0, 0)]
        if not commands or any(p != commands[0] for p in commands):
            continue
        gap = config['gap_factor']*interval([{'t': r['Time[s]']} for r in rows])
        w = window(rows, config, gap)
        stop = w['target_confirm_index'] if w['target_confirm_index'] is not None else len(rows)-1
        aligned = [dict(r, **{'Time_From_Anchor[s]': round(r['Time[s]']-w['anchor'], 6) if r['Time[s]'] is not None else None})
                   for r in rows[w['anchor_index']:stop+1]] if w['anchor_index'] is not None else []
        _, confirmed = held(rows, config['pm_target'], config['hold_seconds'], gap)
        metrics = {'Fan_to_PM50_Confirm_s': rows[confirmed]['Time[s]'] if confirmed is not None else None,
                   'PM500_to_PM50_s': w['target']-w['anchor'] if w['target'] is not None else None}
        runs.append(dict(id='reference_'+path.stem, trial=path.stem, date='이전', group='기본비교용',
                         pair=commands[0], fan=rows, aligned=aligned, gap=gap, metrics=metrics,
                         source=path.relative_to(root).as_posix(), sha256=digest(path)))
    if not runs:
        raise ValueError('--reference에서 비교 가능한 팬 기준 CSV를 찾지 못했습니다.')
    return runs


def write_reference(out, runs, previous):
    dest = out/'04_데이터분석_요약/이전자료_참고비교'
    current = [r for r in runs if r['group'] == '기본비교용']
    grouped = defaultdict(lambda: {'이전': [], '현재': []})
    for label, batch in [('이전', previous), ('현재', current)]:
        for r in batch:
            grouped[r['pair']][label].append(r)
    records = []
    for pair, batches in sorted(grouped.items()):
        row = dict(Intake_PWM=pair[0], Exhaust_PWM=pair[1])
        for key in ('Fan_to_PM50_Confirm_s', 'PM500_to_PM50_s'):
            medians = {}
            for label, batch in batches.items():
                values = [r['metrics'][key] for r in batch if r['metrics'][key] is not None]
                medians[label] = statistics.median(values) if values else None
                row[label+'_'+key+'_Median'] = medians[label]
                row[label+'_'+key+'_n'] = len(values)
            a, b = medians['이전'], medians['현재']
            row[key+'_Change_percent'] = 100*(b-a)/a if a is not None and a > 0 and b is not None else None
        records.append(row)
    write_csv(dest/'동일조건_회복시간_비교.csv', records)
    write_csv(dest/'참고입력_해시.csv', [{'Source': r['source'], 'SHA256': r['sha256']} for r in previous])
    body = '<p>이전/현재 모두 저장된 전처리 PM을 같은 유지 기준으로 평가합니다. 필터를 재적용하지 않습니다. '
    body += '입력의 원래 전처리 설정/실험 절차가 같다는 보장은 없으므로 각 결과의 설정을 확인하세요. 차이율은 (현재−이전)/이전입니다.</p>'
    for section, xkey, xlabel in [('fan', 'Time[s]', '팬 시작 이후 경과시간 [s]'),
                                  ('aligned', 'Time_From_Anchor[s]', 'PM500 미만 유지 구간 시작 이후 [s]')]:
        items = []
        for label, batch in [('이전', previous), ('현재', current)]:
            ss = series(batch, section, xkey=xkey)
            for s in ss:
                s['label'] = label+' '+s['label']
                if label == '이전':
                    s['dash'] = '3 5' if not s['dash'] else '9 3 2 3'
            items.extend(ss)
        svg = overview(items, '이전/현재 '+('팬 시작 비교' if section == 'fan' else 'PM500→50 비교'), xlabel)
        (dest/(section+'_9조건비교.svg')).write_text(svg, encoding='utf-8')
        body += svg
    page(dest/'00_이전현재_비교.html', '이전/현재 동일 팬 조건 비교', body+table(records, list(records[0]) if records else []))
