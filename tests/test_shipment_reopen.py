"""Reopening completion preserves original units and reverses accounting once."""
import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

import test_stock as fixtures
from ops.stock import Stock
from ops.store import ConflictError
from ops.stock_reports import report
from ops.shipping_statistics import shipping_statistics


class ReopenTests(unittest.TestCase):
    setUp=fixtures.StockTests.setUp
    receipt=fixtures.StockTests.receipt

    def body(self, **fields):
        return dict(operation_id=uuid4().hex,**fields)

    def completed(self):
        with patch('ops.stock.now',return_value='2026-01-01T00:00:00+00:00'):
            r=self.receipt();self.stock.confirm_receipt(r['id'],self.body())
        s=self.stock.shipment(self.body(entry_number='MAIN'))
        self.units=[self.stock.scan(s['id'],self.body(barcode='0001'))['unit_id'] for _ in range(2)]
        self.original={u['id']:u for u in self.stock.snapshot()['units']}
        with patch('ops.stock.now',return_value='2026-01-02T00:00:00+00:00'):
            return self.stock.transition(s['id'],'complete',self.body())

    def reopen(self,s,**fields):
        with patch('ops.stock.now',return_value='2026-02-01T00:00:00+00:00'):
            return self.stock.transition(s['id'],'reopen',self.body(expected_shipped_at=s['shipped_at'],expected_reopen_count=s.get('reopen_count',0),**fields))

    def metrics(self,as_of='2026-03-01T00:00:00+00:00',**scope):
        return shipping_statistics(self.store,start='2026-01-01',end='2026-02-28',as_of=as_of,**scope)['totals']

    def test_reopen_then_cancel_restores_same_units_and_cost_only_once(self):
        s=self.completed();opened=self.reopen(s)
        self.assertEqual((opened['state'],opened['shipped_at'],opened['reopen_count']),('allocated',None,1))
        snapshot=self.stock.snapshot();p=snapshot['products'][0]
        self.assertEqual((p['on_hand'],p['allocated'],p['available'],p['stock_cost_cents']),(3,2,1,31000))
        for u in snapshot['units']:
            for field in ('cost_cents','receipt_id','first_received_at','fifo_at'):
                self.assertEqual(u[field],self.original[u['id']][field])
        self.assertEqual({u['id'] for u in snapshot['units'] if u['shipment_id']==s['id']},set(self.units))
        body=self.body(expected_reopen_count=1)
        cancelled=self.stock.transition(s['id'],'cancel',body)
        self.assertEqual(self.stock.transition(s['id'],'cancel',body),cancelled)
        self.stock.transition(s['id'],'cancel',self.body(expected_reopen_count=1))
        p=self.stock.snapshot()['products'][0]
        self.assertEqual((p['on_hand'],p['allocated'],p['available'],p['stock_cost_cents']),(3,0,3,31000))
        self.assertEqual(self.metrics()['shipped'],0);self.assertEqual(self.metrics()['returned'],0)
        self.assertEqual(self.metrics()['shipped_cost'],'0.00')

    def test_recomplete_counts_only_latest_completion_and_preserves_as_of(self):
        s=self.completed();self.reopen(s)
        before=self.metrics(as_of='2026-01-31T00:00:00+00:00')
        self.assertEqual(before['shipped'],2)
        with patch('ops.stock.now',return_value='2026-02-02T00:00:00+00:00'):
            done=self.stock.transition(s['id'],'complete',self.body(expected_reopen_count=1))
        self.assertEqual(done['state'],'shipped')
        current=self.metrics();self.assertEqual(current['shipped'],2)
        self.assertEqual(current['shipped_cost'],before['shipped_cost'])
        self.assertEqual((current['returned'],current['shipping_orders'],current['shipment_count']),(0,1,1))
        january=shipping_statistics(self.store,start='2026-01-01',end='2026-01-31',as_of='2026-03-01T00:00:00+00:00')
        self.assertEqual(january['totals']['shipped'],0)
        snapshots=[('2026-01-31T00:00:00+00:00',1,0,1,2),('2026-02-01T12:00:00+00:00',3,2,1,0),('2026-03-01T00:00:00+00:00',1,0,1,2)]
        for at,on_hand,allocated,available,shipped in snapshots:
            result=report(self.store,start='2026-01-01',end='2026-02-28',as_of=at)
            p=result['inventory'][0]
            self.assertEqual((p['on_hand'],p['allocated'],p['available'],result['totals']['shipped']),(on_hand,allocated,available,shipped))
        events=self.stock.snapshot()['events']
        self.assertEqual(sum(e['kind']=='ship' for e in events),4,'historical ship events are retained')
        self.assertEqual(sum(e['kind']=='ship_reopen' for e in events),2)

    def test_original_units_remain_reserved_and_main_child_are_isolated(self):
        s=self.completed()
        child=self.stock.shipment(self.body(parent_id=s['id'],number='CHILD'))
        child_unit=self.stock.scan(child['id'],self.body(barcode='0001'))['unit_id']
        self.stock.transition(child['id'],'complete',self.body())
        self.reopen(s)
        other=self.stock.shipment(self.body(entry_number='OTHER'))
        with self.assertRaises(ConflictError):self.stock.scan(other['id'],self.body(barcode='0001'))
        self.stock.transition(s['id'],'cancel',self.body(expected_reopen_count=1))
        next_unit=self.stock.scan(other['id'],self.body(barcode='0001'))['unit_id']
        self.assertEqual(next_unit,min(self.units,key=lambda key:(self.original[key]['fifo_at'],key)))
        current=next(u for u in self.stock.snapshot()['units'] if u['id']==child_unit)
        self.assertEqual(current['location'],'customer')

    def test_partial_return_and_reship_block_whole_reopen(self):
        s=self.completed()
        returned=self.stock.receive_return(self.units[0],self.body(shipment_id=s['id'],reason='實退'))
        before=self.stock.snapshot()
        with self.assertRaises(ConflictError):self.reopen(s)
        self.assertEqual(self.stock.snapshot(),before)
        ship_event=next(e['id'] for e in before['events'] if e['kind']=='ship' and e['unit_id']==self.units[0])
        self.stock.release_return(self.units[0],self.body(ship_event_id=ship_event,reason='驗收良品',inspection_passed=True))
        other=self.stock.shipment(self.body(entry_number='OTHER'))
        # Reserve every available piece, including the returned original.
        for _ in range(2):self.stock.scan(other['id'],self.body(barcode='0001'))
        self.stock.transition(other['id'],'complete',self.body())
        with self.assertRaises(ConflictError):self.reopen(s)
        self.assertEqual(next(u for u in self.stock.snapshot()['units'] if u['id']==returned['id'])['location'],'customer')

    def test_retries_stale_cycles_and_concurrent_reopen(self):
        s=self.completed();body=self.body(expected_shipped_at=s['shipped_at'],expected_reopen_count=0)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.stock.transition(s['id'],'reopen',body),range(2)))
        self.assertEqual(results[0],results[1])
        self.assertEqual(sum(e['kind']=='ship_reopen' for e in self.stock.snapshot()['events']),2)
        for action in ('complete','cancel'):
            with self.assertRaises(ConflictError):self.stock.transition(s['id'],action,self.body(expected_reopen_count=0))
        with self.assertRaises(ConflictError):self.stock.transition(s['id'],'complete',self.body())
        done=self.stock.transition(s['id'],'complete',self.body(expected_reopen_count=1))
        with self.assertRaises(ConflictError):self.stock.transition(s['id'],'reopen',dict(body,operation_id='stale-new-id'))
        with self.assertRaises(ConflictError):self.stock.transition(s['id'],'cancel',self.body(expected_reopen_count=1))
        self.assertEqual(done['state'],'shipped')

    def test_reopen_rollback_and_old_return_cannot_move_newly_shipped_unit(self):
        s=self.completed();before=self.stock.snapshot()
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_reopen BEFORE INSERT ON stock_events WHEN NEW.kind='ship_reopen' BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.DatabaseError):self.reopen(s)
        self.assertEqual(before,self.stock.snapshot())
        with self.store.connect() as db:db.execute('DROP TRIGGER fail_reopen')
        self.reopen(s);self.stock.transition(s['id'],'cancel',self.body(expected_reopen_count=1))
        other=self.stock.shipment(self.body(entry_number='OTHER'))
        for _ in range(3):self.stock.scan(other['id'],self.body(barcode='0001'))
        self.stock.transition(other['id'],'complete',self.body())
        with self.assertRaises((ValueError,ConflictError)):
            self.stock.receive_return(self.units[0],self.body(shipment_id=s['id'],reason='舊單不可退他單商品'))
        self.assertEqual(next(u for u in self.stock.snapshot()['units'] if u['id']==self.units[0])['location'],'customer')

    def test_multiple_cycles_and_defects_never_create_saleable_stock(self):
        s=self.completed();self.reopen(s)
        with patch('ops.stock.now',return_value='2026-02-02T00:00:00+00:00'):
            done=self.stock.transition(s['id'],'complete',self.body(expected_reopen_count=1))
        with patch('ops.stock.now',return_value='2026-02-03T00:00:00+00:00'):
            opened=self.stock.transition(s['id'],'reopen',self.body(expected_shipped_at=done['shipped_at'],expected_reopen_count=1))
        self.assertEqual(opened['reopen_count'],2)
        self.assertEqual(self.metrics()['shipped'],0)
        self.stock.quality(self.units[0],self.body(quality='defective',packaging='good',reason='檢查瑕疵'))
        self.stock.transition(s['id'],'cancel',self.body(expected_reopen_count=2))
        p=self.stock.snapshot()['products'][0]
        self.assertEqual((p['on_hand'],p['allocated'],p['available'],p['defective'],p['stock_cost_cents']),(3,0,2,1,31000))

    def test_http_reopen_conflict_retry_and_cancel(self):
        from http.client import HTTPConnection
        from threading import Thread
        from ops.server import create_server
        s=self.completed();server=create_server(self.store.path,0)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        def post(action,body):
            c=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            c.request('POST','/api/shipments/'+s['id']+'/'+action,json.dumps(body),
                {'Content-Type':'application/json','X-Requested-With':'operations-ui'})
            response=c.getresponse();status=response.status;data=json.loads(response.read());c.close()
            return status,data
        try:
            self.assertEqual(post('cancel',self.body())[0],409)
            body=self.body(expected_shipped_at=s['shipped_at'],expected_reopen_count=0)
            status,opened=post('reopen',body);self.assertEqual(status,200)
            self.assertEqual(opened['state'],'allocated')
            self.assertEqual(post('reopen',body),(status,opened))
            self.assertEqual(post('complete',self.body(expected_reopen_count=0))[0],409)
            self.assertEqual(post('cancel',self.body(expected_reopen_count=1))[0],200)
            self.assertEqual(self.stock.snapshot()['products'][0]['available'],3)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_concurrent_completion_and_cancellation_commit_only_one_outcome(self):
        s=self.completed();self.reopen(s)
        def run(action):
            try:return self.stock.transition(s['id'],action,self.body(expected_reopen_count=1))['state']
            except ConflictError:return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(run,['complete','cancel']))
        self.assertEqual(outcomes.count('conflict'),1)
        snapshot=self.stock.snapshot();state=snapshot['shipments'][0]['state'];p=snapshot['products'][0]
        self.assertEqual((p['on_hand'],p['available'],p['allocated']),
                         (1,1,0) if state=='shipped' else (3,3,0))
        self.assertEqual(len(snapshot['units']),3)

