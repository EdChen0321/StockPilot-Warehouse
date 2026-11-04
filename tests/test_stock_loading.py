import json
import tempfile
import unittest
from pathlib import Path

from ops.store import Store
from ops.stock import Stock
from ops.catalogue import Catalogue
from ops.locations import Locations


class StockLoadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'db.sqlite3')
        self.stock = Stock(self.store)
        p = Catalogue(self.store).create(dict(operation_id='p', name='水杯', sku='CUP', barcode='CUP'))
        self.receipt = self.stock.receipt(dict(operation_id='r', number='IN-1',
            lines=[dict(product_id=p['id'], quantity=3, unit_price='10')], freight='0', allocation='quantity'))
        self.stock.confirm_receipt(self.receipt['id'], dict(operation_id='confirm'))
        places = Locations(self.store)
        place = places.save(dict(operation_id='place', address='1F', position='A01', name='一般品櫃',
            can_import=True, can_export=True, can_return=True, entry_state='import'))
        places.assign(dict(operation_id='putaway', receipt_id=self.receipt['id'], place_id=place['id']))
        self.shipment = self.stock.shipment(dict(operation_id='s', entry_number='OUT-1'))
        self.unit = self.stock.scan(self.shipment['id'], dict(operation_id='scan', barcode='CUP'))['unit_id']
        self.stock.transition(self.shipment['id'], 'complete', dict(operation_id='ship'))

    def test_summary_keeps_inventory_and_documents_without_audit_payload(self):
        full = self.stock.snapshot()
        summary = self.stock.snapshot(summary=True)
        for key in ('products', 'units', 'receipts', 'groups', 'shipments', 'additions'):
            self.assertEqual(summary[key], full[key], key)
        for key in ('events', 'cost_revisions', 'unit_cost_revisions'):
            self.assertEqual(summary[key], [])
        self.assertLess(len(json.dumps(summary)), len(json.dumps(full)))

    def test_lazy_history_is_complete_and_scoped(self):
        full = self.stock.snapshot()
        result = self.stock.history('unit', self.unit)
        self.assertEqual(result['events'], [e for e in full['events'] if e['unit_id'] == self.unit])
        shipment = self.stock.history('shipment', self.shipment['id'])
        self.assertTrue(any(e['kind'] == 'ship' for e in shipment['events']))
        self.assertTrue(all(e['unit_id'] == self.unit or e['shipment_id'] == self.shipment['id'] for e in shipment['events']))
        with self.assertRaises(ValueError):
            self.stock.history('anything', self.unit)
        with self.assertRaises(KeyError):
            self.stock.history('unit', 'missing')

    def test_lazy_cost_history_and_reopened_shipment_match_full_history(self):
        self.stock.revise_receipt_cost(self.receipt['id'], dict(operation_id='cost', expected_cost_version=0,
            reason='補登運費', lines=[dict(product_id=self.receipt['lines'][0]['product_id'], unit_price='12')],
            freight='3', tax='0', allocation='quantity'))
        full = self.stock.snapshot()
        unit = self.stock.history('unit', self.unit)
        self.assertEqual(unit['unit_cost_revisions'], [e for e in full['unit_cost_revisions'] if e['unit_id'] == self.unit])
        self.assertEqual(self.stock.history('receipt', self.receipt['id'])['cost_revisions'], full['cost_revisions'])
        shipment = full['shipments'][0]
        self.stock.transition(shipment['id'], 'reopen', dict(operation_id='reopen', expected_reopen_count=0,
            expected_shipped_at=shipment['shipped_at']))
        self.stock.transition(shipment['id'], 'complete', dict(operation_id='reship', expected_reopen_count=1))
        full = self.stock.snapshot()
        history = self.stock.history('shipment', shipment['id'])
        self.assertEqual([e for e in history['events'] if e['shipment_id'] == shipment['id']],
            [e for e in full['events'] if e['shipment_id'] == shipment['id']])


if __name__ == '__main__':
    unittest.main()
