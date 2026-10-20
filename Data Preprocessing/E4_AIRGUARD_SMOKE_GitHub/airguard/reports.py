"""같은 공통 데이터에서 CSV·SVG·HTML·처리 설명을 생성합니다. 외부 CDN은 사용하지 않습니다."""
from collections import Counter, defaultdict
import html
import json
import math
import statistics
from urllib.parse import quote

from . import __version__
from .common import COLUMNS, EXHAUST, FOLDERS, INTAKE, PM, RAW, write_csv, write_json

COLORS = {(35, 35): '#2469ae', (35, 60): '#7c8f00', (35, 100): '#8b46a8',
          (60, 35): '#a66a00', (60, 60): '#179447', (60, 100): '#ce4d8a',
          (100, 35): '#008b8b', (100, 60): '#ea7600', (100, 100): '#d53232'}
DASHES = ('', '9 4', '2 4', '9 3 2 3')


def esc(value):
    return html.escape('' if value is None else str(value))


def link(path, label):
    return f'<a href="{quote(path, safe="/")}">{esc(label)}</a>'


def page(path, title, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('<!doctype html><html lang="ko"><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    f'<title>{esc(title)}</title><style>'
                    'body{font:16px/1.65 "Malgun Gothic",sans-serif;color:#172b42;max-width:1450px;margin:32px auto;padding:0 24px}'
                    'a{color:#1752a4}svg{width:100%;height:auto}table{border-collapse:collapse;font-size:14px}'
                    'td,th{padding:8px;border:1px solid #ccd5de;text-align:left}th{background:#edf3f8}'
                    '.scroll{overflow:auto}.note{padding:16px;background:#f0f5fa;border-left:4px solid #356eb5}'
                    'h1{font-size:26px}h2{margin-top:36px}li{margin:6px 0}</style>'
                    f'<h1>{esc(title)}</h1>{body}</html>', encoding='utf-8')


def table(rows, columns):
    return '<div class="scroll"><table><thead><tr>'+''.join(f'<th>{esc(c)}</th>' for c in columns)+'</tr></thead><tbody>'+''.join(
        '<tr>'+''.join(f'<td>{esc(round(r[c], 4) if isinstance(r.get(c), float) else r.get(c))}</td>' for c in columns)+'</tr>'
        for r in rows)+'</tbody></table></div>'


def series(runs, section='fan', key=PM, xkey='Time[s]'):
    counts, result = Counter(), []
    for run in sorted(runs, key=lambda r: (tuple(-1 if p is None else p for p in r['pair']), r['date'], r['id'])):
        rows = run[section]
        if not rows:
            continue
        pair = run['pair']
        counts[pair] += 1
        repeat = counts[pair]
        label = '/'.join('?' if p is None else f'{p:g}' for p in pair)
        result.append(dict(label=f'I/E {label} · {repeat}회', color=COLORS.get(pair, '#475569'),
                           dash=DASHES[(repeat-1) % len(DASHES)], pair=pair, gap=run['gap'],
                           points=[(r.get(xkey), r.get(key)) for r in rows],
                           detail=f'{run["date"]} / {run["trial"]} / {run["group"]}'))
    return result


def chart(items, title, xlabel='팬 시작 이후 경과시간 [s]', ymax=500, width=1100, xmax_limit=None):
    """시간 공백/역행/무효값에서 선을 끊습니다. 선이 잘려도 값을 보간하지 않습니다."""
    values = [t for item in items for t, v in item['points'] if t is not None]
    xmin, xmax = min([0]+values), max([1]+values)
    if xmax_limit is not None:
        xmax = max(xmax, xmax_limit)
    ymax = max(1, ymax)
    left, right, top, bottom = 80, width-35, 50, 355
    legend_rows = math.ceil(len(items)/3)
    height = 435+legend_rows*30
    x = lambda v: left+(v-xmin)/(xmax-xmin)*(right-left)
    y = lambda v: bottom-v/ymax*(bottom-top)
    pieces = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}" '
              'style="font-family:Malgun Gothic,Arial,sans-serif;font-size:14px">',
              f'<rect width="{width}" height="{height}" fill="white"/>',
              f'<text x="{left}" y="26" font-size="20" font-weight="bold">{esc(title)}</text>']
    for k in range(6):
        value, t = ymax*k/5, xmin+(xmax-xmin)*k/5
        pieces.append(f'<path d="M{left} {y(value):.2f}H{right}" stroke="#e1e7ed"/>'
                      f'<text x="{left-12}" y="{y(value)+5:.2f}" text-anchor="end">{value:g}</text>'
                      f'<text x="{x(t):.2f}" y="380" text-anchor="middle">{t:.0f}</text>')
    if ymax >= 50:
        pieces.append(f'<path d="M{left} {y(50):.2f}H{right}" stroke="#64748b" stroke-dasharray="3 4"/>')
    for idx, item in enumerate(items):
        chunks, previous = [], None
        def flush():
            if chunks:
                pieces.append(f'<polyline points="{" ".join(chunks)}" fill="none" stroke="{item["color"]}" '
                              f'stroke-width="2.4" stroke-dasharray="{item["dash"]}"><title>{esc(item.get("detail", item["label"]))}</title></polyline>')
        points = item['points']
        for j, (t, value) in enumerate(points):
            if t is None or value is None or value < 0 or value > ymax:
                flush(); chunks = []; previous = None
                continue
            if previous is not None and not 0 < t-previous <= item['gap']:
                flush(); chunks = []
            xx, yy = x(t), y(value)
            chunks.append(f'{xx:.2f},{yy:.2f}')
            if j % max(1, len(points)//12) == 0:
                shape = (35, 60, 100).index(item['pair'][1]) if item['pair'][1] in (35, 60, 100) else 0
                if shape == 0:
                    mark = f'<circle cx="{xx:.2f}" cy="{yy:.2f}" r="3.3"'
                elif shape == 1:
                    mark = f'<rect x="{xx-3:.2f}" y="{yy-3:.2f}" width="6" height="6"'
                else:
                    mark = f'<polygon points="{xx:.2f},{yy-4:.2f} {xx-4:.2f},{yy+3:.2f} {xx+4:.2f},{yy+3:.2f}"'
                pieces.append(mark+f' fill="white" stroke="{item["color"]}" stroke-width="1.8"/>')
            previous = t
        flush()
        lx, ly = left+(idx % 3)*(width-100)/3, 435+(idx//3)*30
        pieces.append(f'<path d="M{lx:.2f} {ly-5}h30" stroke="{item["color"]}" stroke-width="3" stroke-dasharray="{item["dash"]}"/>'
                      f'<text x="{lx+38:.2f}" y="{ly}">{esc(item["label"])}</text>')
    pieces.extend([f'<text x="{width/2}" y="410" text-anchor="middle">{esc(xlabel)}</text>',
                   '<text x="16" y="215" transform="rotate(-90 16 215)" text-anchor="middle">PM2.5 [µg/m³]</text>', '</svg>'])
    return ''.join(pieces)


def overview(items, title, xlabel):
    panels = []
    common_max = max([1]+[t for r in items for t, v in r['points'] if t is not None])
    for intake in (35, 60, 100):
        for exhaust in (35, 60, 100):
            selected = [r for r in items if r['pair'] == (intake, exhaust)]
            panels.append(chart(selected, f'흡기 {intake}% / 배기 {exhaust}%', xlabel, width=760, xmax_limit=common_max))
    # 중첩 SVG는 원본 숫자 좌표를 유지하며 독립 패널로 배치합니다.
    h = max(500, 435+max((math.ceil(sum(r['pair'] == p for r in items)/3) for p in COLORS), default=0)*30)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2280 {h*3+60}">',
             f'<rect width="2280" height="{h*3+60}" fill="white"/>',
             f'<text x="35" y="38" font-family="Malgun Gothic,sans-serif" font-size="28">{esc(title)}</text>']
    for i, svg in enumerate(panels):
        parts.append(svg.replace('<svg ', f'<svg x="{(i%3)*760}" y="{60+(i//3)*h}" width="760" height="{h}" ', 1))
    return ''.join(parts)+'</svg>'


def write_report(out, runs, attempts, config, selected):
    for folder in FOLDERS:
        (out/folder).mkdir(parents=True, exist_ok=True)
    full_dir, fan_dir, aligned_dir, summary_dir, audit_dir = [out/f for f in FOLDERS]
    summaries, changes, issues, manifests, metadata, telemetry, aligned_all = [], [], [], [], [], [], []
    basic = [r for r in runs if r['group'] == '기본비교용']
    for run in runs:
        sub = '' if run['group'] == '기본비교용' else '조건변경_참고용'
        filename = run['id']+'.csv'
        full_path = full_dir/sub/filename
        fan_path = fan_dir/sub/(run['id']+'__팬가동이후.csv')
        write_csv(full_path, run['full'], COLUMNS)
        if run['fan']:
            write_csv(fan_path, run['fan'], ['Time[s]', 'Original_Time[s]']+[c for c in COLUMNS if c != 'Time[s]'])
        row = dict(Experiment=run['id'], Trial=run['trial'], Date=run['date'], Format=run['format'],
                   Series=run['series'], Group=run['group'], Intake_PWM=run['pair'][0], Exhaust_PWM=run['pair'][1],
                   Input_Rows=run['input_count'], Full_Rows=run['full_rows'], Fan_Rows=run['fan_rows'],
                   Median_Interval_s=run['step'], Fan_Start_Check=run['fan_reason'],
                   Assigned_Split=run['meta'].get('trial', {}).get('split', ''),
                   Acquisition_Flags=';'.join(run['meta'].get('status', {}).get('flags') or []),
                   Source=run['source'], CSV=full_path.relative_to(out).as_posix(),
                   Fan_CSV=fan_path.relative_to(out).as_posix() if run['fan'] else '', **run['metrics'])
        summaries.append(row)
        changes.extend(dict(Experiment=run['id'], **v) for v in run['changes'])
        issues.extend(dict(Experiment=run['id'], **v) for v in run['issues'])
        manifests.extend(dict(Experiment=run['id'], **v) for v in run['files'])
        telemetry.extend(dict(Experiment=run['id'], **v) for v in run['telemetry'])
        metadata.append(dict(Experiment=run['id'], **run['meta']))
        aligned_all.extend(dict(Experiment=run['id'], Group=run['group'], **{k: v for k, v in r.items() if not k.startswith('_')}) for r in run['aligned'])
        # 전처리 전후는 원래 출력 범위를 포함해 보여줍니다. 500 이상 원값을 숨기지 않습니다.
        old = series([run], 'full', RAW)
        new = series([run], 'full')
        if old:
            old[0].update(label='원측정 PM', color='#8994a5', dash='')
        if new:
            new[0].update(label='전처리 PM', color='#d53232', dash='')
        ymax = max([500]+[r[RAW] for r in run['full'] if r[RAW] is not None])*1.05
        svg = chart(old+new, f'{run["trial"]} · 전처리 전후', '기록 시작 이후 경과시간 [s]', ymax=ymax)
        page(audit_dir/'전처리전후_그래프'/(run['id']+'.html'), run['id'],
             '<p>회색: 유효 원측정값 · 빨강: 전처리값. 결측/오류/시간 공백은 선을 끊습니다.</p>'+svg)
        page(summary_dir/'개별실험_그래프'/(run['id']+'.html'), run['id'],
             f'<p>비교 분류: {esc(run["group"])} · {esc(run["fan_reason"])}</p>'
             +chart(series([run]), '팬 시작 기준', ymax=500)+table([row], ['Fan_to_PM50_Confirm_s', 'PM500_to_PM50_s', 'Recovery_Status']))
    write_csv(summary_dir/'실험별_요약.csv', summaries)
    write_csv(aligned_dir/'PM500_PM50_도달시간.csv', summaries,
              ['Experiment', 'Group', 'Intake_PWM', 'Exhaust_PWM', 'Anchor_From_Fan_Start_s', 'Anchor_Confirm_s',
               'Actual_Anchor_PM[ug/m3]', 'Target_From_Fan_Start_s', 'Target_Confirm_s', 'PM500_to_PM50_s', 'Recovery_Status'])
    write_csv(aligned_dir/'PM500_PM50_정렬데이터.csv', aligned_all,
              ['Experiment', 'Group', 'Time_From_Anchor[s]', 'Time[s]', 'Original_Time[s]']+COLUMNS[:1]+COLUMNS[2:])
    write_csv(audit_dir/'01_입력파일과_선택제외사유.csv', attempts)
    write_csv(audit_dir/'02_원본파일_해시.csv', manifests)
    write_csv(audit_dir/'03_전처리_변경값.csv', changes, ['Experiment', 'Line', 'Time[s]', 'Operation', 'Before', 'After'])
    write_csv(audit_dir/'04_결측과오류_점검.csv', issues, ['Experiment', 'Line', 'Time[s]', 'Reason', 'Detail'])
    write_json(audit_dir/'05_적용설정.json', dict(code_version=__version__, output_schema='1.0', selected_series=selected, **config))
    write_json(audit_dir/'06_실험메타데이터.json', metadata)
    if telemetry:
        write_csv(audit_dir/'팬RPM_원측정기록.csv', telemetry)
    write_csv(audit_dir/'AI학습_실험단위_분할목록.csv', summaries, ['Experiment', 'Date', 'Series', 'Group', 'Assigned_Split', 'CSV', 'Fan_CSV'])
    panels = [('fan', fan_dir, '팬 시작 이후 경과시간 [s]', '팬 시작 기준 회복 비교'),
              ('aligned', aligned_dir, 'PM500 미만 유지 구간 시작 이후 [s]', 'PM500→PM50 감소 비교'),
              ('full', summary_dir, '기록 시작 이후 경과시간 [s]', '전체 측정 비교')]
    for section, dest, xlabel, title in panels:
        items = series(basic, section, xkey='Time_From_Anchor[s]' if section == 'aligned' else 'Time[s]')
        all_svg = chart(items, title, xlabel)
        grid_svg = overview(items, title+' · 9조건', xlabel)
        (dest/'00_PM25_팬조건_전체비교.svg').write_text(all_svg, encoding='utf-8')
        (dest/'01_PM25_9조건비교.svg').write_text(grid_svg, encoding='utf-8')
        note = ('<p class="note">기본비교용 회차만 표시합니다. 참고용 회차는 실험별 요약/개별 그래프에서 확인하세요. '
                '동일한 I/E 조합은 같은 색, 반복 회차는 선 스타일을 바꿉니다. 마크: 배기35 ○ / 60 □ / 100 △.</p>')
        page(dest/'00_여기부터.html', title, note+grid_svg+'<h2>전체 조건 겹쳐보기</h2>'+all_svg)
        for intake in (35, 60, 100):
            (dest/f'흡기{intake}_고정비교.svg').write_text(chart([r for r in items if r['pair'][0] == intake], f'흡기 {intake}% 고정', xlabel), encoding='utf-8')
    groups = defaultdict(list)
    for row in summaries:
        if row['Group'] == '기본비교용':
            groups[(row['Intake_PWM'], row['Exhaust_PWM'])].append(row)
    grouped = []
    for pair, values in sorted(groups.items()):
        row = dict(Intake_PWM=pair[0], Exhaust_PWM=pair[1], Runs=len(values))
        for key in ('Fan_to_PM50_Confirm_s', 'PM500_to_PM50_s'):
            vv = [r[key] for r in values if r[key] is not None]
            row.update({key+'_Valid_n': len(vv), key+'_Median': statistics.median(vv) if vv else None,
                        key+'_Min': min(vv) if vv else None, key+'_Max': max(vv) if vv else None})
        grouped.append(row)
    write_csv(summary_dir/'팬조건별_회복시간_요약.csv', grouped)
    links = '<ul>'+''.join('<li>'+link(f'{folder}/00_여기부터.html', folder)+'</li>' for folder in FOLDERS[1:4])+'</ul>'
    links += '<p>'+link('04_데이터분석_요약/실험별_요약.csv', '실험별 요약 CSV')+' · '+link('전처리_및_분석_설명.md', '전처리와 칼럼 설명')+'</p>'
    explanation = ('<p class="note">PM은 입자 질량농도입니다. 500은 분석 상한이며 실제 농도나 센서의 절대 한계를 뜻하지 않습니다. '
                   '회복 기준 PM50은 프로젝트 분석 기준입니다. PWM은 전력 측정값이 아닙니다. '
                   '회복시간 공백은 관측/유지 확인 부족일 수 있으며 실패나 0초로 바꾸지 않습니다.</p>')
    page(out/'00_분석결과_여기부터.html', 'E4-AIRGUARD SMOKE 분석',
         f'<p>처리 {len(runs)}회 · 기본비교 {len(basic)}회 · 참고용 {len(runs)-len(basic)}회</p>'+explanation+links
         +table(summaries, ['Trial', 'Date', 'Group', 'Intake_PWM', 'Exhaust_PWM', 'Full_Rows', 'Fan_to_PM50_Confirm_s', 'PM500_to_PM50_s']))
    # 처리기록에서도 처음 보는 사람이 각 파일을 찾을 수 있게 목차를 제공합니다.
    page(audit_dir/'00_처리기록_안내.html', '처리기록', '<ul>'+''.join('<li>'+link(p.name, p.name)+'</li>' for p in sorted(audit_dir.iterdir()) if p.is_file())+'</ul>'
         +'<h2>전처리 전후 그래프</h2><ul>'+''.join('<li>'+link('전처리전후_그래프/'+r['id']+'.html', r['id'])+'</li>' for r in runs)+'</ul>')
    (out/'전처리_및_분석_설명.md').write_text(description(config), encoding='utf-8')
    return summaries


def description(config):
    return f'''# E4-AIRGUARD SMOKE 전처리 및 분석

원본은 변경하지 않습니다. 입력은 CSV 센서 기록 또는 JSONL 이벤트/센서 기록입니다.
PM2.5는 입자 질량농도이며 가스 농도가 아닙니다.

## 처리 기준
- 빈값, 숫자 변환 불가, 무한대, 음수 PM, 0~100 밖 PWM, 시각/순서/공백을 검사합니다.
- 평활화: 후행 {config['median_window']}표본 중앙값. 창이 채워지기 전에는 원값을 보존합니다.
- PWM 변경, 오류, 역행, 대표 간격의 {config['gap_factor']}배를 넘는 공백에서 필터를 초기화합니다. JSONL은 phase/seq 경계도 확인합니다.
- 필터 이후 PM500 초과를 500으로 제한합니다. 원값은 Raw 칼럼과 RAW에 남습니다.
- 실제 측정 0은 보존합니다. 결측 보간, 임의 시간 이동, RPM 추정, 원하는 곡선으로의 보정은 하지 않습니다.
- 무효행 정책: `{config['invalid_rows']}`. legacy는 기존 결과 재현을 위해 CSV의 필수값 오류행을 제외하고 JSONL 무효 PM은 공백으로 보존합니다. drop은 필수값 오류행 제외, keep은 무효값을 공백으로 보존합니다. 변경/제외 내역은 05에서 확인합니다.
- JSONL RPM은 별도 비동기 측정입니다. PM 시각에 임의로 붙이지 않고 05에 별도로 보관합니다.

## 결과 폴더
| 폴더 | 내용 |
|---|---|
| 01_전체구간_데이터전처리 | 전체 측정의 전처리 CSV |
| 02_팬시작0초_회복분석 | 검증한 팬 명령 시각 기준 CSV와 그래프 |
| 03_PM500_PM50_감소비교 | 상한 이탈 이후 감소 비교, 실제 시작 농도, 유지 확인 시각 |
| 04_데이터분석_요약 | 전체/개별/9조건 그래프, 회차 및 반복 요약 |
| 05_전처리검증_및_처리이력 | 선택/제외 사유, 입력 SHA256, 값 변경, 오류, 설정, 메타데이터 |

## 시계열 CSV 칼럼
| 칼럼 | 의미 |
|---|---|
| Measurement_Timestamp | 원본 측정 날짜·시각 |
| Time[s] | 01: 첫 PM 기록 기준, 02: 팬 시작 이벤트 기준 경과초 |
| Original_Time[s] | 02에만 포함. 01의 경과시간 |
| PM2.5[ug/m3] | 평활화와 상한을 적용한 PM |
| PM2.5_Raw[ug/m3] | 유효한 원측정 PM. 수치 오류는 공백과 처리기록으로 남음 |
| PM_Capped[0/1] | 필터 결과에 500 상한이 적용되면 1 |
| Intake_PWM[%], Exhaust_PWM[%] | 흡기/배기 명령. JSONL은 직전 command 기록 |

## 회복 기준
팬 기준 주요 지표는 PM50 이하가 {config['hold_seconds']}초 유지되었음을 확인한 시각입니다.
도달 구간 시작(Arrival)과 유지 확인(Confirm)을 모두 기록합니다.
PM500→50은 마지막 상한 표본 이후 PM500 미만 {config['hold_seconds']}초 유지 구간의 시작부터,
PM50 이하 {config['hold_seconds']}초 유지 구간의 시작까지입니다. 보간한 가짜 PM500 표본을 만들지 않습니다.
목표 미관측/유지 미확인은 빈 시간과 사유로 남깁니다. 측정 종료 조건과 분석 기준은 다를 수 있습니다.

## AI 학습
01/02/03은 같은 실험의 전체/부분 자료입니다. 중복 학습하거나 같은 실험을 학습/시험 양쪽에 나누지 마세요.
날짜·측정 방식·원본 회차·development/holdout 지정은 요약/메타데이터에 보존합니다.
조건변경_참고용은 기본 비교에서 분리합니다. 센서 오류 플래그가 없다고 측정 정확성이 보장되지는 않습니다.
회복시간은 미래 유지 구간을 보고 만든 사후 평가값입니다. 실시간 예측 입력에 넣지 마세요.
PM 상한 때문에 고농도 정보 일부가 사라지며, 팬 시작 지연/연기 투입량/주변 환경 차이는 시간 정렬만으로 사라지지 않습니다.
'''
