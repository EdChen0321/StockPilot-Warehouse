import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from ops.store import Store, ConflictError
from ops.catalogue import Catalogue
from ops.materials import Materials


class MaterialTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / 'materials.sqlite3')
        self.service = Materials(self.store)
        self.product = Catalogue(self.store).create(dict(operation_id='product', name='合成盒', sku='BOX'))
        self.supplier = self.service.supplier(dict(operation_id='supplier', name='合成紙廠'))
        self.material = self.service.material(dict(operation_id='material', code='PAPER', name='合成紙', unit='張', supplier_id=self.supplier['id']))
        self.bom = self.service.bom(dict(operation_id='bom', product_id=self.product['id'], expected_version=0,
            lines=[dict(material_id=self.material['id'], quantity='1.500', unit='張')]))

    def plan(self, number='PLAN', quantity=2):
        return self.service.plan(dict(operation_id=number, number=number, product_id=self.product['id'], quantity=quantity))

    def purchase(self, quantity='3'):
        p = self.service.purchase(dict(operation_id='po', number='PO', supplier_id=self.supplier['id'],
            lines=[dict(material_id=self.material['id'], quantity=quantity)]))
        self.service.purchase_action(p['id'], 'confirm', dict(operation_id='po-confirm'))
        return p

    def move(self, kind, number, quantity, **refs):
        return self.service.movement(dict(operation_id=number, number=number, kind=kind,
            lines=[dict(material_id=self.material['id'], quantity=quantity)], **refs))

    def test_master_bom_versions_and_persistence(self):
        self.assertEqual(self.service.snapshot()['materials'][0]['on_hand'], '0')
        plan = self.plan()
        self.service.bom(dict(operation_id='bom2', product_id=self.product['id'], expected_version=1,
            lines=[dict(material_id=self.material['id'], quantity='2', unit='張')]))
        reopened = Materials(Store(self.store.path)).snapshot()
        self.assertEqual(reopened['plans'][0]['bom_version'], 1)
        self.assertEqual(reopened['plans'][0]['lines'][0]['required'], '3')
        self.assertEqual(len(reopened['boms']), 2)
        with self.assertRaises(ValueError):
            self.service.bom(dict(operation_id='unit', product_id=self.product['id'], expected_version=2,
                lines=[dict(material_id=self.material['id'], quantity='2', unit='公斤')]))
        with self.assertRaises(ValueError):
            self.service.plan(dict(operation_id='missing', number='MISSING', product_id='missing', quantity=1))

    def test_receipts_reservations_issues_cancel_and_real_return(self):
        plan, po = self.plan(), self.purchase()
        self.assertEqual(self.service.summary()['materials'][0]['incoming'], '3')
        with self.assertRaises(ConflictError):
            self.service.plan_action(plan['id'], 'confirm', dict(operation_id='short'))
        first = self.move('receipt', 'R1', '2', purchase_id=po['id'])
        self.assertEqual(self.move('receipt', 'R1', '2', purchase_id=po['id']), first)
        with self.assertRaises(ConflictError):
            self.service.movement(dict(operation_id='different-retry', number='R1', kind='receipt', purchase_id=po['id'], lines=[dict(material_id=self.material['id'], quantity='2')]))
        with self.assertRaises(ConflictError):
            self.service.plan_action(plan['id'], 'confirm', dict(operation_id='short2'))
        self.move('receipt', 'R2', '1', purchase_id=po['id'])
        self.service.plan_action(plan['id'], 'confirm', dict(operation_id='reserve'))
        row = self.service.summary()['materials'][0]
        self.assertEqual((row['available'], row['reserved'], row['shortage']), ('0', '3', '0'))
        issue = self.move('issue', 'ISSUE', '1', plan_id=plan['id'])
        self.service.plan_action(plan['id'], 'cancel', dict(operation_id='cancel'))
        row = self.service.summary()['materials'][0]
        self.assertEqual((row['on_hand'], row['reserved']), ('2', '0'))
        self.move('return', 'RETURN', '1', source_id=issue['id'])
        self.assertEqual(self.service.summary()['materials'][0]['on_hand'], '3')
        with self.assertRaises(ConflictError):
            self.move('return', 'RETURN-EXCESS', '1', source_id=issue['id'])

    def test_shared_summary_filter_and_atomic_revision(self):
        p1, p2, po = self.plan('P1', 2), self.plan('P2', 2), self.purchase('4')
        self.move('receipt', 'R1', '4', purchase_id=po['id'])
        self.assertEqual(self.service.summary()['materials'][0]['shortage'], '2')
        self.assertEqual(self.service.summary([p1['id']])['materials'][0]['shortage'], '0')
        confirmed = self.service.plan_action(p1['id'], 'confirm', dict(operation_id='reserve'))
        before = self.service.snapshot()
        with self.assertRaises(ConflictError):
            self.service.plan_action(p1['id'], 'revise', dict(operation_id='revise-fail', expected_version=confirmed['version'], quantity=4))
        self.assertEqual(self.service.snapshot()['plans'], before['plans'])
        self.assertEqual(self.service.summary()['materials'][0]['reserved'], '3')
        revised = self.service.plan_action(p1['id'], 'revise', dict(operation_id='revise', expected_version=confirmed['version'], quantity=1))
        self.assertEqual(revised['lines'][0]['reserved'], '1.5')
        self.assertEqual(self.service.summary()['materials'][0]['shortage'], '0.5')

    def test_concurrent_reservation_and_movement_rollback(self):
        plans = [self.plan('P1'), self.plan('P2')]
        po = self.purchase()
        self.move('receipt', 'R', '3', purchase_id=po['id'])
        def reserve(p):
            try:
                self.service.plan_action(p['id'], 'confirm', dict(operation_id=p['number']+'-confirm'))
                return True
            except ConflictError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(reserve, plans)), 1)
        chosen = next(p for p in self.service.snapshot()['plans'] if p['state'] == 'confirmed')
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_material_event BEFORE INSERT ON material_movements BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(Exception):
            self.move('issue', 'FAIL', '1', plan_id=chosen['id'])
        row = self.service.summary()['materials'][0]
        self.assertEqual((row['on_hand'], row['reserved']), ('3', '3'))

    def business_state(self):
        snapshot = self.service.snapshot()
        snapshot['summary'].pop('as_of')
        return snapshot

    def second_material(self, supplier=None):
        return self.service.material(dict(operation_id='second-material', code='INK',
            name='合成墨', unit='公升', supplier_id=(supplier or self.supplier)['id']))

    def test_precision_rejected_without_creating_purchase_and_fractional_receipts_are_exact(self):
        before = self.business_state()
        for index, value in enumerate(('0.0001', '1.2345', '-1', '0', '1e3', 1.5)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.purchase(dict(operation_id=f'precision-{index}', number=f'BAD-{index}',
                    supplier_id=self.supplier['id'],
                    lines=[dict(material_id=self.material['id'], quantity=value)]))
            self.assertEqual(self.business_state(), before)
        po = self.purchase('0.003')
        self.move('receipt', 'FRACTION-1', '0.001', purchase_id=po['id'])
        self.assertEqual(self.service.summary()['materials'][0]['incoming'], '0.002')
        self.move('receipt', 'FRACTION-2', '0.002', purchase_id=po['id'])
        row = self.service.summary()['materials'][0]
        self.assertEqual((row['on_hand'], row['incoming']), ('0.003', '0'))

    def test_cross_supplier_purchase_rejected_without_partial_document(self):
        other = self.service.supplier(dict(operation_id='other-supplier', name='合成墨廠'))
        ink = self.second_material(other)
        before = self.business_state()
        with self.assertRaisesRegex(ValueError, '供應商'):
            self.service.purchase(dict(operation_id='mixed-supplier', number='MIXED',
                supplier_id=self.supplier['id'], lines=[
                    dict(material_id=self.material['id'], quantity='3'),
                    dict(material_id=ink['id'], quantity='1')]))
        self.assertEqual(self.business_state(), before)

    def test_multimaterial_overreceipt_rolls_back_first_valid_line(self):
        ink = self.second_material()
        po = self.service.purchase(dict(operation_id='multi-po', number='MULTI',
            supplier_id=self.supplier['id'], lines=[
                dict(material_id=self.material['id'], quantity='3'),
                dict(material_id=ink['id'], quantity='1')]))
        self.service.purchase_action(po['id'], 'confirm', dict(operation_id='multi-confirm'))
        self.move('receipt', 'PARTIAL', '1', purchase_id=po['id'])
        before = self.business_state()
        with self.assertRaisesRegex(ConflictError, '超過'):
            self.service.movement(dict(operation_id='over', number='OVER', kind='receipt',
                purchase_id=po['id'], lines=[
                    dict(material_id=self.material['id'], quantity='1'),
                    dict(material_id=ink['id'], quantity='1.001')]))
        self.assertEqual(self.business_state(), before)
        with self.assertRaisesRegex(ConflictError, '超過'):
            self.move('receipt', 'CUMULATIVE-OVER', '2.001', purchase_id=po['id'])
        self.assertEqual(self.business_state(), before)
        rows = {r['code']:r for r in self.service.summary()['materials']}
        self.assertEqual((rows['PAPER']['on_hand'], rows['PAPER']['incoming']), ('1', '2'))
        self.assertEqual((rows['INK']['on_hand'], rows['INK']['incoming']), ('0', '1'))

    def test_missing_second_material_leaves_whole_plan_and_reservation_unchanged(self):
        ink = self.second_material()
        self.service.bom(dict(operation_id='multi-bom', product_id=self.product['id'], expected_version=1,
            lines=[dict(material_id=self.material['id'], quantity='1.5', unit='張'),
                   dict(material_id=ink['id'], quantity='0.5', unit='公升')]))
        plan = self.plan()
        po = self.purchase('3')
        self.move('receipt', 'PAPER-IN', '3', purchase_id=po['id'])
        before = self.business_state()
        with self.assertRaisesRegex(ConflictError, '不足'):
            self.service.plan_action(plan['id'], 'confirm', dict(operation_id='missing-ink'))
        self.assertEqual(self.business_state(), before)
        self.assertEqual(self.service.snapshot()['plans'][0]['state'], 'draft')
        self.assertTrue(all(r['reserved']=='0' for r in self.service.summary()['materials']))
