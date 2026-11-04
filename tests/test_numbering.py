import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
from ops.store import Store
from ops.catalogue import Catalogue
from ops.stock import Stock
from ops.orders import Orders


class NumberingTests(unittest.TestCase):
    def test_date_random_collision_and_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'db')
            p=Catalogue(store).create(dict(operation_id='p',name='合成',sku='NUMBER'))
            stock=Stock(store)
            source=Catalogue(store).account(dict(operation_id='source',name='合成來源',platform='manual'))
            body=dict(operation_id='order',account_id=source['id'],ordered_at='2026-10-28T10:00:00+08:00',lines=[dict(product_id=p['id'],quantity=2,unit_price='1')])
            prefix=datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d')
            with patch('ops.numbering.randbelow',side_effect=[42,42,661102]):
                first=Orders(store).receive(body)
                self.assertEqual(first['order_id'],prefix+'000042')
                self.assertEqual(Orders(store).receive(body),first)
                second=Orders(store).receive(dict(body,operation_id='order2'))
                self.assertEqual(second['order_id'],prefix+'661102')
            order=first
            self.assertRegex(order['order_id'],r'^\d{14}$')
            shipment_body=dict(operation_id='ship',account_id=source['id'],order_id=order['order_id'])
            with self.assertRaises(ValueError):
                stock.shipment(shipment_body)
            shipment_body['number']='MY-SHIPMENT'
            shipment=stock.shipment(shipment_body)
            self.assertEqual(shipment['number'],'MY-SHIPMENT')
            self.assertEqual(stock.shipment(shipment_body),shipment)
            receipt_body=dict(operation_id='in',lines=[dict(product_id=p['id'],quantity=1,unit_price='1')])
            with self.assertRaises(ValueError):
                stock.receipt(receipt_body)
            receipt_body['number']='MY-RECEIPT'
            receipt=stock.receipt(receipt_body)
            self.assertEqual(receipt['number'],'MY-RECEIPT')
            self.assertEqual(stock.receipt(receipt_body),receipt)
