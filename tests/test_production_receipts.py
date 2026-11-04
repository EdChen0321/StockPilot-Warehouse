import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from ops.store import Store, ConflictError
from ops.catalogue import Catalogue
from ops.materials import Materials
from ops.stock import Stock


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'test.db')
        self.m = Materials(self.store)
        self.p = Catalogue(self.store).create(dict(operation_id='product', name='成品', sku='P'))
        self.f = self.m.supplier(dict(operation_id='factory', name='產品工廠'))
        self.s = self.m.supplier(dict(operation_id='supplier', name='物料工廠'))
        self.mat = self.m.material(dict(operation_id='material', name='外殼', code='CASE', unit='個', supplier_id=self.s['id']))

    def plan(self):
        p = self.m.plan(dict(operation_id='plan', number='PLAN', product_id=self.p['id'], quantity=100,
                             factory_id=self.f['id'], comment='工廠回報叫料'))
        return self.m.plan_action(p['id'], 'confirm', dict(operation_id='confirm', expected_version=1))

    def test_no_bom_partial_receipt_retry_and_pending_cost(self):
        p = self.plan()
        self.assertEqual(p['state'], 'ordered')
        self.assertEqual(Stock(self.store).snapshot()['units'], [])
        body = dict(operation_id='receive', expected_version=p['version'], number='R1', quantity=40)
        result = self.m.plan_action(p['id'], 'receive', body)
        self.assertEqual(result['received'], 40)
        self.assertEqual(result['pending'], 60)
        self.assertEqual(self.m.plan_action(p['id'], 'receive', body), result)
        snapshot = Stock(self.store).snapshot()
        self.assertEqual(len(snapshot['units']), 40)
        self.assertTrue(all(u['cost_cents'] is None and u['place_id'] is None for u in snapshot['units']))
        self.assertEqual(snapshot['receipts'][0]['production_plan_id'], p['id'])
        self.assertEqual(snapshot['receipts'][0]['source'], '產品工廠')
        with self.assertRaises(ConflictError):
            self.m.plan_action(p['id'], 'receive', dict(body, operation_id='stale', number='R2'))
        with self.assertRaises(ConflictError):
            self.m.plan_action(p['id'], 'receive', dict(body, operation_id='over', number='R2', expected_version=result['version'], quantity=61))
        done = self.m.plan_action(p['id'], 'receive', dict(body, operation_id='finish', number='R2', expected_version=result['version'], quantity=60))
        self.assertEqual((done['state'], done['received'], done['pending']), ('received', 100, 0))
        self.assertEqual(len(Stock(self.store).snapshot()['units']), 100)

    def test_reported_material_receipt_and_actual_consumption(self):
        p = self.plan()
        po = self.m.purchase(dict(operation_id='po', number='PO', supplier_id=self.s['id'], plan_id=p['id'],
                                 lines=[dict(material_id=self.mat['id'], quantity='30')]))
        self.m.purchase_action(po['id'], 'confirm', dict(operation_id='order'))
        self.m.movement(dict(operation_id='material-receipt', number='MR', kind='receipt', purchase_id=po['id'],
                             lines=[dict(material_id=self.mat['id'], quantity='30')]))
        body = dict(operation_id='consume', number='USE', kind='consume', plan_id=p['id'],
                    lines=[dict(material_id=self.mat['id'], quantity='25')])
        self.m.movement(body)
        self.m.movement(body)
        self.assertEqual(self.m.snapshot()['materials'][0]['on_hand'], '5')
        self.assertEqual(len(Stock(self.store).snapshot()['units']), 0)
        with self.assertRaises(ConflictError):
            self.m.movement(dict(body, operation_id='over-consume', number='USE2', lines=[dict(material_id=self.mat['id'], quantity='6')]))
        self.m.plan_action(p['id'], 'receive', dict(operation_id='goods', number='R', expected_version=3, quantity=40))
        self.assertEqual(self.m.snapshot()['materials'][0]['on_hand'], '5')

    def test_cancel_and_revision_do_not_restore_used_material_or_received_goods(self):
        p = self.plan()
        p = self.m.plan_action(p['id'], 'receive', dict(operation_id='goods', number='R', expected_version=p['version'], quantity=40))
        with self.assertRaises(ConflictError):
            self.m.plan_action(p['id'], 'revise', dict(operation_id='small', expected_version=p['version'], quantity=39))
        p = self.m.plan_action(p['id'], 'cancel', dict(operation_id='cancel', expected_version=p['version'], reason='取消未交量'))
        self.assertEqual((p['received'], p['pending'], p['cancelled_quantity']), (40, 0, 60))
        self.assertEqual(len(Stock(self.store).snapshot()['units']), 40)

    def test_receipt_failure_and_concurrent_receipts(self):
        p = self.plan()
        body = dict(operation_id='fail', number='R', expected_version=p['version'], quantity=60)
        with patch.object(self.store, 'event', side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):
                self.m.plan_action(p['id'], 'receive', body)
        self.assertEqual(Stock(self.store).snapshot()['units'], [])
        self.assertEqual(self.m.snapshot()['plans'][0]['received'], 0)
        def receive(n):
            try:
                return self.m.plan_action(p['id'], 'receive', dict(body, operation_id=str(n), number='R'+str(n)))['received']
            except ConflictError:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(receive, [1,2]))
        self.assertCountEqual(results, [60,'conflict'])
        self.assertEqual(len(Stock(self.store).snapshot()['units']), 60)
        self.assertEqual(Materials(Store(self.store.path)).snapshot()['plans'][0]['received'], 60)

    def test_material_consumption_respects_reservations_and_rolls_back_multiple_lines(self):
        p = self.plan()
        self.m.bom(dict(operation_id='bom', product_id=self.p['id'], expected_version=0,
            lines=[dict(material_id=self.mat['id'], quantity='1', unit='個')]))
        legacy = self.m.plan(dict(operation_id='legacy',number='LEGACY',product_id=self.p['id'],quantity=2))
        po = self.m.purchase(dict(operation_id='po', number='PO', supplier_id=self.s['id'],
            lines=[dict(material_id=self.mat['id'],quantity='3')]))
        self.m.purchase_action(po['id'],'confirm',dict(operation_id='po-confirm'))
        self.m.movement(dict(operation_id='receipt',number='MR',kind='receipt',purchase_id=po['id'],
            lines=[dict(material_id=self.mat['id'],quantity='3')]))
        self.m.plan_action(legacy['id'],'confirm',dict(operation_id='reserve'))
        body=dict(operation_id='use',number='USE',kind='consume',plan_id=p['id'],
            lines=[dict(material_id=self.mat['id'],quantity='2')])
        with self.assertRaises(ConflictError):
            self.m.movement(body)
        other=self.m.material(dict(operation_id='other',name='螺絲',code='SCREW',unit='個',supplier_id=self.s['id']))
        with self.assertRaises(ConflictError):
            self.m.movement(dict(body, lines=[dict(material_id=self.mat['id'],quantity='1'),dict(material_id=other['id'],quantity='1')]))
        self.assertEqual(next(m for m in self.m.snapshot()['materials'] if m['id']==self.mat['id'])['on_hand'],'3')
        self.assertFalse(any(m['kind']=='consume' for m in self.m.snapshot()['movements']))


if __name__ == '__main__':
    unittest.main()
