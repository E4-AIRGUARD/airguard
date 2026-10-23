"""대시보드 서버 (표준 라이브러리만). 제어기가 쓰는 에너지 로그 CSV의 마지막 N줄을 /api/log 로 내어준다.
실행: python3 serve_dashboard.py <에너지로그.csv> [포트=8080]   ->  브라우저에서 http://<젯슨IP>:8080
로그가 몇 MB로 커져도 파일 끝부분만 읽어서 가볍다."""
import http.server, os, sys, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'sample_energy_log.csv')
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8080


def tail_rows(path, n):
    """헤더 + 마지막 n줄. 끝에서 거꾸로 읽는다."""
    with open(path, 'rb') as f:
        header = f.readline()
        f.seek(0, os.SEEK_END)
        size, block, data = f.tell(), 65536, b''
        pos = size
        while pos > len(header) and data.count(b'\n') <= n:
            step = min(block, pos - len(header))
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
        lines = data.splitlines()
        if pos > len(header) and lines:      # 중간에서 잘린 첫 줄 버림
            lines = lines[1:]
    return header + b'\n'.join(lines[-n:]) + b'\n'


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path in ('/', '/index.html'):
            body, ctype = open(os.path.join(HERE, 'dashboard.html'), 'rb').read(), 'text/html; charset=utf-8'
        elif u.path == '/api/log':
            n = int(urllib.parse.parse_qs(u.query).get('rows', ['3600'])[0])
            try:
                body, ctype = tail_rows(LOG, max(1, min(n, 200000))), 'text/csv; charset=utf-8'
            except FileNotFoundError:
                self.send_error(404, 'log not found'); return
        else:
            self.send_error(404); return
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    print('로그: %s\n주소: http://0.0.0.0:%d' % (LOG, PORT))
    http.server.ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
