import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ops.catalogue import Catalogue
from ops.stock import Stock
from ops.store import Store


class ShipmentTimesTests(unittest.TestCase):
    def test_first_completion_and_latest_cancellation_survive_reopen_and_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            stock = Stock(Store(Path(directory)/'test.sqlite3'))
            p = Catalogue(stock.store).create(dict(operation_id='p',name='Time',sku='TIME',barcode='TIME'))
            r = stock.receipt(dict(operation_id='r',number='R',freight='0',allocation='quantity',lines=[dict(product_id=p['id'],quantity=1,unit_price='10')]))
            stock.confirm_receipt(r['id'],dict(operation_id='receive'))
            main = stock.shipment(dict(operation_id='main',entry_number='MAIN'))
            def snapshot():
                return next(s for s in stock.snapshot()['shipments'] if s['id']==main['id'])
            self.assertIsNone(snapshot()['first_completed_at'])
            self.assertIsNone(snapshot()['cancelled_at'])
            stock.scan(main['id'],dict(operation_id='scan',barcode='TIME'))
            with patch('ops.stock.now',return_value='2026-01-01T00:00:00+00:00'):
                done = stock.transition(main['id'],'complete',dict(operation_id='done'))
            stock.transition(main['id'],'reopen',dict(operation_id='reopen',expected_reopen_count=0,expected_shipped_at=done['shipped_at']))
            with patch('ops.store.now',return_value='2026-01-02T00:00:00+00:00'):
                stock.transition(main['id'],'cancel',dict(operation_id='cancel',expected_reopen_count=1))
            # A repeated cancel with a new operation ID must not move its date.
            stock.transition(main['id'],'cancel',dict(operation_id='cancel-again',expected_reopen_count=1))
            self.assertEqual(snapshot()['cancelled_at'],'2026-01-02T00:00:00+00:00')
            stock.transition(main['id'],'reopen',dict(operation_id='reopen-cancel',expected_state='cancelled',expected_reopen_count=1))
            stock.scan(main['id'],dict(operation_id='rescan',barcode='TIME',expected_reopen_count=2))
            with patch('ops.stock.now',return_value='2026-01-03T00:00:00+00:00'):
                stock.transition(main['id'],'complete',dict(operation_id='done-again',expected_reopen_count=2))
            current = snapshot()
            self.assertEqual(current['first_completed_at'],done['shipped_at'])
            self.assertEqual(current['created_at'],main['created_at'])
            self.assertEqual(current['shipped_at'],'2026-01-03T00:00:00+00:00')
            self.assertEqual(current['cancelled_at'],'2026-01-02T00:00:00+00:00')
            child=stock.shipment(dict(operation_id='child',parent_id=main['id'],number='CHILD'))
            with patch('ops.store.now',return_value='2026-01-04T00:00:00+00:00'):
                stock.transition(child['id'],'cancel',dict(operation_id='child-cancel'))
            child=next(s for s in stock.snapshot()['shipments'] if s['id']==child['id'])
            self.assertIsNone(child['first_completed_at'])
            self.assertEqual(child['cancelled_at'],'2026-01-04T00:00:00+00:00')
            self.assertEqual(snapshot(),current)
            stock.transition(main['id'],'reopen',dict(operation_id='reopen-second',expected_reopen_count=2,expected_shipped_at=current['shipped_at']))
            with patch('ops.store.now',return_value='2026-01-05T00:00:00+00:00'):
                stock.transition(main['id'],'cancel',dict(operation_id='cancel-second',expected_reopen_count=3))
            self.assertEqual(snapshot()['cancelled_at'],'2026-01-05T00:00:00+00:00')
            self.assertEqual(snapshot()['first_completed_at'],done['shipped_at'])
