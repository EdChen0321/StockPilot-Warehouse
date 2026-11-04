"""Durable upload receipts and recovery after losing the HTTP response."""
import base64
import socket
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from uuid import uuid4
from unittest.mock import patch

from ops.kol_import import KolImport
from ops.store import Store, ConflictError
from test_kol_excel_import import ImportFixture
import test_kol_excel_http as http_tests
from test_kol_excel_cleaning import line, workbook_bytes


class RecoveryTests(ImportFixture):
    def body(self):
        return dict(account_id='demo-sl',filename='synthetic.xlsx',upload_id=str(uuid4()),
                    content_base64=base64.b64encode(self.default_blob).decode())

    def test_saved_receipt_survives_restart_and_does_not_need_catalog_to_retry(self):
        body=self.body();batch=self.service.preview(body)
        restarted=KolImport(Store(self.dbpath),self.folder/'missing-catalog.xlsx')
        self.assertEqual(restarted.upload_status(body['upload_id']),dict(state='saved',batch_id=batch['id'],batch_state='preview'))
        self.assertEqual(restarted.preview(body)['id'],batch['id'])
        self.assertEqual(restarted.upload_status(str(uuid4())),dict(state='not_found'))

    def test_same_upload_id_cannot_be_reused_with_other_contents(self):
        body=self.body();batch=self.service.preview(body)
        for changes in ({'filename':'another.xlsx'},{'account_id':'demo-sp'},
                        {'content_base64':base64.b64encode(workbook_bytes([line(**{'訂單合計':1200})])).decode()}):
            with self.assertRaises(ConflictError):self.service.preview(dict(body,**changes))
        self.assertEqual(self.service.upload_status(body['upload_id'])['batch_id'],batch['id'])

    def test_retry_of_stale_receipt_does_not_refresh_its_baseline(self):
        original=self.preview();self.service.confirm({'batch_id':original['id']})
        body=self.body();body['content_base64']=base64.b64encode(workbook_bytes([line(**{'訂單合計':1200})])).decode()
        stale=self.service.preview(body)
        other=self.preview([line(**{'訂單合計':1500})]);self.service.confirm({'batch_id':other['id']})
        retry=self.service.preview(body)
        self.assertEqual(retry['id'],stale['id']);self.assertFalse(retry['can_confirm'])
        with self.assertRaises(ConflictError):self.service.confirm({'batch_id':retry['id']})

    def test_concurrent_same_id_creates_one_receipt_and_batch(self):
        body=self.body()
        with ThreadPoolExecutor(max_workers=2) as workers:
            results=list(workers.map(self.service.preview,[body,body]))
        self.assertEqual(results[0]['id'],results[1]['id'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM kol_import_batches').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM kol_upload_receipts').fetchone()[0],1)

    def test_invalid_upload_has_no_saved_receipt(self):
        body=self.body();body['content_base64']=base64.b64encode(b'not-an-xlsx').decode()
        with self.assertRaises(ValueError):self.service.preview(body)
        self.assertEqual(self.service.upload_status(body['upload_id']),dict(state='not_found'))

    def test_receipt_and_batch_commit_together(self):
        import sqlite3
        body=self.body()
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON kol_upload_receipts BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.DatabaseError):self.service.preview(body)
        self.assertEqual(self.service.upload_status(body['upload_id']),{'state':'not_found'})
        self.assertEqual(self.service.list(),[])


class RecoveryHttpTests(http_tests.ExcelHttpTests):
    # Parent tests remain in their original class; only collect this class's new scenarios.
    test_preview_confirm_list_detail_and_retry=None
    test_origin_guard_and_invalid_base64=None
    test_malformed_worksheet_returns_json_error=None

    def test_lost_preview_and_confirm_responses_can_be_queried(self):
        from ops.server import Handler
        body=self.body();body['upload_id']=str(uuid4())
        original_send=Handler.send
        def drop_response(handler,value,*args,**kwargs):
            if handler.command=='POST' and handler.path in ('/api/kol-import/preview','/api/kol-import/confirm') and 'id' in value:
                handler.close_connection=True
                handler.connection.shutdown(socket.SHUT_RDWR)
                return
            return original_send(handler,value,*args,**kwargs)
        with patch.object(Handler,'send',drop_response):
            from http.client import RemoteDisconnected
            with self.assertRaises(RemoteDisconnected):self.request('/api/kol-import/preview',body)
            status,receipt=self.request('/api/kol-import/uploads/'+body['upload_id'])
            self.assertEqual(status,200);self.assertEqual(receipt['state'],'saved')
            with self.assertRaises(RemoteDisconnected):self.request('/api/kol-import/confirm',{'batch_id':receipt['batch_id']})
        status,saved=self.request('/api/kol-import/uploads/'+body['upload_id'])
        self.assertEqual(saved['batch_state'],'confirmed')
        self.assertEqual(self.request('/api/kol-import/confirm',{'batch_id':receipt['batch_id']})[0],200)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM kol_analysis_orders').fetchone()[0],1)

    def test_incomplete_upload_is_not_reported_as_saved(self):
        import json
        body=self.body();body['upload_id']=str(uuid4());raw=json.dumps(body).encode()
        conn=HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        conn.putrequest('POST','/api/kol-import/preview')
        conn.putheader('Content-Type','application/json');conn.putheader('X-Requested-With','operations-ui')
        conn.putheader('Content-Length',len(raw));conn.endheaders()
        conn.send(raw[:len(raw)//2]);conn.sock.shutdown(socket.SHUT_WR)
        response=conn.getresponse();self.assertEqual(response.status,400);response.read();conn.close()
        self.assertEqual(self.request('/api/kol-import/uploads/'+body['upload_id'])[1],{'state':'not_found'})

    def test_status_query_validation_and_origin_guard(self):
        uid=str(uuid4())
        self.assertEqual(self.request('/api/kol-import/uploads/'+uid+'?path=bad')[0],400)
        self.assertEqual(self.request('/api/kol-import/uploads/'+uid,headers={'Origin':'https://other.test'})[0],403)
