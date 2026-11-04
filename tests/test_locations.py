import tempfile
import unittest
from pathlib import Path
from ops.store import Store, ConflictError
from ops.catalogue import Catalogue
from ops.stock import Stock
from ops.locations import Locations


class LocationTests(unittest.TestCase):
    def test_allocation_pick_deduct_ship_recheck_location(self):
        out=self.stock.shipment(dict(operation_id='allocate-out',entry_number='ALLOCATE'))
        self.stock.update_shipment_lines(out['id'],dict(operation_id='lines',expected_lines=[],lines=[dict(product_id=self.product['id'],quantity=1)]))
        with self.assertRaises(ConflictError):
            self.stock.transition(out['id'],'allocate',dict(operation_id='unplaced-allocate'))
        self.assign(self.normal)
        self.stock.transition(out['id'],'allocate',dict(operation_id='valid-allocate'))
        with self.store.connect() as db:
            db.execute('UPDATE storage_places SET can_export=0 WHERE id=?',(self.normal['id'],))
        for action in ('pick','deduct','ship'):
            with self.subTest(action=action),self.assertRaises(ConflictError):
                self.stock.transition(out['id'],action,dict(operation_id='blocked-'+action))
        with self.store.connect() as db:
            db.execute('UPDATE storage_places SET can_export=1 WHERE id=?',(self.normal['id'],))
        self.stock.transition(out['id'],'pick',dict(operation_id='valid-pick'))
        self.stock.transition(out['id'],'deduct',dict(operation_id='valid-deduct'))
        with self.store.connect() as db:
            db.execute("UPDATE storage_places SET entry_state='boxDamaged' WHERE id=?",(self.normal['id'],))
        with self.assertRaises(ConflictError):
            self.stock.transition(out['id'],'ship',dict(operation_id='staged-mismatch'))
        with self.store.connect() as db:
            db.execute("UPDATE storage_places SET entry_state='import' WHERE id=?",(self.normal['id'],))
        self.stock.transition(out['id'],'ship',dict(operation_id='valid-ship'))

    def test_legacy_disabled_flag_cannot_bypass_placement(self):
        with self.store.connect() as db:
            db.execute('UPDATE storage_config SET enabled=0')
        self.assertEqual(self.stock.snapshot()['products'][0]['available'],0)
        out=self.stock.shipment(dict(operation_id='legacy-out',entry_number='LEGACY'))
        with self.assertRaises(ConflictError):
            self.stock.scan(out['id'],dict(operation_id='legacy-scan',barcode='LOC'))
        self.assertTrue(self.places.snapshot()['enabled'])

    def test_all_place_states_and_mismatched_unit_states(self):
        from ops.locations import ENTRY_STATES, eligible, LOCATION_SQL
        uid=self.stock.snapshot()['units'][0]['id']
        for state,(quality,packaging) in ENTRY_STATES.items():
            p=self.place('matrix-'+state,state)
            for actual_quality,actual_packaging in ENTRY_STATES.values():
                with self.subTest(place=state,quality=actual_quality,packaging=actual_packaging):
                    with self.store.connect() as db:
                        db.execute('UPDATE stock_units SET place_id=?,quality=?,packaging=? WHERE id=?',(p['id'],actual_quality,actual_packaging,uid))
                        u=db.execute('SELECT * FROM stock_units WHERE id=?',(uid,)).fetchone()
                        expected=quality=='good' and (quality,packaging)==(actual_quality,actual_packaging)
                        self.assertEqual(eligible(db,u),expected)
                        self.assertEqual(bool(db.execute('SELECT 1 FROM stock_units u WHERE u.id=? '+LOCATION_SQL,(uid,)).fetchone()),expected)
                    snap=self.stock.snapshot()
                    self.assertEqual(next(u['place_eligible'] for u in snap['units'] if u['id']==uid),expected)

    def test_state_mismatch_blocks_scan_and_final_shipping(self):
        self.assign(self.normal)
        with self.store.connect() as db:
            db.execute("UPDATE stock_units SET packaging='damaged'")
        out=self.stock.shipment(dict(operation_id='mismatch-out',entry_number='MISMATCH'))
        with self.assertRaises(ConflictError):
            self.stock.scan(out['id'],dict(operation_id='mismatch-scan',barcode='#LOC'))
        with self.store.connect() as db:
            db.execute("UPDATE stock_units SET packaging='good'")
        scan=self.stock.scan(out['id'],dict(operation_id='valid-scan',barcode='LOC'))
        with self.store.connect() as db:
            db.execute("UPDATE stock_units SET packaging='damaged' WHERE id=?",(scan['unit_id'],))
        with self.assertRaises(ConflictError):
            self.stock.transition(out['id'],'complete',dict(operation_id='mismatch-ship'))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT location FROM stock_units WHERE id=?',(scan['unit_id'],)).fetchone()[0],'warehouse')

    def test_thousand_units_transfer_preserves_identity_cost_and_logs(self):
        r=self.stock.receipt(dict(operation_id='bulk-r',number='BULK',lines=[dict(product_id=self.product['id'],quantity=1000,unit_price='25')]))
        self.stock.confirm_receipt(r['id'],dict(operation_id='bulk-confirm'))
        self.places.assign(dict(operation_id='bulk-assign',receipt_id=r['id'],quantity=1000,place_id=self.normal['id']))
        units=[u for u in self.stock.snapshot()['units'] if u['receipt_id']==r['id']]
        t=self.places.transfer(dict(operation_id='bulk-transfer',number='BULK-T',from_place_id=self.normal['id'],to_place_id=self.box['id'],unit_ids=[u['id'] for u in units]))
        self.places.transition(t['id'],'start',dict(operation_id='bulk-start'))
        self.places.transition(t['id'],'complete',dict(operation_id='bulk-complete'))
        snapshot=self.stock.snapshot();moved=[u for u in snapshot['units'] if u['receipt_id']==r['id']]
        self.assertEqual(len(moved),1000)
        self.assertTrue(all(u['place_id']==self.box['id'] and u['packaging']=='damaged' for u in moved))
        self.assertEqual([(u['id'],u['cost_cents'],u['fifo_at']) for u in units],[(u['id'],u['cost_cents'],u['fifo_at']) for u in moved])
        self.assertEqual(sum(e['kind']=='transfer_complete' for e in snapshot['events']),1000)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'test.sqlite3')
        self.stock=Stock(self.store); self.places=Locations(self.store)
        self.product=Catalogue(self.store).create(dict(operation_id='p',name='櫃位測試',sku='LOC',barcode='LOC'))
        self.places.enable(dict(operation_id='enable'))
        self.normal=self.place('normal','import')
        self.box=self.place('box','boxDamaged')
        self.destroy=self.place('destroy','destroy')
        self.receipt=self.stock.receipt(dict(operation_id='r',number='R',lines=[dict(product_id=self.product['id'],quantity=6,unit_price='25')]))
        self.stock.confirm_receipt(self.receipt['id'],dict(operation_id='rc'))

    def place(self,name,state,export=True):
        return self.places.save(dict(operation_id=name,address='1F',position=name,name=name,can_import=True,can_export=export,can_return=True,entry_state=state))

    def assign(self,place,count=6):
        return self.places.assign(dict(operation_id='assign-'+place['id'],receipt_id=self.receipt['id'],product_id=self.product['id'],quantity=count,place_id=place['id']))

    def test_unassigned_box_and_damaged_flow(self):
        self.assertEqual(self.stock.snapshot()['products'][0]['available'],0)
        out=self.stock.shipment(dict(operation_id='out',entry_number='OUT'))
        with self.assertRaises(ConflictError):
            self.stock.scan(out['id'],dict(operation_id='blocked',barcode='LOC'))
        self.assign(self.normal,3);self.assign(self.box,2);self.assign(self.destroy,1)
        p=self.stock.snapshot()['products'][0]
        self.assertEqual((p['on_hand'],p['available'],p['box_available']),(6,3,2))
        a=self.stock.scan(out['id'],dict(operation_id='normal-scan',barcode='LOC'))
        b=self.stock.scan(out['id'],dict(operation_id='box-scan',barcode='#LOC'))
        snap=self.stock.snapshot(); units={u['id']:u for u in snap['units']}
        self.assertEqual(units[a['unit_id']]['packaging'],'good')
        self.assertEqual(units[b['unit_id']]['packaging'],'damaged')
        self.stock.transition(out['id'],'complete',dict(operation_id='ship'))
        self.assertEqual(self.stock.snapshot()['products'][0]['on_hand'],4)

    def test_transfer_cancel_complete_and_delete_guards(self):
        self.assign(self.normal)
        ids=[u['id'] for u in self.stock.snapshot()['units']][:2]
        transfer=self.places.transfer(dict(operation_id='t',number='T',from_place_id=self.normal['id'],to_place_id=self.box['id'],unit_ids=ids))
        self.places.transition(transfer['id'],'start',dict(operation_id='start'))
        self.assertEqual(self.stock.snapshot()['products'][0]['available'],4)
        with self.assertRaises(ConflictError): self.places.delete(self.normal['id'],dict(operation_id='delete'))
        self.places.transition(transfer['id'],'cancel',dict(operation_id='cancel'))
        self.assertEqual(self.stock.snapshot()['products'][0]['available'],6)
        t=self.places.transfer(dict(operation_id='t2',number='T2',from_place_id=self.normal['id'],to_place_id=self.box['id'],unit_ids=ids))
        self.places.transition(t['id'],'start',dict(operation_id='start2'))
        self.places.transition(t['id'],'complete',dict(operation_id='complete2'))
        self.places.transition(t['id'],'complete',dict(operation_id='retry2'))
        p=self.stock.snapshot()['products'][0]
        self.assertEqual((p['on_hand'],p['available'],p['box_available']),(6,4,2))
        self.assertTrue(all(u['cost_cents']==2500 for u in self.stock.snapshot()['units']))

    def test_non_export_place_and_duplicate(self):
        closed=self.place('closed','import',False)
        self.assign(closed)
        self.assertEqual(self.stock.snapshot()['products'][0]['available'],0)
        with self.assertRaises(ConflictError): self.place('closed','import')

    def test_unit_log_keeps_receipt_transfer_and_shipping_snapshots(self):
        self.assign(self.normal)
        uid=self.stock.snapshot()['units'][0]['id']
        t=self.places.transfer(dict(operation_id='log-t',number='LOG-T',from_place_id=self.normal['id'],to_place_id=self.box['id'],unit_ids=[uid]))
        self.places.transition(t['id'],'start',dict(operation_id='log-start'))
        self.places.transition(t['id'],'complete',dict(operation_id='log-complete'))
        out=self.stock.shipment(dict(operation_id='log-out',entry_number='LOG-OUT'))
        self.stock.scan(out['id'],dict(operation_id='log-scan',barcode='#LOC'))
        self.stock.transition(out['id'],'complete',dict(operation_id='log-ship'))
        events=[e for e in self.stock.snapshot()['events'] if e['unit_id']==uid]
        by_kind={e['kind']:e for e in events}
        self.assertTrue({'receipt','putaway','transfer_complete','ship'} <= by_kind.keys())
        moved=by_kind['transfer_complete']['details']
        self.assertEqual(moved['transfer_number'],'LOG-T')
        self.assertEqual(moved['from_place']['id'],self.normal['id'])
        self.assertEqual(moved['to_place']['id'],self.box['id'])
        self.assertEqual(by_kind['ship']['shipment_id'],out['id'])
        self.assertEqual(by_kind['putaway']['receipt_id'],self.receipt['id'])
        self.places.save({**self.box,'operation_id':'rename','expected_version':1,'name':'renamed', 'can_import':True,'can_export':True,'can_return':True})
        saved=next(e for e in self.stock.snapshot()['events'] if e['id']==by_kind['transfer_complete']['id'])
        self.assertEqual(saved['details']['to_place']['name'],'box')

    def test_return_location_rule_and_hold(self):
        self.assign(self.normal)
        out=self.stock.shipment(dict(operation_id='return-out',entry_number='RETURN-OUT'))
        scan=self.stock.scan(out['id'],dict(operation_id='return-scan',barcode='LOC'))
        self.stock.transition(out['id'],'complete',dict(operation_id='return-ship'))
        uid=scan['unit_id']
        event=next(e for e in self.stock.snapshot()['events'] if e['unit_id']==uid and e['kind']=='ship')
        self.stock.receive_return(uid,dict(operation_id='return-in',shipment_id=out['id'],place_id=self.destroy['id'],reason='test'))
        u=next(u for u in self.stock.snapshot()['units'] if u['id']==uid)
        self.assertEqual(u['quality'],'damaged');self.assertTrue(u['return_hold'])
        self.stock.restock_return(uid,dict(operation_id='return-restock',ship_event_id=event['id'],reason='test'))
        u=next(u for u in self.stock.snapshot()['units'] if u['id']==uid)
        self.assertEqual(u['quality'],'damaged');self.assertFalse(u['return_hold'])
