import unittest
from tests import test_stock as fixtures
from unittest.mock import patch
from ops.stock_reports import report


class ProductAnalyticsTests(unittest.TestCase):
    setUp = fixtures.StockTests.setUp
    receipt = fixtures.StockTests.receipt

    def test_daily_stock_zero_days_and_unit_drilldown(self):
        receipt = self.receipt()
        self.stock.confirm_receipt(receipt['id'], dict(operation_id='confirm'))
        shipment = self.stock.shipment(dict(operation_id='main', entry_number='ANALYTICS'))
        unit = self.stock.scan(shipment['id'], dict(operation_id='scan', barcode='0001'))['unit_id']
        self.stock.transition(shipment['id'], 'complete', dict(operation_id='complete'))
        with self.store.connect() as db:
            db.execute("UPDATE stock_events SET time='2026-10-01T00:00:00Z' WHERE kind='receipt'")
            db.execute("UPDATE stock_events SET time='2026-10-02T00:00:00Z' WHERE kind IN ('allocate','ship')")
            db.execute("UPDATE stock_meta SET value='2026-09-01T00:00:00Z' WHERE key='coverage_since'")
        result = report(self.store, start='2026-10-01', end='2026-10-03', as_of='2026-10-04T00:00:00Z', product_id=self.product['id'])
        self.assertEqual([(d['day'], d['on_hand'], d['change']) for d in result['daily_stock']],
                         [('2026-10-01', 3, 3), ('2026-10-02', 2, -1), ('2026-10-03', 2, 0)])
        self.assertEqual(result['unit_records'][0]['unit_id'], unit)
        self.assertEqual(result['unit_records'][0]['shipment_id'], shipment['id'])
        self.assertEqual(result['unit_records'][0]['day'], '2026-10-02')
        other = self.catalogue.create(dict(operation_id='other', name='其他商品', sku='OTHER'))
        empty = report(self.store, start='2026-10-01', end='2026-10-03', as_of='2026-10-04T00:00:00Z', product_id=other['id'])
        self.assertEqual(empty['unit_records'], [])
        self.assertTrue(all(d['on_hand'] == 0 for d in empty['daily_stock']))

    def test_incomplete_history_is_not_zero_inventory(self):
        result = report(self.store, start='2000-01-01', end='2000-01-02', product_id=self.product['id'])
        self.assertTrue(all(d['on_hand'] is None for d in result['daily_stock']))

    def test_reopen_restores_stock_but_removes_sales_then_return_adds_only_once(self):
        with patch('ops.stock.now', return_value='2026-10-01T00:00:00Z'):
            receipt = self.receipt()
            self.stock.confirm_receipt(receipt['id'], dict(operation_id='confirm'))
            shipment = self.stock.shipment(dict(operation_id='main', entry_number='REOPEN'))
            unit = self.stock.scan(shipment['id'], dict(operation_id='scan', barcode='0001'))['unit_id']
            completed = self.stock.transition(shipment['id'], 'complete', dict(operation_id='complete'))
        with patch('ops.stock.now', return_value='2026-10-02T00:00:00Z'):
            self.stock.reopen_shipment(shipment['id'], dict(operation_id='reopen', expected_state='shipped', expected_reopen_count=0, expected_shipped_at=completed['shipped_at']))
        with self.store.connect() as db:
            db.execute("UPDATE stock_meta SET value='2026-09-01T00:00:00Z' WHERE key='coverage_since'")
        query = dict(start='2026-10-01', end='2026-10-03', as_of='2026-10-04T00:00:00Z', product_id=self.product['id'])
        reopened = report(self.store, **query)
        self.assertEqual(reopened['unit_records'], [])
        self.assertEqual([d['on_hand'] for d in reopened['daily_stock']], [2, 3, 3])
        with patch('ops.stock.now', return_value='2026-10-03T00:00:00Z'):
            self.stock.transition(shipment['id'], 'complete', dict(operation_id='complete-again', expected_reopen_count=1))
            self.stock.receive_return(unit, dict(operation_id='return', shipment_id=shipment['id'], reason='實退'))
        result = report(self.store, **query)
        self.assertEqual([r['kind'] for r in result['unit_records']], ['ship', 'return'])
        self.assertEqual(result['daily_stock'][-1]['on_hand'], 3)

