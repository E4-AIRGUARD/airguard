"""입출력·수치 검사 공통 함수. 원본 파일은 읽기만 합니다."""
import csv
import hashlib
import json
import math
import re
import statistics
from datetime import datetime
from pathlib import Path

PM = 'PM2.5[ug/m3]'
RAW = 'PM2.5_Raw[ug/m3]'
INTAKE = 'Intake_PWM[%]'
EXHAUST = 'Exhaust_PWM[%]'
COLUMNS = ['Measurement_Timestamp', 'Time[s]', PM, RAW, 'PM_Capped[0/1]', INTAKE, EXHAUST]
FOLDERS = ['01_전체구간_데이터전처리', '02_팬시작0초_회복분석',
           '03_PM500_PM50_감소비교', '04_데이터분석_요약', '05_전처리검증_및_처리이력']


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def valid(value, upper=None):
    value = number(value)
    return value if value is not None and value >= 0 and (upper is None or value <= upper) else None


def timestamp(value):
    return datetime.fromisoformat(str(value))


def read_json(path):
    result = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(result, dict):
        raise ValueError(f'JSON object required: {path.name}')
    return result


def read_csv(path):
    # 기존 CSV의 UTF-8/BOM 및 Windows CP949 형식을 지원합니다.
    for encoding in ('utf-8-sig', 'cp949'):
        try:
            with path.open(encoding=encoding, newline='') as stream:
                return list(csv.DictReader(stream))
        except UnicodeDecodeError:
            continue
    raise ValueError(f'CSV 인코딩 확인 필요: {path.name}')


def write_csv(path, rows, columns=None):
    rows = list(rows)
    columns = columns or list(dict.fromkeys(k for row in rows for k in row)) or ['Status']
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def interval(samples):
    times = [r.get('t') for r in samples]
    steps = [b-a for a, b in zip(times, times[1:]) if a is not None and b is not None and b > a]
    return statistics.median(steps) if steps else 1.0


def safe_name(value):
    return re.sub(r'[^\w.\-]+', '_', str(value), flags=re.UNICODE).strip('._')[:100] or 'run'


def unique_output(base, input_root):
    base, input_root = base.resolve(), input_root.resolve()
    if base == input_root or input_root in base.parents or base in input_root.parents:
        raise ValueError('결과 폴더는 RAW와 서로 포함되지 않는 별도 경로로 지정하세요.')
    target, n = base, 2
    while target.exists():
        target = base.with_name(f'{base.name}_{n:02d}')
        n += 1
    target.mkdir(parents=True)
    (target / '.e4_airguard_generated').write_text('1\n', encoding='utf-8')
    return target
