"""Operational Excel import must create orders/products, never inventory."""
import base64
import sqlite3
import tempfile
import unittest
from pathlib import Path
from ops.store import Store, ConflictError
from ops.catalogue import Catalogue
from ops.sales import sales
from test_kol_excel_cleaning import line, workbook_bytes, HEADERS


class OperationalExcelTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'work.sqlite3'
        self.store=Store(self.path)
        self.account=Catalogue(self.store).account(dict(operation_id='source',name='水壺店',platform='SHOPLINE'))['id']
        self.default_blob=self.blob()

    def blob(self, rows=None):
        rows=rows or [line(**{'貨幣':'TWD','商品類型':'水壺','商品貨號':'WB-001','商品名稱':'水壺','商品結帳價':200,'數量':2,'訂單合計':400})]
        return base64.b64encode(workbook_bytes(rows,headers=HEADERS+['貨幣','商品類型'])).decode()

    def preview(self, rows=None, filename='orders.xlsx'):
        return self.store.preview_excel(self.account,filename,self.default_blob if rows is None else self.blob(rows),True)

    def count(self,table):
        with self.store.connect() as db:return db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]

    def test_water_bottle_preview_confirm_sales_and_restart(self):
        p=Path(__file__).resolve().parents[1]/'outputs/water-bottle-100-20261030/水壺商店_100筆訂單_修正版.xlsx'
        b=self.store.preview_excel(self.account,p.name,base64.b64encode(p.read_bytes()).decode(),True)
        self.assertTrue(b['can_confirm'])
        self.assertEqual(len(b['new_products']),24)
        self.assertEqual(self.count('products'),0)
        self.assertEqual(self.count('current_orders'),0)
        self.assertEqual(len(b['raw_rows']),199)
        done=self.store.confirm(b['id'])
        self.assertEqual(len([o for o in done['results'] if o['outcome']=='applied']),100)
        self.assertEqual(self.count('products'),24)
        self.assertEqual(self.count('current_orders'),100)
        self.assertEqual(self.count('stock_units'),0)
        self.assertEqual(self.count('kol_analysis_orders'),0)
        self.assertEqual((sales(self.store)['order_count'],sales(self.store)['sales_quantity'],sales(self.store)['amount']),(89,332,'194080.00'))
        self.assertEqual(Store(self.path).confirm(b['id'])['state'],'confirmed')
        self.assertEqual(self.count('order_versions'),100)

    def test_existing_master_preserved_and_exact_sku(self):
        cat=Catalogue(self.store)
        p=cat.create(dict(operation_id='p',sku='WB-001',name='正式水壺名稱'))
        b=self.preview()
        self.assertFalse(b['new_products'])
        self.store.confirm(b['id'])
        self.assertEqual(cat.detail(p['id'])['name'],'正式水壺名稱')
        self.assertEqual(self.count('products'),1)

    def test_single_shop_empty_database_retry_and_confirm(self):
        store = Store(self.path.with_name('empty.sqlite3'))
        first = store.preview_excel(None, 'orders.xlsx', self.default_blob, True)
        again = store.preview_excel('', 'orders.xlsx', self.default_blob, True)
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(len(store.bootstrap()['accounts']), 1)
        self.assertEqual(store.bootstrap()['products'], [])
        store.confirm(first['id'])
        self.assertEqual(len(store.bootstrap()['products']), 1)
        self.assertEqual(self.store.preview_excel(None, 'orders.xlsx', self.default_blob, True)['account_id'], self.account)

    def test_single_shop_invalid_file_and_failed_draft_leave_no_source(self):
        store = Store(self.path.with_name('empty.sqlite3'))
        with self.assertRaises(ValueError):
            store.preview_excel(None, 'orders.xlsx', 'invalid', True)
        self.assertEqual(store.bootstrap()['accounts'], [])
        with store.connect() as db:
            db.execute("CREATE TRIGGER fail_batch BEFORE INSERT ON batches BEGIN SELECT RAISE(ABORT, 'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            store.preview_excel(None, 'orders.xlsx', self.default_blob, True)
        self.assertEqual(store.bootstrap()['accounts'], [])

    def test_single_shop_never_guesses_legacy_sources(self):
        Catalogue(self.store).account(dict(operation_id='other', name='other', platform='manual'))
        with self.assertRaises(ValueError):
            self.store.preview_excel(None, 'orders.xlsx', self.default_blob, True)
        with self.assertRaises(ValueError):
            self.store.preview_excel('missing', 'orders.xlsx', self.default_blob, True)

    def test_csv_uses_same_single_shop(self):
        from test_orders import row, as_csv
        csv = as_csv([row()])
        store = Store(self.path.with_name('empty.sqlite3'))
        first = store.preview(None, 'orders.csv', csv, True)
        again = store.preview('', 'orders.csv', csv, True)
        self.assertEqual(first['id'], again['id'])
        excel = store.preview_excel(None, 'orders.xlsx', self.default_blob, True)
        self.assertEqual(first['account_id'], excel['account_id'])
        self.assertEqual(len(store.bootstrap()['accounts']), 1)

    def test_retry_update_stale_preview_and_old_file_do_not_revive_cancel(self):
        first=self.preview();self.store.confirm(first['id'])
        self.assertEqual(self.preview()['id'],first['id'])
        row=line(**{'貨幣':'TWD','商品貨號':'WB-001','商品名稱':'水壺','訂單合計':600,'數量':3,'商品結帳價':200})
        update=self.preview([row],'update.xlsx')
        stale=self.preview([dict(row,數量=4,訂單合計=800)],'later.xlsx')
        self.store.confirm(update['id'])
        with self.assertRaises(ConflictError):self.store.confirm(stale['id'])
        cancel=self.preview([dict(row,訂單狀態='已取消')],'cancel.xlsx')
        self.store.confirm(cancel['id'])
        self.store.confirm(first['id'])
        self.assertEqual(sales(self.store)['order_count'],0)
        self.assertEqual(self.count('order_versions'),3)
        refreshed=self.store.reprocess(stale['id'])
        self.assertTrue(refreshed['can_confirm'])

    def test_invalid_input_creates_no_master_and_blocks_confirmation(self):
        for changes in ({'商品貨號':''},{'商品名稱':''},{'貨幣':'USD'},{'訂單狀態':'不明狀態'},{'數量':0}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.preview([line(**dict({'貨幣':'TWD'},**changes))])
        self.assertEqual(self.count('products'),0)

    def test_product_changed_after_preview_requires_new_preview(self):
        cat=Catalogue(self.store)
        p=cat.create(dict(operation_id='p',sku='WB-001',name='水壺'))
        b=self.preview()
        cat.update(p['id'],dict(operation_id='disable',expected_version=1,active=False))
        with self.assertRaises(ConflictError):self.store.confirm(b['id'])
        self.assertEqual(self.count('current_orders'),0)

    def test_atomic_product_and_order_rollback(self):
        b=self.preview()
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_order BEFORE INSERT ON order_versions BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.DatabaseError):self.store.confirm(b['id'])
        self.assertEqual(self.count('products'),0)
        self.assertEqual(self.count('product_versions'),0)
        self.assertEqual(self.store.batch(b['id'])['state'],'draft')

    def test_reordered_interleaved_rows_keep_source_indices_and_order_totals(self):
        first=line(**{'貨幣':'TWD','訂單號碼':'001','商品貨號':'0001','商品名稱':'甲','數量':1,'商品結帳價':200,'訂單合計':600})
        second=line(**{'貨幣':'TWD','訂單號碼':'002','商品貨號':'0002','商品名稱':'乙','數量':1,'商品結帳價':100,'訂單合計':100})
        third=dict(first,數量=2,訂單合計=None,**{'UTM 媒介':None})
        b=self.preview([first,second,third])
        self.assertEqual(b['results'][0]['row_indices'],[2,4])
        self.assertEqual([l['row_index'] for l in b['results'][0]['lines']],[2,4])
        self.store.confirm(b['id'])
        self.assertEqual(sales(self.store)['amount'],'700.00')
        self.assertEqual({p['sku'] for p in Catalogue(self.store).list()['products']},{'0001','0002'})

    def test_conflicting_product_in_same_file_rejected_and_two_previews_cannot_create_twice(self):
        r=line(**{'貨幣':'TWD'})
        with self.assertRaises(ValueError):self.preview([r,dict(r,商品名稱='衝突名稱')])
        first=self.preview()
        second=self.preview([line(**{'貨幣':'TWD','訂單號碼':'OTHER','商品貨號':'WB-001','商品名稱':'水壺'})],'other.xlsx')
        self.store.confirm(first['id'])
        with self.assertRaises(ConflictError):self.store.confirm(second['id'])
        retry=self.store.reprocess(second['id']);self.store.confirm(retry['id'])
        self.assertEqual(self.count('products'),1)

    def test_same_content_in_another_workbook_does_not_add_order_version(self):
        b=self.preview();self.store.confirm(b['id'])
        row=line(**{'貨幣':'TWD','商品類型':'水壺','商品貨號':'WB-001','商品名稱':'水壺','商品結帳價':200,'數量':2,'訂單合計':400})
        # An unrelated extra column changes bytes without changing the order facts.
        encoded=base64.b64encode(workbook_bytes([dict(row,備註='另一個匯出檔')],headers=HEADERS+['貨幣','商品類型','備註'])).decode()
        duplicate=self.store.preview_excel(self.account,'another.xlsx',encoded,True)
        self.assertEqual(duplicate['results'][0]['outcome'],'duplicate')
        self.store.confirm(duplicate['id'])
        self.assertEqual(self.count('order_versions'),1)

    def test_imported_order_can_receive_stock_putaway_and_ship(self):
        from ops.stock import Stock
        from ops.locations import Locations
        b=self.preview();self.store.confirm(b['id'])
        o=self.store.current_orders()[0];pid=o['lines'][0]['product_id']
        stock=Stock(self.store);places=Locations(self.store)
        places.enable(dict(operation_id='enable'))
        place=places.save(dict(operation_id='place',address='1F',position='A',name='水壺櫃',can_import=True,can_export=True,can_return=True,entry_state='import'))
        receipt=stock.receipt(dict(operation_id='receipt',number='IN-1',lines=[dict(product_id=pid,quantity=2,unit_price='100')]))
        stock.confirm_receipt(receipt['id'],dict(operation_id='received'))
        shipment=stock.shipment(dict(operation_id='shipment',number='OUT-1',account_id=self.account,order_id=o['order_id']))
        with self.assertRaises(ConflictError):stock.transition(shipment['id'],'allocate',dict(operation_id='unassigned'))
        places.assign(dict(operation_id='putaway',receipt_id=receipt['id'],quantity=2,place_id=place['id']))
        for state in ('allocate','pick','ship'):
            stock.transition(shipment['id'],state,dict(operation_id=state))
        self.assertEqual(stock.snapshot()['shipments'][0]['state'],'shipped')
        self.assertEqual(sales(self.store)['amount'],'400.00')

    def test_http_binary_upload_preview_confirm_and_invalid_payload(self):
        import json
        import threading
        from http.client import HTTPConnection
        from ops.server import create_server
        server=create_server(self.path,0)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def request(path,body):
            conn=HTTPConnection('127.0.0.1',server.server_port,timeout=10)
            try:
                conn.request('POST',path,json.dumps(body),{'Content-Type':'application/json','X-Requested-With':'operations-ui'})
                response=conn.getresponse()
                return response.status,json.loads(response.read())
            finally:conn.close()
        try:
            body=dict(account_id=self.account,filename='orders.xlsx',content_base64=self.default_blob,complete_snapshot=True)
            status,b=request('/api/imports',body)
            self.assertEqual(status,200,b)
            self.assertEqual(len(b['new_products']),1)
            self.assertEqual(request('/api/imports/'+b['id']+'/confirm',{})[0],200)
            self.assertEqual(request('/api/imports',body)[1]['id'],b['id'])
            self.assertEqual(request('/api/imports',dict(body,content_base64='%%%'))[0],400)
            self.assertEqual(request('/api/imports',dict(body,csv_text='ambiguous'))[0],400)
        finally:
            server.shutdown();server.server_close();worker.join()

