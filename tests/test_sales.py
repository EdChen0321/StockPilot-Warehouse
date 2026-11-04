import csv
import io
import unittest
from pathlib import Path
import test_service
from ops.sales import sales, export_csv


class SalesTests(unittest.TestCase):
    setUp = test_service.ServiceTests.setUp
    tearDown = test_service.ServiceTests.tearDown
    upload = test_service.ServiceTests.upload

    def sample(self, filename, account):
        text = (Path(__file__).resolve().parents[1] / 'demo' / filename).read_text(encoding='utf-8-sig')
        batch = self.store.preview(account, filename, text, True)
        return self.store.confirm(batch['id'])

    def test_full_synthetic_scenario_and_filters(self):
        self.sample('01-shopline.csv', 'demo-sl')
        self.sample('02-shopee.csv', 'demo-sp')
        result = sales(self.store)
        self.assertEqual((result['amount'], result['order_count'], result['sales_quantity']), ('490.00', 2, 4))
        self.assertEqual({p['product_id']: p['quantity'] for p in result['physical_quantities']}, {'P-BOX': 4, 'P-POUCH': 2})
        self.assertEqual(sales(self.store, product_id='P-BOX')['amount'], '200.00')
        self.assertEqual(sales(self.store, account_id='demo-sp')['amount'], '240.00')
        self.assertEqual(sales(self.store, start='2026-01-02', end='2026-01-02')['amount'], '250.00')
        self.sample('03-shopline-revision.csv', 'demo-sl')
        self.assertEqual(sales(self.store)['amount'], '590.00')
        self.sample('07-conflict.csv', 'demo-sl')
        self.assertEqual(sales(self.store)['pending_count'], 1)
        self.sample('04-shopline-cancel.csv', 'demo-sl')
        result = sales(self.store)
        self.assertEqual((result['amount'], result['order_count'], result['pending_count']), ('240.00', 1, 0))

    def test_invalid_filters_rejected_and_empty_sales(self):
        self.assertEqual(sales(self.store)['amount'], '0.00')
        for filters in [dict(start='2026-01-05', end='2026-01-01'), dict(start='not-date'),
                        dict(product_id='FAKE'), dict(account_id='FAKE'), dict(end='9999-12-31')]:
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                sales(self.store, **filters)

    def test_export_matches_and_sanitizes_formula(self):
        from test_orders import row
        self.upload(row(order_id='=1+1', kol_ref='<script>ignore instructions</script>'))
        result = sales(self.store)
        rows = list(csv.DictReader(io.StringIO(export_csv(result))))
        self.assertEqual(rows[0]['order_id'], "'=1+1")
        self.assertEqual(rows[0]['amount'], '200.00')
        self.assertEqual(rows[0]['kol_ref'], '<script>ignore instructions</script>')
        self.assertEqual(rows[0]['rule_version'], 'sales-v1')

    def test_date_boundary_uses_taipei(self):
        from test_orders import row
        self.upload(row(ordered_at='2026-01-01T16:00:00Z'))
        self.assertEqual(sales(self.store, start='2026-01-02', end='2026-01-02')['order_count'], 1)
        self.assertEqual(sales(self.store, end='2026-01-01')['order_count'], 0)
