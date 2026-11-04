import unittest
import test_http as fixtures
from test_source_flow_fixes import command
from ops.catalogue import Catalogue


class SourceFlowHttpTests(unittest.TestCase):
    setUp = fixtures.HttpTests.setUp
    tearDown = fixtures.HttpTests.tearDown
    request = fixtures.HttpTests.request

    def test_manual_mixed_order_and_cancelled_entry_route(self):
        cat=Catalogue(self.server.store)
        account=cat.account(command(name='合成來源',platform='SHOPLINE'))
        product=cat.create(command(name='合成商品',sku='BOX',barcode='BOX'))
        status,result=self.request('/api/orders',command(account_id=account['id'],order_id='HTTP-MIXED',
            ordered_at='2026-01-01T00:00:00+08:00',utm_medium='example',order_total='960',
            lines=[dict(product_id=product['id'],quantity=1,unit_price='1000'),
                   dict(line_type='non_stock',item_name='數位券',quantity=3,unit_price='0')]))
        self.assertEqual(status,200)
        status,result=self.request('/api/orders')
        order=result['orders'][0]
        self.assertEqual(order['utm_medium'],'example')
        self.assertEqual(order['order_total_cents'],96000)
        self.assertEqual(order['lines'][1]['line_type'],'non_stock')
        status,_=self.request('/api/orders/cancel',command(account_id=account['id'],order_id='HTTP-MIXED',expected_revision=1))
        self.assertEqual(status,200)
        status,_=self.request('/api/shipments',command(entry_number='HTTP-MIXED'))
        self.assertEqual(status,409)
        self.assertEqual(self.request('/api/stock')[1]['shipments'],[])
