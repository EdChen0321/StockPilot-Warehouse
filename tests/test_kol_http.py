import csv
import io
import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from ops.server import create_server
from test_orders import row, as_csv


class KolHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.server=create_server(Path(self.temp.name)/'http.db',0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.server.store.seed()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def request(self,path,body=None,headers=None):
        c=HTTPConnection('127.0.0.1',self.server.server_port)
        h={'Content-Type':'application/json','X-Requested-With':'operations-ui'}
        h.update(headers or {})
        c.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,h)
        r=c.getresponse();data=r.read().decode('utf-8-sig');status=r.status
        result=json.loads(data) if 'application/json' in r.getheader('Content-Type','') else data
        c.close();return status,result

    def test_api_export_and_validation(self):
        body=dict(operation_id='one',name='合成小晴',utms='sunny',started_at='2026-01-01T00:00:00+08:00')
        status,k=self.request('/api/kols',body)
        self.assertEqual(status,200)
        b=self.server.store.preview('demo-sl','test.csv',as_csv([row(utm_medium='sunny',order_total='960')]),True)
        self.server.store.confirm(b['id'])
        status,result=self.request('/api/kols/report?kol_id='+k['id'])
        self.assertEqual(status,200)
        self.assertEqual(result['summary']['total'],'960.00')
        self.assertEqual(result['filtered_summary']['total'],'960.00')
        status, empty = self.request('/api/kols/report?utm=not-present')
        self.assertEqual(status,200)
        self.assertEqual(empty['orders'],[])
        self.assertEqual(empty['filtered_summary']['order_count'],0)
        self.assertEqual(empty['filtered_summary']['total'],'0.00')
        self.assertEqual(empty['summary']['total'],'960.00')
        status,text=self.request('/api/kols/export?kol_id='+k['id'])
        self.assertEqual(status,200)
        self.assertEqual(list(csv.DictReader(io.StringIO(text)))[0]['total'],'960.00')
        status,filtered=self.request('/api/kols/report?platform=Other&status=all')
        self.assertEqual(status,200)
        self.assertEqual(filtered['summary']['order_count'],0)
        for kind in ('list','products','orders'):
            status,text=self.request('/api/kols/export?kind='+kind+'&kol_id='+k['id'])
            self.assertEqual(status,200)
            self.assertTrue(list(csv.DictReader(io.StringIO(text))))
        self.assertEqual(self.request('/api/kols/export?kind=invalid')[0],400)
        self.assertEqual(self.request('/api/kols/report?kind=orders')[0],400)
        for path in ['/api/kols/report?bad=1','/api/kols/report?start=2026-01-01&start=2026-02-01','/api/kols?bad=1']:
            self.assertEqual(self.request(path)[0],400)
        self.assertEqual(self.request('/api/kols',body,{'Origin':'https://other.test'})[0],403)
        self.assertEqual(self.request('/api/kols/delete',dict(operation_id='del',id=k['id'],expected_version=1))[0],200)
        self.assertEqual(self.request('/api/kols')[1]['kols'],[])

    def test_fixed_destinations_are_queryable_exportable_and_read_only(self):
        b=self.server.store.preview('demo-sl','general.csv',as_csv([row(utm_medium='',order_total='960')]),True)
        self.server.store.confirm(b['id'])
        status,data=self.request('/api/kols/report?kol_id=general&status=all')
        self.assertEqual(status,200)
        self.assertEqual(data['kol']['name'],'一般訂單')
        self.assertEqual(data['orders'][0]['kol_id'],'general')
        self.assertEqual(self.request('/api/kols/report?kol_id=general&status=excluded')[0],200)
        rows=list(csv.DictReader(io.StringIO(self.request('/api/kols/export?kind=list')[1])))
        self.assertEqual({r['id'] for r in rows},{'general','unattributed'})
        for key in ('general','unattributed'):
            body=dict(operation_id='edit-'+key,id=key,expected_version=1,name='bad',utms='bad',started_at='2026-01-01T00:00:00+08:00')
            self.assertEqual(self.request('/api/kols',body)[0],400)
            self.assertEqual(self.request('/api/kols/delete',dict(operation_id='delete-'+key,id=key,expected_version=1))[0],400)
        self.assertEqual(self.request('/api/kols')[1]['kols'],[])


if __name__=='__main__':unittest.main()
