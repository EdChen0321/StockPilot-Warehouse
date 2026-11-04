"""Real localhost HTTP contract checks using a disposable database/catalogue."""
import base64
import json
import threading
from http.client import HTTPConnection
from ops.server import create_server
from test_kol_excel_import import ImportFixture
from test_kol_excel_cleaning import line, workbook_bytes

class ExcelHttpTests(ImportFixture):
    def setUp(self):
        super().setUp()
        self.server=create_server(self.dbpath,0,kol_catalog=self.catalog_path)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self,path,body=None,headers=None):
        conn=HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        h={'Content-Type':'application/json','X-Requested-With':'operations-ui'};h.update(headers or {})
        conn.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,h)
        response=conn.getresponse(); status=response.status; raw=response.read();conn.close()
        return status,json.loads(raw.decode('utf-8-sig'))

    def body(self):
        return dict(account_id='demo-sl',filename='synthetic.xlsx',content_base64=base64.b64encode(workbook_bytes([line()])).decode('ascii'))

    def test_preview_confirm_list_detail_and_retry(self):
        status,b=self.request('/api/kol-import/preview',self.body())
        self.assertEqual(status,200); self.assertTrue(b['can_confirm'])
        status,listing=self.request('/api/kol-import/batches')
        self.assertEqual(status,200)
        self.assertIn(b['id'],[item['id'] for item in listing['batches']])
        status,detail=self.request('/api/kol-import/batches/'+b['id'])
        self.assertEqual(status,200);self.assertEqual(detail['id'],b['id'])
        self.assertNotIn('content_base64',detail)
        for _ in range(2):
            status,confirmed=self.request('/api/kol-import/confirm',{'batch_id':b['id']})
            self.assertEqual(status,200);self.assertEqual(confirmed['state'],'confirmed')
        self.assertEqual(self.request('/api/kol-import/batches/missing')[0],404)

    def test_origin_guard_and_invalid_base64(self):
        self.assertEqual(self.request('/api/kol-import/preview',self.body(),{'Origin':'https://other.test'})[0],403)
        body=self.body();body['content_base64']='%%%invalid'
        status,result=self.request('/api/kol-import/preview',body)
        self.assertEqual(status,400);self.assertIn('error',result)
        body=self.body();body['catalog_path']='C:/untrusted.xlsx'
        self.assertEqual(self.request('/api/kol-import/preview',body)[0],400)

    def test_malformed_worksheet_returns_json_error(self):
        from test_kol_excel_reader_edges import rewrite_sheet
        body=self.body()
        raw=rewrite_sheet(base64.b64decode(body['content_base64']),lambda data:b'<broken')
        body['content_base64']=base64.b64encode(raw).decode('ascii')
        status,result=self.request('/api/kol-import/preview',body)
        self.assertEqual(status,400)
        self.assertIn('error',result)
