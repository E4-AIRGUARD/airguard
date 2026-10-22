"""Server-only Aiven settings. Never run schema initialization on connection."""
import os
from pathlib import Path
import ssl


def connect(env_file=None, *, readonly=False):
    import pymysql
    values = {}
    if env_file and Path(env_file).is_file():
        from dotenv import dotenv_values
        values.update(dotenv_values(env_file))
    values.update({key: value for key, value in os.environ.items() if key.startswith('DB_')})
    required = ('DB_HOST', 'DB_USER', 'DB_PASSWORD', 'DB_NAME')
    if any(not values.get(key) for key in required):
        raise ValueError('Incomplete DB configuration')
    ca = values.get('DB_CA_PATH') or values.get('DB_CA')
    if not ca:
        raise ValueError('A CA certificate is required')
    ca = Path(ca)
    if not ca.is_absolute():
        ca = (Path(env_file).parent if env_file else Path.cwd()) / ca
    context = ssl.create_default_context(cafile=str(ca))
    conn = pymysql.connect(host=values['DB_HOST'], port=int(values.get('DB_PORT', 3306)),
        user=values['DB_USER'], password=values['DB_PASSWORD'], database=values['DB_NAME'],
        charset='utf8mb4', ssl=context, connect_timeout=5, read_timeout=10, write_timeout=10,
        autocommit=False, cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("SET time_zone = '+00:00'")
            if readonly:
                cur.execute('START TRANSACTION READ ONLY')
        return conn
    except Exception:
        conn.close()
        raise
