import importlib.util
import io
import json
from pathlib import Path
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from unittest.mock import patch
import unittest

root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('dashboard_run',root/'run.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)

class ProxyTests(unittest.TestCase):
    def setUp(self):
        handler=partial(app.DashboardHandler,database=root/'missing.db',power=None,
                        lease_key='test-only-key',control_url='http://jetson-test.invalid:8095/')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def post(self,origin,content_type='application/json'):
        return urlopen(Request(self.origin+'/api/dashboard-lease',data=json.dumps(dict(client='a'*16,action='heartbeat',sequence=1)).encode(),
            headers={'Origin':origin,'Content-Type':content_type}),timeout=3)
    def test_forwards_only_same_origin_with_server_credential(self):
        with patch.object(app,'urlopen',return_value=io.BytesIO(b'{"active":true}')) as remote:
            with self.post(self.origin) as r:self.assertTrue(json.load(r)['active'])
            request=remote.call_args.args[0]
            self.assertEqual(request.full_url,'http://jetson-test.invalid:8095/api/dashboard-lease')
            self.assertEqual(request.get_header('Authorization'),'Bearer test-only-key')
    def test_cross_origin_and_form_posts_rejected(self):
        with patch.object(app,'urlopen') as remote:
            with self.assertRaises(HTTPError) as e:self.post('http://untrusted.invalid')
            self.assertEqual(e.exception.code,403)
            with self.assertRaises(HTTPError) as e:self.post(self.origin,'text/plain')
            self.assertEqual(e.exception.code,415)
            remote.assert_not_called()

if __name__=='__main__':unittest.main()
