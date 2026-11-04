import csv
import io
import unittest

import test_source_flow_fixes as fixtures
from test_source_flow_fixes import command
from test_orders import row
from ops.importing import FIELDS, parse_orders
from ops.orders import Orders
from ops.sales import sales, export_csv
from ops.insights import sales_insights, crossbuy
from ops.kol import report


def source_csv(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(dict.fromkeys([*FIELDS, 'line_type'])))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


class NonStockTests(unittest.TestCase):
    setUp = fixtures.SourceFlowFixTests.setUp
    receive_stock = fixtures.SourceFlowFixTests.receive_stock

    def mixed(self):
        return [row(expected_line_count='2',order_item_amount='220',order_total='180',utm_medium='sample'),
            row(expected_line_count='2',order_item_amount='220',order_total='180',utm_medium='sample',
                line_id='DIGITAL',external_sku='',item_name='數位券',quantity='4',line_item_amount='20',line_type='non_stock')]

    def test_full_mixed_order_preserves_non_stock_without_catalogue_or_fulfillment(self):
        payload = source_csv(self.mixed())
        batch = self.store.preview(self.account['id'], 'mixed.csv', payload, True)
        self.assertEqual(batch['results'][0]['errors'], [])
        self.store.confirm(batch['id'])
        self.assertEqual(len(self.cat.list()['products']), 1)
        original = self.store.current_orders()[0]
        digital = original['lines'][1]
        self.assertEqual((digital['item_name'],digital['quantity'],digital['amount_cents']), ('數位券',4,2000))
        self.assertEqual((digital['product_id'],digital['external_sku'],digital['line_type']), (None,'','non_stock'))
        self.assertEqual(Orders(self.store).list(q='數位券')[0]['order_id'], 'O-001')
        self.receive_stock()
        shipment = self.stock.shipment(command(entry_number='O-001'))
        self.assertEqual(shipment['lines'], [dict(product_id=self.product['id'],quantity=2)])
        self.stock.scan(shipment['id'],command(barcode='BOX'))
        self.stock.scan(shipment['id'],command(barcode='BOX'))
        self.stock.transition(shipment['id'],'complete',command())
        self.assertEqual(self.stock.snapshot()['products'][0]['on_hand'], 1)
        result = report(self.store)
        self.assertEqual((result['summary']['order_count'],result['summary']['total']), (1,'180.00'))
        self.assertEqual(result['orders'][0]['lines'][1]['name'], '數位券')
        self.assertEqual(len(result['products']), 1)
        result = sales(self.store)
        self.assertEqual(result['amount'], '220.00')
        self.assertEqual(result['physical_quantities'][0]['quantity'], 2)
        self.assertIn('數位券', export_csv(result))
        self.assertEqual(len(sales_insights(self.store)['products']), 1)
        self.assertEqual(crossbuy(self.store)['eligible_orders'], 1)
        self.assertEqual(self.store.preview(self.account['id'],'again.csv',payload,True)['id'],batch['id'])

    def test_missing_sku_requires_explicit_non_stock_and_cannot_mask_stock_sku(self):
        for change in ({'external_sku':''}, {'line_type':'unknown'}, {'line_type':'non_stock'}, {'line_type':'non_stock','external_sku':'','item_name':''}):
            with self.subTest(change=change):
                self.assertTrue(parse_orders(source_csv([row(**change)]))[1][0]['errors'])

    def test_non_stock_only_order_can_be_counted_but_cannot_ship(self):
        payload = source_csv([row(external_sku='',item_name='數位券',line_type='non_stock',order_total='200')])
        batch=self.store.preview(self.account['id'],'only.csv',payload,True)
        self.store.confirm(batch['id'])
        self.assertEqual(report(self.store)['summary']['order_count'],1)
        self.assertEqual(sales(self.store)['physical_quantities'],[])
        cross=crossbuy(self.store)
        self.assertEqual(cross['excluded_bundle_only_orders'],0)
        self.assertEqual(cross['excluded_non_stock_only_orders'],1)
        with self.assertRaises(ValueError):self.stock.shipment(command(entry_number='O-001'))
        self.assertEqual(self.stock.snapshot()['shipments'],[])

    def test_old_stock_fingerprint_unchanged_by_explicit_default(self):
        old=parse_orders(source_csv([row()]))[1][0]
        new=parse_orders(source_csv([row(line_type='stock')]))[1][0]
        self.assertEqual(old['fingerprint'],new['fingerprint'])

    def test_manual_non_stock_lines_and_zero_total(self):
        Orders(self.store).receive(command(account_id=self.account['id'],order_id='DIGITAL',
            ordered_at='2026-01-02T00:00:00+08:00',order_total='0',
            lines=[dict(line_type='non_stock',item_name='數位抽獎券',quantity=2,unit_price='0')]))
        self.assertEqual(report(self.store)['summary']['total'],'0.00')
        self.assertEqual(self.store.current_orders()[0]['lines'][0]['product_id'],None)
        self.assertEqual(len(self.cat.list()['products']),1)
