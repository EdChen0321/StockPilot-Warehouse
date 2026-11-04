import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from ops.store import Store, ConflictError
from ops.catalogue import Catalogue
from ops.orders import Orders
from ops.stock import Stock
from ops.kol import Kol, report
from test_orders import row, as_csv


def command(**values):
    return dict(operation_id=uuid4().hex, **values)


class SourceFlowFixTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / 'audit.db')
        self.cat = Catalogue(self.store)
        self.stock = Stock(self.store)
        self.account = self.cat.account(command(name='測試來源', platform='SHOPLINE'))
        self.product = self.cat.create(command(name='測試商品', sku='P-BOX', barcode='BOX'))

    def upload(self, account=None, **changes):
        b = self.store.preview(account or self.account['id'], 'test.csv', as_csv([row(**changes)]), True)
        return self.store.confirm(b['id'])

    def receive_stock(self):
        r = self.stock.receipt(command(number='IN', lines=[dict(product_id=self.product['id'], quantity=3, unit_price='1')]))
        self.stock.confirm_receipt(r['id'], command())

    def test_entry_number_rejects_cancelled_source_without_creating_shipment(self):
        self.upload(raw_status='cancelled')
        with self.assertRaises(ValueError):
            self.stock.shipment(command(entry_number='O-001'))
        self.assertEqual(self.stock.snapshot()['shipments'], [])

    def test_entry_number_links_source_and_cancellation_releases_stock(self):
        self.upload()
        self.receive_stock()
        s = self.stock.shipment(command(entry_number='O-001'))
        group = next(g for g in self.stock.snapshot()['groups'] if g['id']==s['group_id'])
        self.assertEqual(group['account_id'], self.account['id'])
        self.assertFalse(group['manual'])
        self.stock.scan(s['id'], command(barcode='BOX'))
        self.stock.scan(s['id'], command(barcode='BOX'))
        with self.assertRaises(ConflictError):
            self.stock.scan(s['id'], command(barcode='BOX'))
        Orders(self.store).cancel(command(account_id=self.account['id'], order_id='O-001', expected_revision=1))
        self.assertEqual(self.stock.snapshot()['products'][0]['available'], 3)
        with self.assertRaises(ConflictError):
            self.stock.transition(s['id'], 'complete', command())

    def test_same_number_across_sources_requires_explicit_source(self):
        self.upload()
        other = self.cat.account(command(name='另一來源', platform='manual'))
        self.upload(account=other['id'])
        with self.assertRaises(ConflictError):
            self.stock.shipment(command(entry_number='O-001'))

    def test_entry_does_not_open_unrelated_shipment_with_same_barcode(self):
        self.upload(order_id='OTHER')
        other=self.stock.shipment(command(account_id=self.account['id'],order_id='OTHER',number='O-001'))
        self.upload()
        with self.assertRaises(ConflictError):
            self.stock.shipment(command(entry_number='O-001'))
        self.assertEqual(len(self.stock.snapshot()['shipments']),1)

    def test_manual_shipment_created_before_source_cannot_bypass_later_cancel(self):
        self.receive_stock()
        s = self.stock.shipment(command(entry_number='O-001'))
        self.stock.scan(s['id'], command(barcode='BOX'))
        self.upload(raw_status='cancelled')
        with self.assertRaises(ConflictError):
            self.stock.transition(s['id'], 'complete', command())
        with self.assertRaises(ConflictError):
            self.stock.scan(s['id'], command(barcode='BOX'))
        self.stock.transition(s['id'], 'cancel', command())
        self.assertEqual(self.stock.snapshot()['products'][0]['available'], 3)

    def test_manual_order_preserves_total_and_utm_for_kol(self):
        Kol(self.store).save(command(name='測試KOL',utms='sample',started_at='2020-01-01T00:00:00+08:00'))
        Orders(self.store).receive(command(account_id=self.account['id'], order_id='MANUAL',
            ordered_at='2026-01-02T09:00:00+08:00', utm_medium='sample', order_total='960',
            lines=[dict(product_id=self.product['id'], quantity=1, unit_price='1000')]))
        result = report(self.store)
        self.assertEqual(result['orders'][0]['matched_utm'], 'sample')
        self.assertEqual(result['summary']['total'], '960.00')
        self.assertEqual(result['products'][0]['amount'], '1000.00')

    def test_manual_invalid_extensions_rejected_atomically(self):
        for fields in ({'order_total':'-1'}, {'order_total':None}, {'utm_medium':[]}, {'utm_medium':'x'*2001}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                Orders(self.store).receive(command(account_id=self.account['id'], order_id='BAD',
                    ordered_at='2026-01-02T09:00:00+08:00',
                    lines=[dict(product_id=self.product['id'], quantity=1, unit_price='1')], **fields))
            self.assertEqual(self.store.current_orders(), [])
