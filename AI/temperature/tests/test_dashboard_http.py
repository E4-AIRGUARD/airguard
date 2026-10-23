"""Exercise actual HTTP responses using an ephemeral loopback port."""
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import json
from urllib.request import urlopen, Request
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dashboard_api import make_server


class HttpTests(unittest.TestCase):
    def test_read_only_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'missing.db'
            server=make_server(path,port=0)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                for route in ('/api/temperature','/api/snapshot','/api/events?seconds=86400'):
                    with urlopen(base+route,timeout=3) as response:
                        self.assertEqual(response.status,200)
                        self.assertEqual(response.headers['Access-Control-Allow-Origin'],'http://127.0.0.1:8091')
                        json.load(response)
                for route,expected in (('/api/temperature?seconds=0',400),('/api/events?date=bad',400),('/private.db',404)):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(base+route,timeout=3)
                    self.assertEqual(error.exception.code,expected)
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base+'/api/temperature',data=b'{}',method='POST'),timeout=3)
                self.assertEqual(error.exception.code,405)
                self.assertFalse(path.exists())
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=3)


if __name__=='__main__': unittest.main()
