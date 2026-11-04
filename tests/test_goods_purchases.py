import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from ops.store import Store, ConflictError
from ops.catalogue import Catalogue
from ops.materials import Materials
from ops.stock import Stock
from ops.goods_purchases import GoodsPurchases


class GoodsPurchaseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / 'test.sqlite3')
        self.service = GoodsPurchases(self.store)
        self.supplier = Materials(self.store).supplier(dict(operation_id='supplier', name='測試工廠'))
        self.product = Catalogue(self.store).create(dict(operation_id='product', name='成品', sku='GOODS'))

    def create(self, quantity=100):
        return self.service.save(dict(operation_id=uuid4().hex, number=uuid4().hex,
            supplier_id=self.supplier['id'], expected_date='2026-11-30',
            lines=[dict(product_id=self.product['id'], quantity=quantity)]))

    def action(self, p, action, **body):
        return self.service.action(p['id'], action, dict(operation_id=uuid4().hex, expected_version=p['version'], **body))

    def receive(self, p, quantity, **extra):
        return self.action(p, 'receive', number=uuid4().hex, freight='1.03', allocation='quantity',
            lines=[dict(product_id=self.product['id'], quantity=quantity, unit_price='2.50')], **extra)

    def test_partial_receipts_and_cost_and_persistent_links(self):
        p = self.create()
        self.assertIsNone(p['lines'][0]['quoted_price_cents'])
        p = self.action(p, 'confirm')
        self.assertEqual(len(Stock(self.store).snapshot()['units']), 0)
        p = self.receive(p, 40)
        self.assertEqual((p['state'], p['lines'][0]['received'], p['lines'][0]['pending']), ('partial', 40, 60))
        units = Stock(self.store).snapshot()['units']
        self.assertEqual(sum(u['cost_cents'] for u in units), 10103)
        p = self.receive(p, 60)
        self.assertEqual(p['state'], 'received')
        self.assertEqual(p['lines'][0]['pending'], 0)
        self.assertEqual(len(p['receipts']), 2)
        self.assertEqual(len(Stock(self.store).snapshot()['units']), 100)
        self.assertEqual(GoodsPurchases(Store(self.store.path)).snapshot()['purchases'][0]['receipts'], p['receipts'])
        with self.assertRaises(ConflictError):
            self.action(p, 'cancel', reason='收齊不能取消')

    def test_cancel_remaining_preserves_received_stock(self):
        p = self.receive(self.action(self.create(), 'confirm'), 40)
        before = Stock(self.store).snapshot()['units']
        p = self.action(p, 'cancel', reason='工廠不再供貨')
        self.assertEqual((p['state'], p['lines'][0]['cancelled'], p['lines'][0]['pending']), ('cancelled', 60, 0))
        self.assertEqual(before, Stock(self.store).snapshot()['units'])
        with self.assertRaises(ConflictError):
            self.receive(p, 1)

    def test_retry_stale_and_invalid_prices(self):
        draft = self.create(3)
        p = self.action(draft, 'confirm')
        with self.assertRaises(ConflictError):
            self.action(draft, 'cancel', reason='舊畫面')
        # Empty prices are now valid pending costs; malformed amounts still reject.
        for price in ('-1', '0.001', 'abc'):
            with self.assertRaises(ValueError):
                self.action(p, 'receive', number='BAD', freight='0', allocation='quantity',
                    lines=[dict(product_id=self.product['id'], quantity=1, unit_price=price)])
        body = dict(operation_id='repeat', expected_version=p['version'], number='R1', freight='0',
            allocation='quantity', lines=[dict(product_id=self.product['id'], quantity=1, unit_price='0')])
        result = self.service.action(p['id'], 'receive', body)
        self.assertEqual(self.service.action(p['id'], 'receive', body), result)
        self.assertEqual(len(Stock(self.store).snapshot()['units']), 1)

    def test_overreceipt_and_failure_roll_back_all(self):
        p = self.action(self.create(3), 'confirm')
        with self.assertRaises(ConflictError):
            self.receive(p, 4)
        with patch('ops.stock.stock_event', side_effect=RuntimeError('write failed')):
            with self.assertRaises(RuntimeError):
                self.receive(p, 2)
        snap = Stock(self.store).snapshot()
        self.assertEqual((len(snap['units']), len(snap['receipts'])), (0, 0))
        current = self.service.snapshot()['purchases'][0]
        self.assertEqual((current['version'], current['lines'][0]['received']), (p['version'], 0))

    def test_concurrent_receipts_only_one_succeeds(self):
        p = self.action(self.create(3), 'confirm')
        def run(_):
            try:
                return self.receive(p, 2)['state']
            except ConflictError:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(run, range(2)))
        self.assertCountEqual(outcomes, ['partial', 'conflict'])
        self.assertEqual(len(Stock(self.store).snapshot()['units']), 2)

    def test_draft_edit_and_schedule_do_not_change_ordered_quantities(self):
        p = self.create(2)
        body = dict(operation_id='edit', id=p['id'], expected_version=p['version'], number=p['number'],
            supplier_id=p['supplier_id'], expected_date='', comment='改數量',
            lines=[dict(product_id=self.product['id'], quantity=5, quoted_price='1.23')])
        p = self.service.save(body)
        self.assertEqual(p['lines'][0]['quoted_price_cents'], 123)
        p = self.action(p, 'confirm')
        body.update(operation_id='illegal', expected_version=p['version'])
        with self.assertRaises(ConflictError):
            self.service.save(body)
        p = self.action(p, 'schedule', expected_date='2026-12-01', comment='延後')
        self.assertEqual(p['lines'][0]['quantity'], 5)
        self.assertEqual(p['expected_date'], '2026-12-01')

    def test_multiple_lines_overreceipt_and_value_freight(self):
        second = Catalogue(self.store).create(dict(operation_id='second-product', name='第二成品', sku='SECOND'))
        p = self.service.save(dict(operation_id='multi', number='MULTI', supplier_id=self.supplier['id'], lines=[
            dict(product_id=self.product['id'], quantity=2), dict(product_id=second['id'], quantity=1)]))
        p = self.action(p, 'confirm')
        with self.assertRaises(ConflictError):
            self.action(p, 'receive', number='OVER', freight='1', allocation='value', lines=[
                dict(product_id=self.product['id'], quantity=2, unit_price='1'),
                dict(product_id=second['id'], quantity=2, unit_price='3')])
        self.assertEqual(Stock(self.store).snapshot()['units'], [])
        p = self.action(p, 'receive', number='VALUE', freight='0.03', allocation='value', lines=[
            dict(product_id=self.product['id'], quantity=2, unit_price='1'),
            dict(product_id=second['id'], quantity=1, unit_price='3')])
        units = Stock(self.store).snapshot()['units']
        self.assertEqual(sorted(u['cost_cents'] for u in units), [100, 101, 302])
        self.assertEqual(p['state'], 'received')

    def test_disabled_products_duplicate_skus_and_date_validation(self):
        for overrides in ({'expected_date':'2026-02-30'}, {'lines':[]},
                {'lines':[dict(product_id=self.product['id'], quantity=1)]*2}):
            body = dict(operation_id=uuid4().hex, number='BAD', supplier_id=self.supplier['id'],
                lines=[dict(product_id=self.product['id'], quantity=1)])
            body.update(overrides)
            with self.assertRaises(ValueError):
                self.service.save(body)
        p = self.action(self.create(2), 'confirm')
        with self.store.connect() as db:
            db.execute('UPDATE product_details SET active=0 WHERE product_id=?', (self.product['id'],))
        with self.assertRaises(ValueError):
            self.receive(p, 1)
        self.assertEqual(Stock(self.store).snapshot()['units'], [])

    def test_receive_races_cancellation_without_inventory_corruption(self):
        p = self.action(self.create(3), 'confirm')
        def run(action):
            try:
                return (self.receive(p, 2) if action=='receive' else self.action(p, 'cancel', reason='停供'))['state']
            except ConflictError:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(run, ['receive','cancel']))
        self.assertEqual(outcomes.count('conflict'), 1)
        state = self.service.snapshot()['purchases'][0]['state']
        self.assertEqual(len(Stock(self.store).snapshot()['units']), 2 if state=='partial' else 0)


if __name__ == '__main__':
    unittest.main()
