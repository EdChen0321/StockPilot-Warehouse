import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch
from ops.server import create_server
from ops.ai_evaluation import read_run


class EvaluationHttpTests(unittest.TestCase):
    def test_readonly_routes_and_no_path_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            server=create_server(Path(temporary)/'isolated.db', 0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def get(path):
                connection=HTTPConnection('127.0.0.1',server.server_port)
                connection.request('GET',path)
                response=connection.getresponse();body=response.read();connection.close()
                return response.status,body
            try:
                with patch('ops.server.list_runs',return_value=[]):
                    status,body=get('/api/assistant/evaluations')
                    self.assertEqual(status,200);self.assertEqual(json.loads(body),{'runs':[]})
                with patch('ops.server.read_run',side_effect=lambda key:read_run(key,Path(temporary))):
                    self.assertEqual(get('/api/assistant/evaluations/../settings')[0],400)
                    self.assertEqual(get('/api/assistant/evaluations/missing')[0],404)
                self.assertEqual(get('/api/assistant/evaluations?file=settings')[0],400)
                for path in ('/ai-evaluation.js','/ai-evaluation.css'):
                    self.assertEqual(get(path)[0],200)
                self.assertIn(b'ai-evaluation',get('/')[1])
            finally:
                server.shutdown();server.server_close();thread.join()
