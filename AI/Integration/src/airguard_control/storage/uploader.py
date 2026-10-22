"""로컬 대기열(pending.sqlite3)을 Aiven MySQL(e4_recovery_events / e4_recovery_samples)로 올린다. 팀 DB 문서 6절의 방식을 따른다.
  - 최대 200개를 읽어 한 트랜잭션으로 UPSERT, commit 성공 뒤에만 로컬 기록 삭제, 실패 시 rollback하고 큐 유지 (같은 UUID는 UPSERT라 중복 행이 안 생김)
실행: python3 uploader.py pending.sqlite3 [--once]       (접속 정보는 환경변수. 코드·저장소에 넣지 않음)
  E4_CATEGORY_COLUMN=1 (events 테이블에 category 열을 추가한 경우만)
  DB_HOST DB_PORT DB_USER DB_PASSWORD DB_NAME DB_CA(CA 인증서 경로, 선택)
필요 패키지: pymysql  (pip install pymysql)
기존 테이블이 구성된 DB를 사용한다. 이 스크립트는 스키마를 생성하거나 변경하지 않는다.
원본은 2026-10-21 실제 PM 업로드를 확인했고, 이 경로에서는 모의 연결로 회귀 검증한다.
"""
import argparse, json, os, sqlite3, time
from airguard_control.paths import RUNTIME_DIR
from datetime import datetime, timezone
import sys
from contextlib import contextmanager
from pathlib import Path

EVENT_COLS = ['event_id', 'device_id', 'started_at', 'first_reached_at', 'recovered_at', 'recording_ended_at', 'status', 'recovery_seconds', 'config_json']
SAMPLE_COLS = ['event_id', 'sample_id', 'measured_at', 'temperature', 'humidity', 'gas', 'gas_unit', 'pm25', 'intake_pwm', 'exhaust_pwm', 'heat_load', 'exhaust_rpm', 'intake_rpm', 'valid_json', 'phase']


def upsert_sql(table, cols, keys):
    upd = ', '.join('%s=VALUES(%s)' % (c, c) for c in cols if c not in keys)
    return 'INSERT INTO %s (%s) VALUES (%s) ON DUPLICATE KEY UPDATE %s' % (table, ', '.join(cols), ', '.join(['%s'] * len(cols)), upd)


if os.environ.get('E4_CATEGORY_COLUMN') == '1':   # 운영 DB에 category 열이 이미 있을 때만 사용
    EVENT_COLS = EVENT_COLS + ['category']

SQL = {'event': ('e4_recovery_events', EVENT_COLS, ['event_id']), 'sample': ('e4_recovery_samples', SAMPLE_COLS, ['event_id', 'sample_id']),
       'photo': ('e4_recovery_photos', ['photo_id','event_id','captured_at','stage','relative_path'], ['photo_id'])}


def normalize_payload(kind, payload):
    p = dict(payload)
    if kind == 'event':
        config = p.get('config_json') or {}
        if isinstance(config, str): config = json.loads(config)
        if p.get('event_type'): config.setdefault('event_type', p['event_type'])
        if p.get('category'): config.setdefault('category', p['category'])
        p['config_json'] = json.dumps(config, ensure_ascii=False, allow_nan=False)
    if kind == 'sample':
        valid = p.get('valid_json', p.get('valid', {}))
        p['valid_json'] = valid if isinstance(valid, str) else json.dumps(valid, allow_nan=False)
    for key in ('started_at','first_reached_at','recovered_at','recording_ended_at','measured_at','captured_at'):
        if p.get(key):
            stamp = datetime.fromisoformat(p[key].replace('Z', '+00:00'))
            if stamp.tzinfo is not None: stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
            p[key] = stamp
    return p


def flush(queue_db, conn, limit=200):
    rows = queue_db.execute('SELECT id, kind, payload FROM queue ORDER BY id LIMIT ?', (limit,)).fetchall()
    if not rows:
        return 0
    try:
        with conn.cursor() as cur:
            for _, kind, payload in rows:
                table, cols, keys = SQL[kind]
                p = normalize_payload(kind, json.loads(payload))
                cur.execute(upsert_sql(table, cols, keys), [p.get(c) for c in cols])
        conn.commit()
    except Exception:
        conn.rollback(); raise
    queue_db.executemany('DELETE FROM queue WHERE id=?', [(r[0],) for r in rows]); queue_db.commit()
    return len(rows)


def connect(env_file=None):
    from airguard_control.storage.aiven_connection import connect as aiven_connect
    return aiven_connect(env_file or RUNTIME_DIR/'aiven/.env')


@contextmanager
def queue_lock(path):
    """Single uploader per queue on both Windows development PCs and Jetson."""
    with open(str(path) + '.lock', 'a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0, 2)
            if lock.tell() == 0:
                lock.write(b'0'); lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('queue', nargs='?', type=Path, default=RUNTIME_DIR/'pm_pending.sqlite3')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--env-file', type=Path)
    args = parser.parse_args(argv)
    if not args.queue.is_file():
        parser.error('Queue does not exist; start the event recorder first')
    with queue_lock(args.queue):
        q = sqlite3.connect(args.queue)
        try:
            while True:
                conn = None
                failed = False
                try:
                    if q.execute('SELECT 1 FROM queue LIMIT 1').fetchone():
                        conn = connect(args.env_file) if args.env_file else connect()
                        while flush(q, conn):
                            pass
                except Exception as error:
                    failed = True
                    print('업로드 실패, 큐 유지:', type(error).__name__)
                finally:
                    if conn is not None:
                        conn.close()
                if args.once:
                    return 1 if failed else 0
                time.sleep(10)
        finally:
            q.close()


if __name__ == '__main__':
    raise SystemExit(main())
