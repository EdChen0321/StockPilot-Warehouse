"""Boundary/state/atomicity checks against isolated databases only."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_production_receipts
from ops.catalogue import Catalogue
from ops.store import ConflictError
from ops.stock import Stock


class ProductionEdgeTests(unittest.TestCase):
    setUp = test_production_receipts.ProductionTests.setUp
    plan = test_production_receipts.ProductionTests.plan

    def draft(self, **extra):
        return self.m.plan(dict(operation_id='draft',number='DRAFT',product_id=self.p['id'],
                               factory_id=self.f['id'],quantity=100,**extra))

    def business(self):
        with self.store.connect() as db:
            tables=['production_plans','production_details','production_receipts','production_purchases',
                    'plan_versions','materials','material_purchases','material_movements','receipts','stock_units','stock_events','operations']
            return {t:[tuple(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY rowid')] for t in tables}

    def stock_material(self, amount='30'):
        po=self.m.purchase(dict(operation_id='po',number='PO',supplier_id=self.s['id'],
            lines=[dict(material_id=self.mat['id'],quantity=amount)]))
        self.m.purchase_action(po['id'],'confirm',dict(operation_id='order-po'))
        self.m.movement(dict(operation_id='mr',number='MR',kind='receipt',purchase_id=po['id'],
            lines=[dict(material_id=self.mat['id'],quantity=amount)]))
        return po

    def test_confirm_rechecks_product_disabled_after_draft(self):
        p=self.draft()
        Catalogue(self.store).update(self.p['id'],dict(operation_id='disable',expected_version=1,active=False))
        before=self.business()
        with self.assertRaises(ConflictError):
            self.m.plan_action(p['id'],'confirm',dict(operation_id='confirm',expected_version=1))
        self.assertEqual(before,self.business())

    def test_explicit_empty_factory_does_not_create_legacy_bom_plan(self):
        self.m.bom(dict(operation_id='bom',product_id=self.p['id'],expected_version=0,
            lines=[dict(material_id=self.mat['id'],quantity='1',unit='個')]))
        for index, factory in enumerate(['',None,False,0]):
            with self.subTest(factory=factory):
                before=self.business()
                with self.assertRaises(ValueError):
                    self.m.plan(dict(operation_id='bad'+str(index),number='BAD'+str(index),product_id=self.p['id'],factory_id=factory,quantity=1))
                self.assertEqual(before,self.business())

    def test_invalid_quantities_versions_dates_and_failed_retry(self):
        p=self.plan()
        invalid=[0,-1,1.5,True,'1',None,10001]
        for index, qty in enumerate(invalid):
            with self.subTest(quantity=qty):
                before=self.business()
                with self.assertRaises(ValueError):
                    self.m.plan_action(p['id'],'receive',dict(operation_id='qty'+str(index),number='R',quantity=qty,expected_version=p['version']))
                self.assertEqual(before,self.business())
        for index, version in enumerate([None,True,'2',1,999]):
            before=self.business()
            with self.assertRaises(ConflictError):
                self.m.plan_action(p['id'],'receive',dict(operation_id='version'+str(index),number='R',quantity=1,expected_version=version))
            self.assertEqual(before,self.business())
        for index, date in enumerate(['2026-02-30','2026-2-1',0,None]):
            before=self.business()
            with self.assertRaises(ValueError):
                self.m.plan_action(p['id'],'schedule',dict(operation_id='date'+str(index),expected_date=date,expected_version=p['version']))
            self.assertEqual(before,self.business())
        result=self.m.plan_action(p['id'],'schedule',dict(operation_id='date0',expected_date='2026-12-01',comment='更新',expected_version=p['version']))
        self.assertEqual(result['expected_date'],'2026-12-01')
        self.assertEqual(result['comment'],'更新')

    def test_duplicate_receipt_and_operation_payload_collision(self):
        p=self.plan()
        body=dict(operation_id='receive',number='R',quantity=1,expected_version=p['version'])
        result=self.m.plan_action(p['id'],'receive',body)
        before=self.business()
        for change in [dict(quantity=2),dict(operation_id='different',expected_version=result['version'])]:
            with self.assertRaises(ConflictError):
                self.m.plan_action(p['id'],'receive',{**body,**change})
            self.assertEqual(before,self.business())
        self.assertEqual(self.m.plan_action(p['id'],'receive',body),result)
        self.assertEqual(before,self.business())

    def test_terminal_states_and_revision_to_received_quantity(self):
        p=self.plan()
        p=self.m.plan_action(p['id'],'receive',dict(operation_id='r',number='R',quantity=40,expected_version=p['version']))
        p=self.m.plan_action(p['id'],'revise',dict(operation_id='resize',quantity=40,expected_version=p['version']))
        self.assertEqual((p['state'],p['pending']),('received',0))
        for act,values in [('confirm',{}),('receive',dict(number='R2',quantity=1)),('revise',dict(quantity=100)),('cancel',dict(reason='x')),('schedule',dict(expected_date=''))]:
            before=self.business()
            with self.assertRaises(ConflictError):
                self.m.plan_action(p['id'],act,dict(operation_id=act,expected_version=p['version'],**values))
            self.assertEqual(before,self.business())
        with self.store.connect() as db:
            latest=json.loads(db.execute('SELECT snapshot FROM plan_versions WHERE plan_id=? ORDER BY version DESC',(p['id'],)).fetchone()[0])
        self.assertEqual((latest['state'],latest['received'],latest['pending']),('received',40,0))

    def test_cancel_keeps_consumption_and_does_not_cancel_related_purchase(self):
        p=self.plan();self.stock_material()
        linked=self.m.purchase(dict(operation_id='linked',number='LINKED',supplier_id=self.s['id'],plan_id=p['id'],lines=[dict(material_id=self.mat['id'],quantity='10')]))
        self.m.purchase_action(linked['id'],'confirm',dict(operation_id='linked-confirm'))
        self.m.movement(dict(operation_id='use',number='USE',kind='consume',plan_id=p['id'],lines=[dict(material_id=self.mat['id'],quantity='25')]))
        p=self.m.snapshot()['plans'][0]
        p=self.m.plan_action(p['id'],'cancel',dict(operation_id='cancel',reason='取消餘量',expected_version=p['version']))
        snapshot=self.m.snapshot()
        self.assertEqual(snapshot['materials'][0]['on_hand'],'5')
        self.assertEqual(next(x for x in snapshot['purchases'] if x['id']==linked['id'])['state'],'ordered')
        for act,values in [('confirm',{}),('receive',dict(number='R2',quantity=1)),('revise',dict(quantity=100))]:
            before=self.business()
            with self.assertRaises(ConflictError):
                self.m.plan_action(p['id'],act,dict(operation_id=act,expected_version=p['version'],**values))
            self.assertEqual(before,self.business())
        before=self.business()
        with self.assertRaises(ConflictError):
            self.m.movement(dict(operation_id='late-use',number='USE2',kind='consume',plan_id=p['id'],lines=[dict(material_id=self.mat['id'],quantity='1')]))
        self.assertEqual(before,self.business())

    def test_concurrent_shared_material_consumption_and_duplicate_number(self):
        p=self.plan();self.stock_material('3')
        def use(index):
            try:
                self.m.movement(dict(operation_id='use'+str(index),number='USE'+str(index),kind='consume',plan_id=p['id'],lines=[dict(material_id=self.mat['id'],quantity='2')]))
                return 'ok'
            except ConflictError:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(use,[1,2]))
        self.assertCountEqual(results,['ok','conflict'])
        self.assertEqual(self.m.snapshot()['materials'][0]['on_hand'],'1')
        event=next(x for x in self.m.snapshot()['movements'] if x['kind']=='consume')
        before=self.business()
        with self.assertRaises(ConflictError):
            self.m.movement(dict(operation_id='duplicate',number=event['number'],kind='consume',plan_id=p['id'],lines=[dict(material_id=self.mat['id'],quantity='1')]))
        self.assertEqual(before,self.business())

    def test_fractional_material_boundary_and_audit_failure_rollback(self):
        p=self.plan();self.stock_material('0.003')
        body=dict(operation_id='use',number='USE',kind='consume',plan_id=p['id'],lines=[dict(material_id=self.mat['id'],quantity='0.001')])
        before=self.business()
        with patch.object(self.store,'event',side_effect=RuntimeError('injected failure')):
            with self.assertRaises(RuntimeError):self.m.movement(body)
        self.assertEqual(before,self.business())
        self.m.movement(body)
        self.assertEqual(self.m.snapshot()['materials'][0]['on_hand'],'0.002')
        for index,qty in enumerate(['0.0001','0','-1','1e-3',0.001]):
            before=self.business()
            with self.assertRaises(ValueError):
                self.m.movement(dict(body,operation_id='bad'+str(index),number='BAD'+str(index),lines=[dict(material_id=self.mat['id'],quantity=qty)]))
            self.assertEqual(before,self.business())

    def test_inactive_product_receipt_rolls_back_and_cost_repair_preserves_counts(self):
        p=self.plan()
        c=Catalogue(self.store)
        c.update(self.p['id'],dict(operation_id='disable',expected_version=1,active=False))
        before=self.business()
        body=dict(operation_id='receive',number='R',quantity=2,expected_version=p['version'])
        with self.assertRaises(ConflictError):self.m.plan_action(p['id'],'receive',body)
        self.assertEqual(before,self.business())
        c.update(self.p['id'],dict(operation_id='enable',expected_version=2,active=True))
        p=self.m.plan_action(p['id'],'receive',body)
        receipt=p['receipts'][0]
        Stock(self.store).revise_receipt_cost(receipt['id'],dict(operation_id='cost',expected_cost_version=0,reason='補成本',
            freight='0.01',tax='0',allocation='quantity',lines=[dict(product_id=self.p['id'],quantity=2,unit_price='10')]))
        snap=Stock(self.store).snapshot()
        self.assertEqual(sum(u['cost_cents'] for u in snap['units']),2001)
        after=self.m.snapshot()['plans'][0]
        self.assertEqual((after['received'],after['pending']),(2,98))


if __name__=='__main__':unittest.main()
