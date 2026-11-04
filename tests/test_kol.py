import csv
import io
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from test_orders import row, as_csv
from ops.importing import parse_orders
from ops.store import Store, ConflictError
from ops.kol import Kol, report, export_csv


class KolTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / 'kol.db')
        self.store.seed()
        self.kol = Kol(self.store)

    def save(self, **changes):
        body = dict(operation_id=uuid4().hex, name='合成小晴', utms=' sunny, sunny ',
                    platforms='Instagram', started_at='2026-01-01T00:00:00+08:00', comment='')
        body.update(changes)
        return self.kol.save(body)

    def upload(self, account='demo-sl', **changes):
        data = row(utm_medium='sunny', order_total='960', **changes)
        batch = self.store.preview(account, 'kol.csv', as_csv([data]), True)
        self.store.confirm(batch['id'])
        return batch

    def test_import_header_and_old_fingerprint(self):
        original = as_csv([row()])
        parsed = parse_orders(original)[1][0]
        self.assertNotIn('utm_medium', parsed)
        self.assertNotIn('order_total_cents', parsed)
        data = parse_orders(as_csv([row(utm_medium='sunny', order_total='960')]))[1][0]
        self.assertEqual(data['order_total_cents'], 96000)
        self.assertEqual(data['amount_cents'], 20000)
        self.assertNotEqual(parsed['fingerprint'], data['fingerprint'])
        mismatch = [row(expected_line_count='2', order_item_amount='400', order_total='960'),
                    row(line_id='L2', expected_line_count='2', order_item_amount='400', order_total='961')]
        self.assertTrue(parse_orders(as_csv(mismatch))[1][0]['errors'])
        self.assertTrue(parse_orders(as_csv([row(order_total='-1')]))[1][0]['errors'])

    def test_master_duplicate_version_and_retry(self):
        body = dict(operation_id='same', name='合成小晴', utms='Sunny,sunny', platforms='IG',
                    started_at='2026-01-01T00:00:00+08:00')
        k = self.kol.save(body)
        self.assertEqual(k, self.kol.save(body))
        self.assertEqual(k['utms'], ['sunny'])
        with self.assertRaises(ConflictError):
            self.save()
        updated = self.save(id=k['id'], expected_version=1, name='小晴更新')
        self.assertEqual(updated['version'], 2)
        with self.assertRaises(ConflictError):
            self.save(id=k['id'], expected_version=1)
        self.kol.delete(dict(operation_id='delete', id=k['id'], expected_version=2))
        self.assertEqual(self.kol.list(), [])

    def test_total_attribution_and_read_only(self):
        k = self.save()
        self.upload()
        self.upload(account='demo-sp')
        self.upload(order_id='CANCEL', raw_status='cancelled')
        with self.store.connect() as db:
            before = list(db.iterdump())
        result = report(self.store)
        self.assertEqual(result['summary']['order_count'], 2)
        self.assertEqual(result['summary']['total'], '1920.00')
        self.assertEqual(result['summary']['average'], '960.00')
        detail = report(self.store, kol_id=k['id'])
        self.assertEqual(len(detail['orders']), 2)
        self.assertEqual([p['amount'] for p in detail['products']], ['200.00','200.00'])
        self.assertEqual([p['order_count'] for p in detail['products']], [1,1])
        self.assertEqual(detail['orders'][0]['matched_utm'], 'sunny')
        with self.store.connect() as db:
            self.assertEqual(before, list(db.iterdump()))

    def test_unattributed_missing_totals_boundaries_and_revision(self):
        self.save()
        for oid, utm, when in [('before','sunny','2025-12-31T23:59:59+08:00'),
                               ('case','Sunny','2026-01-02T09:00:00+08:00'),
                               ('empty','','2026-01-02T09:00:00+08:00')]:
            batch = self.store.preview('demo-sl','input.csv',as_csv([row(order_id=oid,utm_medium=utm,
                ordered_at=when,kol_ref='not-a-utm')]),True)
            self.store.confirm(batch['id'])
        result = report(self.store, kol_id='unattributed', start='2026-01-01',end='2026-01-02')
        self.assertEqual(result['summary']['order_count'], 1)
        self.assertIsNone(result['summary']['total'])
        self.assertIsNone(result['summary']['average'])
        self.assertEqual(result['summary']['missing_total_count'], 1)
        self.assertEqual(report(self.store,kol_id='general')['summary']['missing_total_count'],1)
        self.upload()
        self.upload(source_revision='2',raw_status='cancelled')
        self.assertEqual(report(self.store)['summary']['order_count'],3)
        with self.assertRaises(ValueError): report(self.store,account_id='missing')
        with self.assertRaises(ValueError): report(self.store,start='2026-02-02',end='2026-01-01')

    def test_csv_same_scope_and_formula_safety(self):
        k=self.save(name='=合成')
        self.upload()
        result=report(self.store,kol_id=k['id'],account_id='demo-sl')
        rows=list(csv.DictReader(io.StringIO(export_csv(result))))
        self.assertEqual(rows[0]['total'],'960.00')
        self.assertTrue(rows[0]['name'].startswith("'="))

    def test_multiline_bundle_boundaries_zero_and_recalculation(self):
        k=self.save(keep_case=True,utms='Sunny')
        rows=[row(order_id='TWO',line_id='1',expected_line_count='2',order_item_amount='400',
                  utm_medium='Sunny',order_total='0',ordered_at='2026-01-01T00:00:00+08:00'),
              row(order_id='TWO',line_id='2',expected_line_count='2',order_item_amount='400',
                  utm_medium='Sunny',order_total='0',ordered_at='2026-01-01T00:00:00+08:00',external_sku='SET-01')]
        b=self.store.preview('demo-sl','bundle.csv',as_csv(rows),True);self.store.confirm(b['id'])
        data=report(self.store,start='2026-01-01',end='2026-01-01',kol_id=k['id'])
        self.assertEqual(data['summary']['order_count'],1)
        self.assertEqual(data['summary']['total'],'0.00')
        self.assertEqual(len(data['products']),2)
        self.assertEqual(len(data['orders'][0]['lines']),2)
        self.assertEqual(report(self.store,start='2025-12-31',end='2025-12-31')['summary']['order_count'],0)
        self.save(id=k['id'],expected_version=1,utms='different')
        self.assertEqual(report(self.store,kol_id=k['id'])['summary']['order_count'],0)
        self.assertEqual(report(self.store,kol_id='unattributed')['summary']['order_count'],1)

    def test_legacy_fingerprint_and_reimport_without_new_columns(self):
        from ops.importing import FIELDS, digest
        rows=[row()]
        old_fields=[f for f in FIELDS if f not in ('utm_medium','order_total')]
        out=io.StringIO();writer=csv.DictWriter(out,fieldnames=old_fields)
        writer.writeheader();writer.writerows(rows)
        legacy=out.getvalue()
        normalized=parse_orders(legacy)[1][0]
        canonical={k:v for k,v in normalized.items() if k not in ('row_indices','warnings','errors','lines','fingerprint')}
        canonical['lines']=[{k:v for k,v in line.items() if k!='row_index'} for line in normalized['lines']]
        self.assertEqual(normalized['fingerprint'],digest(canonical))
        b=self.store.preview('demo-sl','old.csv',legacy,True);self.store.confirm(b['id'])
        b=self.store.preview('demo-sl','extended.csv',as_csv(rows),True);self.store.confirm(b['id'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM order_versions').fetchone()[0],1)

    def test_invalid_master_id_and_version_are_validation_errors(self):
        with self.assertRaises(ValueError):
            self.save(id=['invalid'])
        k=self.save()
        with self.assertRaises(ValueError):
            self.save(id=k['id'],expected_version=True)

    def test_master_metadata_migration_and_edit_preserves_creation(self):
        k=self.save(campaign_name='夏季活動',comment='第一行\n第二行')
        self.assertEqual(k['campaign_name'],'夏季活動')
        self.assertTrue(k['created_at'])
        updated=self.save(id=k['id'],expected_version=1,campaign_name='秋季活動')
        self.assertEqual(updated['created_at'],k['created_at'])
        self.assertGreaterEqual(updated['updated_at'],k['updated_at'])
        import sqlite3
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.db'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE kols(id TEXT PRIMARY KEY,name TEXT,platforms TEXT,started_at TEXT,utms TEXT,keep_case INTEGER,comment TEXT,version INTEGER)')
                db.execute("INSERT INTO kols VALUES('old','舊資料','[]','2026-01-01T00:00:00+00:00','[\"old\"]',0,'',1)")
            db.close()
            old=Kol(Store(path)).list()[0]
            self.assertEqual(old['created_at'],'')
            self.assertEqual(old['campaign_name'],'')

    def test_filtered_summary_matches_orders_and_preserves_active_performance(self):
        self.save(utms='sunny,other')
        rows = [row(order_id='A', utm_medium='sunny', order_total='500'),
                row(order_id='B', utm_medium='other', order_total='300'),
                row(order_id='C', utm_medium='sunny', order_total='200', raw_status='cancelled'),
                row(order_id='D', utm_medium='sunny', order_total='', external_sku='SET-01')]
        batch = self.store.preview('demo-sl', 'scope.csv', as_csv(rows), True)
        self.store.confirm(batch['id'])
        for filters, ids, total, missing in [
            ({'utm':'other'}, {'B'}, '300.00', 0),
            ({'status':'cancelled'}, {'C'}, '200.00', 0),
            ({'status':'all','utm':'sunny','product_id':'P-BOX'}, {'A','C'}, '700.00', 0),
            ({'utm':'missing'}, set(), '0.00', 0),
            ({'utm':'sunny'}, {'A','D'}, None, 1),
        ]:
            with self.subTest(filters=filters):
                data = report(self.store, **filters)
                self.assertEqual({o['order_id'] for o in data['orders']}, ids)
                self.assertEqual(data['filtered_summary']['order_count'], len(ids))
                self.assertEqual(data['filtered_summary']['total'], total)
                self.assertEqual(data['filtered_summary']['missing_total_count'], missing)
                self.assertEqual(data['summary']['order_count'], 3)

    def test_page_filters_do_not_change_active_statistics(self):
        k=self.save(utms='sunny,other')
        self.upload()
        self.upload(account='demo-sp')
        self.upload(order_id='CANCEL',raw_status='cancelled')
        data=report(self.store,kol_id=k['id'],status='cancelled',utm='sunny')
        self.assertEqual(data['summary']['total'],'1920.00')
        self.assertEqual(len(data['orders']),1)
        self.assertEqual(data['orders'][0]['status'],'cancelled')
        self.assertEqual(data['status_counts'],{'active':2,'cancelled':1,'excluded':0,'all':3})
        self.assertEqual(len(data['products']),2,'same standard product must retain source groups')
        data=report(self.store,kol_id=k['id'],status='all',product_id='P-BOX',account_id='demo-sl')
        self.assertEqual([o['product_quantity'] for o in data['orders']],[2,2])
        self.assertEqual(report(self.store,platform='YouTube')['summary']['order_count'],0)
        self.assertEqual(report(self.store,platform='Instagram')['summary']['order_count'],2)
        for kind in ('list','products','orders'):
            rows=list(csv.DictReader(io.StringIO(export_csv(data,kind))))
            self.assertTrue(rows)
            self.assertIn('filters',rows[0])
        with self.assertRaises(ValueError):report(self.store,status='completed')
        with self.assertRaises(ValueError):report(self.store,product_id='missing')


if __name__ == '__main__': unittest.main()
