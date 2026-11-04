import csv
import io
import unittest
from ops.stock_reports import export_report


class StockExportTests(unittest.TestCase):
    def test_shared_numbers_metadata_and_formula_safety(self):
        result=dict(start='2026-01-01',end='2026-01-31',as_of='2026-02-01T00:00:00+00:00',timezone='UTC+08:00',rule_version='stock-events-v1',data_complete=False,
            inventory=[dict(product_id='p1',name='=1+1',sku='00123',on_hand=4,available=1,stock_cost='100.01')],
            monthly=[dict(month='2026-01',received=4,shipped=1,returned=0,shipping_orders=1,shipment_count=2)],
            totals=dict(received=4,shipped=1,returned=0,shipping_orders=1,shipment_count=2,received_cost='100.01',shipped_cost='25.00'))
        for kind in ('inventory','monthly','totals'):
            rows=list(csv.DictReader(io.StringIO(export_report(result,kind))))
            self.assertEqual(rows[0]['as_of'],result['as_of'])
            self.assertEqual(rows[0]['data_complete'],'False')
        row=list(csv.DictReader(io.StringIO(export_report(result,'inventory'))))[0]
        self.assertEqual((row['name'],row['sku'],row['on_hand'],row['stock_cost']),("'=1+1",'00123','4','100.01'))
        with self.assertRaises(ValueError):export_report(result,'unknown')
