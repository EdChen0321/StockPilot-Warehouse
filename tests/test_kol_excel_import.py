"""Confirmation tests use disposable SQLite and synthetic catalogues only."""
import base64
import tempfile
import unittest
from pathlib import Path
from openpyxl import Workbook
from ops.store import Store, ConflictError
from ops.kol_import import KolImport
from test_kol_excel_cleaning import line, workbook_bytes

class ImportFixture(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder=Path(temp.name); self.dbpath=self.folder/'test.db'
        self.store=Store(self.dbpath); self.store.seed()
        self.catalog_path=self.folder/'catalog.xlsx'
        self.write_catalog()
        self.service=KolImport(self.store,self.catalog_path)
        self.default_blob=workbook_bytes([line()])

    def write_catalog(self, name='標準甲'):
        wb=Workbook(); ws=wb.active
        ws.append(['產品名稱','庫存','產品代號','產品類型','產品條碼'])
        ws.append([name,0,'AA-01001','合成類','SYN-BARCODE'])
        wb.save(self.catalog_path); wb.close()

    def preview(self, rows=None, filename='synthetic.xlsx'):
        return self.service.preview(dict(account_id='demo-sl',filename=filename,
            content_base64=base64.b64encode(self.default_blob if rows is None else workbook_bytes(rows)).decode('ascii')))

    def business_snapshot(self):
        with self.store.connect() as db:
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    if r[0] in ('products','order_versions','current_orders','receipts','fulfillment_groups',
                                'shipments','allocations','shipment_work','manual_dispatch_groups','scanned_shipments',
                                'order_work','order_additions') or r[0].startswith('stock')]
            return {t:[tuple(r) for r in db.execute('SELECT * FROM "'+t+'" ORDER BY rowid')] for t in tables}

class ConfirmationTests(ImportFixture):
    def test_preview_and_confirmation_never_change_inventory_or_operational_orders(self):
        before=self.business_snapshot(); b=self.preview()
        self.assertTrue(b['can_confirm']); self.assertEqual(b['state'],'preview')
        self.assertEqual(before,self.business_snapshot())
        final=self.service.confirm({'batch_id':b['id']})
        self.assertEqual(final['state'],'confirmed')
        self.assertEqual(before,self.business_snapshot())

    def test_same_file_retry_confirmation_retry_and_restart(self):
        b=self.preview(); self.assertEqual(self.preview()['id'],b['id'])
        final=self.service.confirm({'batch_id':b['id']})
        self.assertEqual(self.service.confirm({'batch_id':b['id']})['id'],final['id'])
        restarted=KolImport(Store(self.dbpath),self.catalog_path)
        self.assertEqual(restarted.detail(b['id'])['state'],'confirmed')
        self.assertEqual(restarted.detail(b['id'])['summary']['order_count'],1)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM kol_analysis_orders').fetchone()[0],1)

    def test_catalog_change_requires_new_preview(self):
        b=self.preview(); self.write_catalog('變更後名稱')
        with self.assertRaises(ConflictError): self.service.confirm({'batch_id':b['id']})
        self.assertEqual(self.service.detail(b['id'])['state'],'preview')
        self.assertNotEqual(self.preview()['id'],b['id'])

    def test_invalid_preview_cannot_confirm(self):
        b=self.preview([line(**{'訂單合計':None})])
        self.assertFalse(b['can_confirm']); self.assertTrue(b['errors'])
        with self.assertRaises(ValueError): self.service.confirm({'batch_id':b['id']})

    def test_transaction_failure_rolls_back_order_product_and_batch_state(self):
        import sqlite3
        b=self.preview()
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_import BEFORE INSERT ON kol_analysis_orders BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        with self.assertRaises((sqlite3.DatabaseError,ValueError)):
            self.service.confirm({'batch_id':b['id']})
        self.assertEqual(self.service.detail(b['id'])['state'],'preview')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM kol_analysis_orders').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM kol_analysis_products').fetchone()[0],0)

    def test_missing_catalog_and_unknown_account_are_actionable_errors(self):
        with self.assertRaises((ValueError,FileNotFoundError)):
            KolImport(self.store,self.folder/'absent.xlsx').preview(dict(account_id='demo-sl',filename='synthetic.xlsx',content_base64=base64.b64encode(workbook_bytes([line()])).decode('ascii')))
        with self.assertRaises(ValueError):
            self.service.preview(dict(account_id='missing',filename='synthetic.xlsx',content_base64=base64.b64encode(workbook_bytes([line()])).decode('ascii')))


    def test_complete_snapshot_replaces_amount_and_lines_preserving_old_batch(self):
        from ops.kol import report
        first=self.preview(); self.service.confirm({'batch_id':first['id']})
        update=self.preview([line(**{'訂單合計':1200,'數量':5})],filename='update.xlsx')
        self.assertTrue(update['can_confirm'])
        self.service.confirm({'batch_id':update['id']})
        current=report(self.store)
        self.assertEqual(current['summary']['order_count'],1)
        self.assertEqual(current['summary']['total'],'1200.00')
        self.assertEqual(self.service.detail(first['id'])['orders'][0]['lines'][0]['quantity'],2)
        self.assertEqual(self.service.detail(update['id'])['orders'][0]['lines'][0]['quantity'],5)
        self.assertTrue(all(l.get('amount') is None for o in current['orders'] for l in o['lines']))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM kol_analysis_orders').fetchone()[0],1)

    def test_cancelled_or_excluded_snapshot_masks_previous_active_and_old_retry_does_not_revive(self):
        from ops.kol import report
        for status, changes in [('cancelled',{'訂單狀態':'已取消'}),('excluded',{'商品貨號':None})]:
            with self.subTest(status=status):
                oid='TOMBSTONE-'+status
                original_body=dict(account_id='demo-sl',filename=oid+'.xlsx',content_base64=base64.b64encode(workbook_bytes([line(**{'訂單號碼':oid})])).decode('ascii'))
                original=self.service.preview(original_body)
                self.service.confirm({'batch_id':original['id']})
                replacement=self.preview([line(**dict({'訂單號碼':oid},**changes))],filename=oid+'-update.xlsx')
                self.service.confirm({'batch_id':replacement['id']})
                self.assertEqual(report(self.store)['summary']['order_count'],0)
                self.service.confirm({'batch_id':original['id']})
                replay=self.service.preview(original_body)
                self.service.confirm({'batch_id':replay['id']})
                self.assertEqual(report(self.store)['summary']['order_count'],0)
                self.assertEqual(self.service.detail(original['id'])['orders'][0]['status'],'active')
                self.assertEqual(self.service.detail(replacement['id'])['orders'][0]['status'],status)

    def test_two_previews_cannot_overwrite_changed_baseline(self):
        from ops.kol import report
        original=self.preview();self.service.confirm({'batch_id':original['id']})
        a=self.preview([line(**{'訂單合計':1200})],filename='a.xlsx')
        b_body=dict(account_id='demo-sl',filename='b.xlsx',content_base64=base64.b64encode(workbook_bytes([line(**{'訂單合計':1500})])).decode('ascii'))
        b=self.service.preview(b_body)
        self.service.confirm({'batch_id':a['id']})
        with self.assertRaises(ConflictError): self.service.confirm({'batch_id':b['id']})
        self.assertEqual(self.service.detail(b['id'])['state'],'preview')
        self.assertEqual(report(self.store)['summary']['total'],'1200.00')


        self.assertFalse(self.service.detail(b['id'])['can_confirm'])
        refreshed=self.service.preview(b_body)
        self.assertEqual(refreshed['id'],b['id'])
        self.assertTrue(refreshed['can_confirm'])
        self.service.confirm({'batch_id':refreshed['id']})
        self.assertEqual(report(self.store)['summary']['total'],'1500.00')

