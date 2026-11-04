"""Cancelled shipment reopens empty; released stock belongs to its current owner."""
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
import test_shipment_reopen as fixture
from ops.store import ConflictError
from ops.shipping_statistics import shipping_statistics
from ops.stock_reports import report


class CancelReopenTests(unittest.TestCase):
    setUp=fixture.ReopenTests.setUp
    receipt=fixture.ReopenTests.receipt
    body=fixture.ReopenTests.body
    completed=fixture.ReopenTests.completed
    reopen=fixture.ReopenTests.reopen

    def cancelled(self):
        s=self.completed();self.reopen(s)
        return self.stock.transition(s['id'],'cancel',self.body(expected_reopen_count=1))

    def reopen_cancelled(self,s,body=None):
        return self.stock.transition(s['id'],'reopen',body or self.body(expected_state='cancelled',expected_reopen_count=s['reopen_count']))

    def test_old_goods_owned_elsewhere_are_never_reclaimed(self):
        s=self.cancelled()
        other=self.stock.shipment(self.body(entry_number='OTHER'))
        taken=[self.stock.scan(other['id'],self.body(barcode='0001'))['unit_id'] for _ in range(2)]
        self.assertEqual(set(taken),set(self.units))
        self.stock.transition(other['id'],'complete',self.body())
        before=self.stock.snapshot();opened=self.reopen_cancelled(s)
        self.assertEqual((opened['state'],opened['lines'],opened['reopen_count']),('draft',[],2))
        after=self.stock.snapshot()
        self.assertEqual(before['units'],after['units']);self.assertEqual(before['products'],after['products'])
        self.assertEqual([e for e in before['events'] if e['unit_id']],[e for e in after['events'] if e['unit_id']])
        with self.assertRaises(ConflictError):self.stock.transition(s['id'],'complete',self.body(expected_reopen_count=2))
        fresh=self.stock.scan(s['id'],self.body(barcode='0001',expected_reopen_count=2))['unit_id']
        self.assertNotIn(fresh,taken)
        self.stock.transition(s['id'],'complete',self.body(expected_reopen_count=2))
        self.assertEqual(self.stock.snapshot()['products'][0]['on_hand'],0)
        stats=shipping_statistics(self.store,start='2026-01-01',end='2099-12-31')
        self.assertEqual((stats['totals']['shipped'],stats['totals']['shipped_cost']),(3,'310.00'))
        self.assertEqual(report(self.store,start='2026-01-01',end='2099-12-31')['inventory'][0]['on_hand'],0)

    def test_retries_and_stale_item_actions_do_not_touch_new_round(self):
        s=self.cancelled();body=self.body(expected_state='cancelled',expected_reopen_count=1)
        opened=self.reopen_cancelled(s,body)
        self.assertEqual(self.reopen_cancelled(s,body),opened)
        with self.assertRaises(ConflictError):self.reopen_cancelled(s)
        before=self.stock.snapshot()
        with self.assertRaises(ConflictError):self.stock.scan(s['id'],self.body(barcode='0001',expected_reopen_count=1))
        self.assertEqual(self.stock.snapshot(),before)
        fresh=self.stock.scan(s['id'],self.body(barcode='0001',expected_reopen_count=2))['unit_id']
        with self.assertRaises(ConflictError):self.stock.cancel_item(s['id'],fresh,self.body(expected_reopen_count=1))
        self.assertEqual(next(u for u in self.stock.snapshot()['units'] if u['id']==fresh)['shipment_id'],s['id'])
        self.stock.cancel_item(s['id'],fresh,self.body(expected_reopen_count=2))
        self.assertEqual(self.stock.snapshot()['products'][0]['available'],3)

    def test_empty_cancelled_order_and_rollback(self):
        s=self.stock.shipment(self.body(entry_number='EMPTY'))
        s=self.stock.transition(s['id'],'cancel',self.body())
        before=self.stock.snapshot()
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_cancel_reopen BEFORE INSERT ON stock_events WHEN NEW.kind='cancel_reopen' BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(sqlite3.DatabaseError):self.reopen_cancelled(s)
        self.assertEqual(self.stock.snapshot(),before)
        with self.store.connect() as db:db.execute('DROP TRIGGER fail_cancel_reopen')
        opened=self.reopen_cancelled(s)
        self.assertEqual((opened['state'],opened['lines'],opened['reopen_count']),('draft',[],1))
        self.assertEqual(self.stock.snapshot()['units'],[])

    def test_parallel_new_owner_and_reopen_leave_stock_with_new_owner(self):
        s=self.cancelled();other=self.stock.shipment(self.body(entry_number='OTHER'))
        def act(kind):
            return self.reopen_cancelled(s) if kind=='reopen' else self.stock.scan(other['id'],self.body(barcode='0001'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            reopened,taken=list(pool.map(act,['reopen','scan']))
        self.assertEqual(reopened['lines'],[])
        unit=next(u for u in self.stock.snapshot()['units'] if u['id']==taken['unit_id'])
        self.assertEqual(unit['shipment_id'],other['id'])
        self.assertEqual(self.stock.snapshot()['products'][0]['available'],2)

    def test_wrong_expected_state_cannot_reopen_completed_order_as_empty(self):
        s=self.completed();before=self.stock.snapshot()
        with self.assertRaises(ConflictError):
            self.stock.transition(s['id'],'reopen',self.body(expected_state='cancelled',expected_reopen_count=0))
        self.assertEqual(self.stock.snapshot(),before)

    def test_event_identity_keeps_original_product_and_receipt_information(self):
        s=self.completed()
        snapshot=self.stock.snapshot()
        event=next(e for e in snapshot['events'] if e['kind']=='ship')
        original=event['details']['item_snapshot']
        self.assertEqual((original['name'],original['sku'],original['barcode'],original['receipt_number']),('合成盒','BOX','0001','IN-001'))
        self.assertEqual(original['cost_cents'],self.original[event['unit_id']]['cost_cents'])
        with self.store.connect() as db:
            db.execute("UPDATE products SET name='已改名稱' WHERE id=?",(self.product['id'],))
        saved=next(e for e in self.stock.snapshot()['events'] if e['id']==event['id'])
        self.assertEqual(saved['details']['item_snapshot'],original)

    def test_quality_audit_records_actual_owner_not_previous_order(self):
        r=self.receipt();self.stock.confirm_receipt(r['id'],self.body())
        a=self.stock.shipment(self.body(entry_number='A'))
        unit=self.stock.scan(a['id'],self.body(barcode='0001'))['unit_id']
        self.stock.quality(unit,self.body(quality='defective',packaging='good',reason='A檢查'))
        self.stock.quality(unit,self.body(quality='good',packaging='good',reason='無占用時檢查'))
        b=self.stock.shipment(self.body(entry_number='B'))
        self.assertEqual(self.stock.scan(b['id'],self.body(barcode='0001'))['unit_id'],unit)
        self.stock.quality(unit,self.body(quality='defective',packaging='good',reason='B檢查'))
        events={e['details']['reason']:e for e in self.stock.snapshot()['events'] if e['kind']=='quality'}
        self.assertEqual(events['A檢查']['shipment_id'],a['id'])
        self.assertIsNone(events['無占用時檢查']['shipment_id'])
        self.assertEqual(events['B檢查']['shipment_id'],b['id'])

