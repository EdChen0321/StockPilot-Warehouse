"""An order workbook is sufficient; legacy catalogues remain opt-in."""
import base64
import tempfile
import unittest
from pathlib import Path
from ops.store import Store
from ops.catalogue import Catalogue
from ops.kol_import import KolImport
from ops.kol import report
from test_kol_excel_cleaning import line, workbook_bytes, HEADERS


class SingleFileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/'test.sqlite3'
        self.store = Store(self.path)
        self.account = Catalogue(self.store).account(dict(operation_id='account', name='水壺店', platform='SHOPLINE'))['id']
        self.service = KolImport(self.store)

    def preview(self, rows):
        return self.service.preview(dict(account_id=self.account,filename='orders.xlsx',
            content_base64=base64.b64encode(workbook_bytes(rows, headers=HEADERS+['商品類型'])).decode()))

    def test_one_file_confirm_retry_and_restart_without_catalogue(self):
        batch = self.preview([line(**{'商品貨號':'BOTTLE-500-BL','商品名稱':'海藍水壺','商品類型':'水壺'})])
        self.assertTrue(batch['can_confirm'])
        self.assertEqual(batch['orders'][0]['lines'][0]['name'], '海藍水壺')
        self.assertEqual(batch['rules_version'], 'order-file-v1')
        restarted = KolImport(Store(self.path))
        self.assertEqual(restarted.confirm({'batch_id':batch['id']})['state'], 'confirmed')
        restarted.confirm({'batch_id':batch['id']})
        self.assertEqual(report(self.store)['summary']['order_count'], 1)
        with self.store.connect() as db:
            for table in ('products','current_orders','stock_units'):
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0],0)

    def test_exact_sku_no_legacy_rewrite_or_bundle_expansion(self):
        for sku in ('HO-01001', '#AA-X01001*3+BB-02001', 'BOTTLE500BL'):
            with self.subTest(sku=sku):
                b = self.preview([line(**{'商品貨號':sku,'顧客':'補刷小姐'})])
                self.assertTrue(b['can_confirm'])
                self.assertEqual([(l['sku'],l['quantity']) for l in b['orders'][0]['lines']],[(sku,2)])
                self.assertFalse(b['orders'][0]['internal'])

    def test_missing_sku_or_name_is_blocking_not_silently_excluded(self):
        for field in ('商品貨號','商品名稱'):
            b = self.preview([line(**{field:''})])
            self.assertFalse(b['can_confirm'])
            self.assertEqual(b['errors'][0]['row_index'],2)
            with self.assertRaises(ValueError): self.service.confirm({'batch_id':b['id']})

    def test_conflicting_name_or_category_for_same_sku_blocks(self):
        for change in ({'商品名稱':'另一個名字'}, {'商品類型':'另一類'}):
            b = self.preview([line(**{'商品類型':'水壺'}),line(**dict({'訂單號碼':'ORDER-2','商品類型':'水壺'},**change))])
            self.assertFalse(b['can_confirm'])
            self.assertIn('SKU', b['errors'][0]['message'])

    def test_cancelled_missing_sku_is_allowed_and_category_optional(self):
        b = self.preview([line(),line(**{'訂單號碼':'CANCEL','訂單狀態':'已取消','商品貨號':''})])
        self.assertTrue(b['can_confirm'])
        self.assertEqual(b['summary']['order_count'],1)
        self.assertEqual(b['orders'][0]['lines'][0]['category'],'未分類')

    def test_real_water_bottle_fixture_without_second_workbook(self):
        path = Path(__file__).resolve().parents[1]/'outputs/water-bottle-100-20261030/水壺商店_100筆訂單_修正版.xlsx'
        b = self.service.preview(dict(account_id=self.account,filename=path.name,content_base64=base64.b64encode(path.read_bytes()).decode()))
        self.assertTrue(b['can_confirm'],b['errors'])
        self.assertEqual(b['summary']['order_count'],89)
        self.assertEqual(b['summary']['product_count'],24)
        self.assertEqual(b['summary']['order_total'],'194080.00')
        self.service.confirm({'batch_id':b['id']})
        self.assertEqual(report(self.store)['summary']['order_count'],89)

    def test_http_upload_confirm_and_retry_with_no_catalogue(self):
        import json
        import threading
        from http.client import HTTPConnection
        from ops.server import create_server
        server = create_server(self.path,0)
        worker = threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        def request(path,body):
            connection = HTTPConnection('127.0.0.1',server.server_port,timeout=10)
            try:
                connection.request('POST',path,json.dumps(body),{'Content-Type':'application/json','X-Requested-With':'operations-ui'})
                response = connection.getresponse()
                result = json.loads(response.read())
                self.assertEqual(response.status,200,result)
                return result
            finally:
                connection.close()
        try:
            body=dict(account_id=self.account,filename='orders.xlsx',content_base64=base64.b64encode(workbook_bytes([line()])).decode())
            b=request('/api/kol-import/preview',body)
            self.assertTrue(b['can_confirm'])
            self.assertEqual(request('/api/kol-import/confirm',{'batch_id':b['id']})['state'],'confirmed')
            self.assertEqual(request('/api/kol-import/preview',body)['id'],b['id'])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

