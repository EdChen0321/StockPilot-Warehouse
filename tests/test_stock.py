import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ops.store import Store, ConflictError
from ops.catalogue import Catalogue
from ops.stock import Stock
from ops.orders import Orders
from ops.stock_reports import report, save_alert


class StockTests(unittest.TestCase):
    def test_child_creation_after_parent_fulfilled_and_independent_cancel(self):
        self.stock.confirm_receipt(self.receipt()['id'], dict(operation_id='confirm'))
        source = self.receive_order(1)
        main = self.stock.shipment(dict(operation_id='main', number='MAIN', account_id=source['id'], order_id='ORDER-001'))
        self.stock.scan(main['id'], dict(operation_id='scan-main', barcode='0001'))
        self.stock.transition(main['id'], 'complete', dict(operation_id='complete-main'))
        child = self.stock.shipment(dict(operation_id='child', parent_id=main['id'], number='CHILD'))
        self.assertEqual(child['lines'], [])
        self.assertEqual(child['parent_id'], main['id'])
        # Creating a document never silently increases source-order quantities.
        with self.assertRaises(ConflictError):
            self.stock.scan(child['id'], dict(operation_id='over', barcode='0001'))
        complete = next(s for s in self.stock.snapshot()['shipments'] if s['id'] == main['id'])
        self.stock.transition(main['id'], 'reopen', dict(operation_id='reopen', expected_shipped_at=complete['shipped_at'], expected_reopen_count=0))
        self.stock.transition(main['id'], 'cancel', dict(operation_id='cancel', expected_reopen_count=1))
        self.stock.scan(child['id'], dict(operation_id='scan-child', barcode='0001'))
        self.stock.transition(child['id'], 'complete', dict(operation_id='complete-child'))
        states = {s['id']: s['state'] for s in self.stock.snapshot()['shipments']}
        self.assertEqual((states[main['id']], states[child['id']]), ('cancelled', 'shipped'))

    def test_preparing_parent_cancel_preserves_completed_child(self):
        self.stock.confirm_receipt(self.receipt()['id'], dict(operation_id='confirm'))
        main = self.stock.shipment(dict(operation_id='main', entry_number='MAIN'))
        main_unit = self.stock.scan(main['id'], dict(operation_id='scan-main', barcode='0001'))['unit_id']
        child = self.stock.shipment(dict(operation_id='child', parent_id=main['id'], number='CHILD'))
        child_unit = self.stock.scan(child['id'], dict(operation_id='scan-child', barcode='0001'))['unit_id']
        self.stock.transition(child['id'], 'complete', dict(operation_id='complete-child'))
        before = self.stock.snapshot()
        self.stock.transition(main['id'], 'cancel', dict(operation_id='cancel-main'))
        after = self.stock.snapshot()
        self.assertEqual(next(s for s in before['shipments'] if s['id']==child['id']), next(s for s in after['shipments'] if s['id']==child['id']))
        self.assertEqual(next(u for u in before['units'] if u['id']==child_unit), next(u for u in after['units'] if u['id']==child_unit))
        self.assertIsNone(next(u for u in after['units'] if u['id']==main_unit)['shipment_id'])
        self.assertEqual([e for e in before['events'] if e['shipment_id']==child['id']], [e for e in after['events'] if e['shipment_id']==child['id']])

    def test_explicit_child_barcode_and_one_step_completion(self):
        receipt=self.receipt(freight='0')
        self.stock.confirm_receipt(receipt['id'],dict(operation_id='confirm'))
        main=self.stock.shipment(dict(operation_id='main',entry_number='MAIN'))
        with self.assertRaises(ValueError):
            self.stock.shipment(dict(operation_id='missing',parent_id=main['id']))
        with self.assertRaises(ConflictError):
            self.stock.shipment(dict(operation_id='duplicate-main',parent_id=main['id'],number='MAIN'))
        child=self.stock.shipment(dict(operation_id='child',parent_id=main['id'],number='CHILD-EXACT'))
        self.assertEqual(child['number'],'CHILD-EXACT')
        self.assertEqual(child['group_id'],main['group_id'])
        with self.assertRaises(ConflictError):
            self.stock.shipment(dict(operation_id='duplicate-child',parent_id=main['id'],number='CHILD-EXACT'))
        with self.assertRaises(ConflictError):
            self.stock.transition(child['id'],'complete',dict(operation_id='empty'))
        unit=self.stock.scan(child['id'],dict(operation_id='scan',barcode='0001'))['unit_id']
        completed=self.stock.transition(child['id'],'complete',dict(operation_id='complete'))
        self.assertEqual(completed['state'],'shipped')
        self.assertTrue(completed['shipped_at'])
        self.assertEqual(self.stock.transition(child['id'],'complete',dict(operation_id='retry')),completed)
        snapshot=self.stock.snapshot()
        self.assertEqual(next(u for u in snapshot['units'] if u['id']==unit)['location'],'customer')
        self.assertEqual(sum(e['kind']=='ship' for e in snapshot['events']),1)
        self.assertEqual(snapshot['products'][0]['on_hand'],2)
        self.assertEqual(snapshot['products'][0]['staged'],0)
        self.assertEqual(self.store.current_orders(),[])

    def test_scanning_locks_unit_against_other_orders_until_cancelled(self):
        receipt = self.receipt(lines=[dict(product_id=self.product['id'], quantity=1, unit_price='24.00')])
        self.stock.confirm_receipt(receipt['id'], dict(operation_id='confirm-lock'))
        main = self.stock.shipment(dict(operation_id='lock-main', entry_number='LOCK-MAIN'))
        children = [self.stock.shipment(dict(operation_id=f'lock-child-{i}', parent_id=main['id'], number=f'LOCK-CHILD-{i}')) for i in range(2)]
        other = self.stock.shipment(dict(operation_id='lock-other', entry_number='LOCK-OTHER'))
        orders = [main, *children, other]
        self.assertEqual({order['group_id'] for order in orders[:3]}, {main['group_id']})
        self.assertTrue(all(child['parent_id'] == main['id'] for child in children))
        self.assertNotEqual(other['group_id'], main['group_id'])
        def scan(index):
            try:
                return index, self.stock.scan(orders[index]['id'], dict(operation_id=f'lock-scan-{index}', barcode='0001'))
            except ConflictError:
                return index, None
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(scan, range(4)))
        winner, result = next(x for x in results if x[1] is not None)
        loser = (winner+1) % len(orders)
        self.assertEqual(sum(x[1] is not None for x in results), 1)
        self.assertEqual(self.stock.snapshot()['products'][0]['available'], 0)
        with self.assertRaises(ConflictError):
            self.stock.cancel_item(orders[loser]['id'], result['unit_id'], dict(operation_id='wrong-owner'))
        self.stock.cancel_item(orders[winner]['id'], result['unit_id'], dict(operation_id='release-owner'))
        self.assertEqual(self.stock.snapshot()['products'][0]['available'], 1)
        claimed = self.stock.scan(orders[loser]['id'], dict(operation_id='scan-after-release', barcode='0001'))
        self.assertEqual(claimed['unit_id'], result['unit_id'])

    def test_shipping_statistics_channels_costs_returns_and_order_grain(self):
        from ops.shipping_statistics import shipping_statistics
        from datetime import datetime, timezone, timedelta
        today=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        r=self.receipt(freight='0')
        self.stock.confirm_receipt(r['id'],dict(operation_id='rc'))
        main=self.stock.shipment(dict(operation_id='main',entry_number='STATS',channel='合成通路A'))
        child=self.stock.shipment(dict(operation_id='child',parent_id=main['id'],number='STATS-CHILD',channel='合成通路B'))
        units=[]
        for i,s in enumerate((main,child)):
            units.append(self.stock.scan(s['id'],dict(operation_id=f'scan{i}',barcode='0001'))['unit_id'])
            self.stock.transition(s['id'],'ship',dict(operation_id=f'ship{i}'))
        self.stock.receive_return(units[0],dict(operation_id='return',shipment_id=main['id'],reason='合成統計退貨'))
        result=shipping_statistics(self.store,start=today,end=today)
        from ops.ai_queries import query_shipping
        ai_result=query_shipping(self.store.path,{'start':today,'end':today})
        self.assertEqual(ai_result['totals'],result['totals'])
        self.assertEqual((ai_result['totals']['shipping_orders'],ai_result['totals']['shipment_count']), (1,2))
        self.assertEqual(ai_result['totals']['shipped_cost'],'200.00')
        self.assertEqual((result['totals']['shipping_orders'],result['totals']['shipment_count']), (1,2))
        self.assertEqual((result['totals']['shipped'],result['totals']['returned'],result['totals']['net_shipped']), (2,1,1))
        self.assertEqual((result['totals']['shipped_cost'],result['totals']['returned_cost'],result['totals']['net_cost']), ('200.00','100.00','100.00'))
        self.assertEqual(len(result['channels']),2)
        self.assertEqual(result['shipping_products'][0]['shipping_orders'],1)
        filtered=shipping_statistics(self.store,start=today,end=today,channel='合成通路B',product_id=self.product['id'])
        self.assertEqual((filtered['totals']['shipped'],filtered['totals']['returned']),(1,0))
        self.assertEqual(filtered['shipping_details'][0]['shipment_id'],child['id'])
        self.assertEqual(shipping_statistics(self.store,start=today,end=today,channel='不存在')['totals']['shipped'],0)
        with self.assertRaises(ValueError): shipping_statistics(self.store,start='2026-02-30')
        with self.assertRaises(KeyError): shipping_statistics(self.store,product_id='missing')
        with self.store.connect() as db:
            db.execute("UPDATE stock_events SET time='2026-01-31T15:59:59+00:00' WHERE kind='ship'")
            db.execute("UPDATE stock_events SET time='2026-01-31T16:00:00+00:00' WHERE kind='return'")
        returned=shipping_statistics(self.store,start='2026-02-01',end='2026-02-01',as_of='2026-02-02T00:00:00+00:00')
        self.assertEqual((returned['totals']['shipped'],returned['totals']['returned'],returned['totals']['net_cost']),(0,1,'-100.00'))
        self.assertEqual(returned['shipping_daily'][0]['day'],'2026-02-01')
        before=shipping_statistics(self.store,start='2026-01-31',end='2026-02-01',as_of='2026-01-31T15:59:59+00:00')
        self.assertEqual((before['totals']['shipped'],before['totals']['returned']),(2,0))

    def test_scan_fifty_units_fifo_batch_cost_and_cancel(self):
        for i, price in enumerate(('10.00', '20.00')):
            receipt = self.receipt(operation_id=f'r{i}', number=f'BATCH-{i}', source=f'SOURCE-{i}', freight='0',
                lines=[dict(product_id=self.product['id'], quantity=25, unit_price=price)])
            self.stock.confirm_receipt(receipt['id'], dict(operation_id=f'rc{i}'))
        expected = self.stock.snapshot()['units']
        main = self.stock.shipment(dict(operation_id='main', entry_number='SCAN-50'))
        for i in range(50):
            body = dict(operation_id=f'scan{i}', barcode='0001')
            result = self.stock.scan(main['id'], body)
            self.assertEqual(result['unit_id'], expected[i]['id'])
            self.assertEqual(self.stock.scan(main['id'], body), result)
        with self.assertRaises(ConflictError):
            self.stock.scan(main['id'], dict(operation_id='overflow', barcode='0001'))
        self.assertEqual(self.stock.snapshot()['products'][0]['allocated'], 50)
        self.stock.cancel_item(main['id'], expected[0]['id'], dict(operation_id='cancel'))
        child = self.stock.shipment(dict(operation_id='child', parent_id=main['id'], number='SCAN-CHILD'))
        self.assertEqual(self.stock.scan(child['id'], dict(operation_id='childscan', barcode='0001'))['unit_id'], expected[0]['id'])
        for s in (main, child):
            self.stock.transition(s['id'], 'ship', dict(operation_id='ship'+s['id']))
        snap = Stock(Store(self.store.path)).snapshot()
        self.assertEqual([u['cost_cents'] for u in snap['units']], [1000]*25+[2000]*25)
        self.assertEqual(len([e for e in snap['events'] if e['kind']=='ship']), 50)
        self.assertEqual(self.store.current_orders(), [])

    def test_scan_quality_return_preserves_single_unit_history(self):
        receipt = self.receipt()
        self.stock.confirm_receipt(receipt['id'], dict(operation_id='rc'))
        main = self.stock.shipment(dict(operation_id='main', entry_number='SCAN-RETURN'))
        units = [self.stock.scan(main['id'], dict(operation_id=f'scan{i}', barcode='0001'))['unit_id'] for i in range(2)]
        self.stock.quality(units[0], dict(operation_id='defect', quality='defective', packaging='good'))
        self.assertTrue(any(e['kind']=='quality' and e['details']['reason']=='標記瑕疵' for e in self.stock.snapshot()['events']))
        self.assertEqual(self.stock.snapshot()['products'][0]['allocated'], 1)
        self.stock.transition(main['id'], 'ship', dict(operation_id='ship'))
        before = next(u for u in self.stock.snapshot()['units'] if u['id']==units[1])
        returned = self.stock.receive_return(units[1], dict(operation_id='return', shipment_id=main['id'], reason='合成退貨', quality='defective'))
        self.assertEqual(returned['cost_cents'], before['cost_cents'])
        self.assertEqual(returned['receipt_id'], before['receipt_id'])
        snap = self.stock.snapshot()
        self.assertTrue(any(e['kind']=='ship' and e['unit_id']==units[1] for e in snap['events']))
        self.assertTrue(any(e['kind']=='return' and e['details']['reason']=='合成退貨' for e in snap['events']))
        self.assertEqual(snap['products'][0]['available'], 1)

    def test_direct_deduct_atomically_allocates_fifo_and_retries(self):
        receipt=self.receipt()
        self.stock.confirm_receipt(receipt['id'],dict(operation_id='rc'))
        shipment=self.stock.shipment(dict(operation_id='new',entry_number='AUTO-FIFO'))
        self.stock.update_shipment_lines(shipment['id'],dict(operation_id='lines',expected_lines=[],lines=[dict(product_id=self.product['id'],quantity=2)]))
        fifo=[u['id'] for u in self.stock.snapshot()['units']][:2]
        result=self.stock.transition(shipment['id'],'deduct',dict(operation_id='deduct'))
        self.assertEqual(result['state'],'staged')
        self.assertEqual(self.stock.transition(shipment['id'],'deduct',dict(operation_id='deduct')),result)
        self.assertEqual(self.stock.transition(shipment['id'],'deduct',dict(operation_id='deduct-again')),result)
        snap=self.stock.snapshot()
        self.assertEqual([u['id'] for u in snap['units'] if u['location']=='staging'],fifo)
        self.assertEqual(len([e for e in snap['events'] if e['kind']=='deduct']),2)
        self.assertEqual(snap['products'][0]['available'],1)
        self.assertEqual(snap['products'][0]['on_hand'],3)
        other=self.stock.shipment(dict(operation_id='other',entry_number='SHORTAGE'))
        self.stock.update_shipment_lines(other['id'],dict(operation_id='otherlines',expected_lines=[],lines=[dict(product_id=self.product['id'],quantity=2)]))
        with self.assertRaises(ConflictError):
            self.stock.transition(other['id'],'deduct',dict(operation_id='short'))
        snap=self.stock.snapshot()
        self.assertFalse(any(u['shipment_id']==other['id'] for u in snap['units']))
        self.assertEqual(next(s for s in snap['shipments'] if s['id']==other['id'])['state'],'draft')

    def test_manual_dispatch_entry_opens_existing_and_blank_can_ship(self):
        body=dict(operation_id='open',entry_number='20261028009999',channel='Synthetic',comment='manual')
        main=self.stock.shipment(body)
        self.assertEqual(main['lines'],[])
        self.assertEqual(self.stock.shipment(dict(body,operation_id='again',comment='do not overwrite')),main)
        self.assertEqual(self.store.current_orders(),[])
        with self.assertRaises(ConflictError):
            self.stock.transition(main['id'],'allocate',dict(operation_id='empty'))
        r=self.receipt()
        self.stock.confirm_receipt(r['id'],dict(operation_id='rc'))
        saved=self.stock.update_shipment_lines(main['id'],dict(operation_id='lines',expected_lines=[],lines=[dict(product_id=self.product['id'],quantity=2)]))
        self.stock.transition(main['id'],'allocate',dict(operation_id='alloc'))
        self.stock.transition(main['id'],'ship',dict(operation_id='ship'))
        child=self.stock.shipment(dict(operation_id='sub',parent_id=main['id'],number='MANUAL-CHILD'))
        self.assertEqual(child['parent_id'],main['id'])
        self.assertEqual(child['lines'],[])
        self.stock.update_shipment_lines(child['id'],dict(operation_id='childlines',expected_lines=[],lines=[dict(product_id=self.product['id'],quantity=1)]))
        self.stock.transition(child['id'],'allocate',dict(operation_id='childalloc'))
        self.stock.transition(child['id'],'ship',dict(operation_id='childship'))
        self.assertEqual(self.stock.shipment(dict(body,operation_id='aftership'))['id'],main['id'])
        self.assertEqual(self.store.current_orders(),[])
        from datetime import datetime, timezone, timedelta
        today=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        stats=report(self.store,start=today,end=today)
        self.assertEqual(stats['totals']['shipping_orders'],1)
        self.assertEqual(stats['totals']['shipment_count'],2)
        self.assertEqual(stats['inventory'][0]['available'],0)

    def test_draft_shipment_goods_edit_is_audited_and_stops_after_allocation(self):
        receipt=self.receipt()
        self.stock.confirm_receipt(receipt['id'],dict(operation_id='confirm'))
        source=self.receive_order()
        shipment=self.stock.shipment(dict(operation_id='shipment',number='GOODS',account_id=source['id'],order_id='ORDER-001'))
        body=dict(operation_id='goods',expected_lines=shipment['lines'],lines=[dict(product_id=self.product['id'],quantity=1)])
        saved=self.stock.update_shipment_lines(shipment['id'],body)
        self.assertEqual(saved['lines'][0]['quantity'],1)
        self.assertEqual(self.stock.update_shipment_lines(shipment['id'],body),saved)
        self.assertEqual(self.stock.snapshot()['products'][0]['available'],3)
        self.assertEqual(report(self.store)['inventory'][0]['available'],3)
        with self.assertRaises(ConflictError):
            self.stock.update_shipment_lines(shipment['id'],dict(body,operation_id='stale'))
        with self.assertRaises(ValueError):
            self.stock.update_shipment_lines(shipment['id'],dict(body,operation_id='empty',expected_lines=saved['lines'],lines=[]))
        with self.assertRaises(ValueError):
            self.stock.update_shipment_lines(shipment['id'],dict(body,operation_id='over',expected_lines=saved['lines'],lines=[dict(product_id=self.product['id'],quantity=4)]))
        self.stock.transition(shipment['id'],'allocate',dict(operation_id='allocate'))
        with self.assertRaises(ConflictError):
            self.stock.update_shipment_lines(shipment['id'],dict(body,operation_id='allocated',expected_lines=saved['lines']))
        snap=Stock(Store(self.store.path)).snapshot()
        self.assertEqual(len([e for e in snap['events'] if e['kind']=='shipment_lines']),1)
        self.assertEqual(snap['shipments'][0]['lines'],saved['lines'])

    def test_shipment_channel_comment_persist_without_inventory_effect(self):
        source = self.receive_order()
        body = dict(operation_id='metadata', number='OUT-META', account_id=source['id'],
                    order_id='ORDER-001', channel='合成門市', comment='分兩次寄送',
                    lines=[dict(product_id=self.product['id'], quantity=1)])
        saved = self.stock.shipment(body)
        self.assertEqual(saved['channel'], '合成門市')
        self.assertEqual(saved['comment'], '分兩次寄送')
        self.assertEqual(self.stock.shipment(body), saved)
        reopened = Stock(Store(self.store.path)).snapshot()
        self.assertEqual(reopened['shipments'][0], {**saved, 'first_completed_at': None, 'cancelled_at': None})
        self.assertEqual(reopened['units'], [])
        child = self.stock.shipment(dict(body, operation_id='child', number='OUT-CHILD', channel='', comment=''))
        self.assertEqual(child['parent_id'], saved['id'])
        self.assertEqual(child['channel'], '')
        with self.assertRaises(ValueError):
            self.stock.shipment(dict(body, operation_id='bad', number='BAD', comment=['invalid']))
        self.assertEqual(len(self.stock.snapshot()['shipments']), 2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'db.sqlite3')
        self.catalogue = Catalogue(self.store)
        self.product = self.catalogue.create(dict(operation_id='p1', name='合成盒', sku='BOX', barcode='0001'))
        self.stock = Stock(self.store)

    def receipt(self, **changes):
        data = dict(operation_id='receipt', number='IN-001', lines=[dict(product_id=self.product['id'], quantity=3, unit_price='100.00')], freight='10.00', allocation='quantity')
        data.update(changes)
        return self.stock.receipt(data)

    def test_original_order_cancel_releases_picked_preserves_shipped_and_quality(self):
        draft = self.receipt()
        self.stock.confirm_receipt(draft['id'], dict(operation_id='confirm'))
        source = self.receive_order()
        shipments = []
        for index in range(3):
            s = self.stock.shipment(dict(operation_id=f's{index}', number=f'S{index}', account_id=source['id'], order_id='ORDER-001',
                lines=[dict(product_id=self.product['id'], quantity=1)]))
            self.stock.transition(s['id'], 'allocate', dict(operation_id=f'a{index}'))
            shipments.append(s)
        self.stock.transition(shipments[0]['id'], 'ship', dict(operation_id='ship'))
        self.stock.transition(shipments[1]['id'], 'pick', dict(operation_id='pick'))
        picked = next(u for u in self.stock.snapshot()['units'] if u['shipment_id'] == shipments[1]['id'])
        self.stock.quality(picked['id'], dict(operation_id='damage', quality='damaged', packaging='good', reason='合成驗證'))
        body = dict(operation_id='cancel-order', account_id=source['id'], order_id='ORDER-001', expected_revision=1)
        result = Orders(self.store).cancel(body)
        self.assertEqual(Orders(self.store).cancel(body), result)
        Orders(self.store).cancel(dict(body, operation_id='cancel-again'))
        snapshot = Stock(Store(self.store.path)).snapshot()
        self.assertEqual(sorted(s['state'] for s in snapshot['shipments']), ['cancelled', 'cancelled', 'shipped'])
        self.assertFalse(any(u['shipment_id'] for u in snapshot['units']))
        self.assertEqual(next(u for u in snapshot['units'] if u['id'] == picked['id'])['quality'], 'damaged')
        self.assertEqual(snapshot['products'][0]['available'], 1)
        self.assertEqual(report(self.store)['inventory'][0]['available'], 1)
        self.assertEqual(len([e for e in snapshot['events'] if e['kind'] == 'release']), 2)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM order_versions').fetchone()[0], 2)

    def test_direct_sku_import_and_original_cancel(self):
        from test_orders import row, as_csv
        source = self.catalogue.account(dict(operation_id='source', name='合成門市', platform='manual'))
        def upload(**changes):
            batch = self.store.preview(source['id'], 'test.csv', as_csv([row(external_sku='BOX', **changes)]), True)
            return self.store.confirm(batch['id'])
        self.assertEqual(upload()['results'][0]['outcome'], 'applied')
        self.stock.confirm_receipt(self.receipt()['id'], dict(operation_id='confirm'))
        shipment = self.stock.shipment(dict(operation_id='s', number='OUT', account_id=source['id'], order_id='O-001'))
        self.assertEqual(shipment['lines'][0]['quantity'], 2)
        self.stock.transition(shipment['id'], 'allocate', dict(operation_id='allocate'))
        self.stock.transition(shipment['id'], 'pick', dict(operation_id='pick'))
        upload(source_revision='2', raw_status='cancelled')
        upload(source_revision='2', raw_status='cancelled')
        self.assertEqual(self.stock.snapshot()['products'][0]['available'], 3)
        self.assertEqual(self.stock.snapshot()['shipments'][0]['state'], 'cancelled')

    def test_shipment_empty_and_fulfilled_business_errors(self):
        source = self.receive_order(1)
        body = dict(operation_id='empty', account_id=source['id'], order_id='ORDER-001', number='OUT', lines=[])
        with self.assertRaisesRegex(ValueError, '明細為空'):
            self.stock.shipment(body)
        self.stock.confirm_receipt(self.receipt()['id'], dict(operation_id='confirm'))
        del body['lines']
        shipment = self.stock.shipment(body)
        self.stock.transition(shipment['id'], 'allocate', dict(operation_id='allocate'))
        with self.assertRaisesRegex(ConflictError, '全部配貨或出貨'):
            self.stock.shipment(dict(body, operation_id='second', number='OUT-2'))

    def test_shipment_has_no_arbitrary_100_line_limit(self):
        from test_orders import row, as_csv
        source = self.catalogue.account(dict(operation_id='source', name='合成大量單', platform='manual'))
        rows = []
        for index in range(101):
            sku = f'BULK-{index:03}'
            self.catalogue.create(dict(operation_id=sku, sku=sku, name=f'合成商品{index}'))
            rows.append(row(line_id=str(index), external_sku=sku, expected_line_count='101', quantity='1',
                line_item_amount='1', order_item_amount='101'))
        batch = self.store.preview(source['id'], 'bulk.csv', as_csv(rows), True)
        self.assertEqual(self.store.confirm(batch['id'])['results'][0]['outcome'], 'applied')
        shipment = self.stock.shipment(dict(operation_id='bulk-ship', number='BULK', account_id=source['id'], order_id='O-001'))
        self.assertEqual(len(shipment['lines']), 101)

    def test_quality_change_releases_pick_and_can_reallocate_good_units(self):
        self.stock.confirm_receipt(self.receipt()['id'], dict(operation_id='confirm'))
        source = self.receive_order(2)
        shipment = self.stock.shipment(dict(operation_id='shipment', number='OUT', account_id=source['id'], order_id='ORDER-001'))
        self.stock.transition(shipment['id'], 'allocate', dict(operation_id='allocate'))
        self.stock.transition(shipment['id'], 'pick', dict(operation_id='pick'))
        unit = next(u for u in self.stock.snapshot()['units'] if u['shipment_id'])
        self.stock.quality(unit['id'], dict(operation_id='quality', quality='defective', packaging='good', reason='合成檢查'))
        snapshot = self.stock.snapshot()
        self.assertEqual(snapshot['shipments'][0]['state'], 'draft')
        self.assertEqual((snapshot['products'][0]['allocated'],snapshot['products'][0]['available']), (0,2))
        self.stock.transition(shipment['id'], 'allocate', dict(operation_id='reallocate'))
        self.stock.transition(shipment['id'], 'ship', dict(operation_id='ship'))
        self.assertEqual(next(u for u in self.stock.snapshot()['units'] if u['id']==unit['id'])['quality'], 'defective')
        self.assertEqual(report(self.store)['inventory'][0]['available'], 0)

    def test_draft_confirm_retry_reopen(self):
        draft = self.receipt()
        self.assertEqual(self.stock.snapshot()['units'], [])
        confirmed = self.stock.confirm_receipt(draft['id'], dict(operation_id='confirm'))
        self.assertEqual(confirmed['state'], 'confirmed')
        self.stock.confirm_receipt(draft['id'], dict(operation_id='confirm'))
        self.stock.confirm_receipt(draft['id'], dict(operation_id='confirm-again'))
        units = Stock(Store(self.store.path)).snapshot()['units']
        self.assertEqual(len(units), 3)
        self.assertEqual(sum(u['cost_cents'] for u in units), 31000)
        self.assertEqual(len({u['id'] for u in units}), 3)

    def test_unknown_product_and_invalid_receipt(self):
        for lines in [[dict(product_id='missing', quantity=1, unit_price='1')], [dict(product_id=self.product['id'], quantity=0, unit_price='1')]]:
            with self.assertRaises((ValueError, KeyError)):
                self.receipt(lines=lines)
        self.assertEqual(self.stock.snapshot()['receipts'], [])

    def test_freight_value_distribution(self):
        other = self.catalogue.create(dict(operation_id='p2', name='合成袋', sku='POUCH'))
        draft = self.receipt(lines=[dict(product_id=self.product['id'], quantity=1, unit_price='1.00'), dict(product_id=other['id'], quantity=2, unit_price='1.00')], freight='0.01', allocation='value')
        self.stock.confirm_receipt(draft['id'], dict(operation_id='confirm'))
        self.assertEqual(sum(u['cost_cents'] for u in self.stock.snapshot()['units']), 301)

    def receive_order(self, quantity=3):
        source = self.catalogue.account(dict(operation_id='source', name='合成門市', platform='manual'))
        body = dict(operation_id='order', account_id=source['id'], order_id='ORDER-001', ordered_at='2026-10-28T10:00:00+08:00',
                    lines=[dict(product_id=self.product['id'], quantity=quantity, unit_price='150.00')])
        result = Orders(self.store).receive(body)
        self.assertEqual(Orders(self.store).receive(body), result)
        return source

    def test_complete_flow_split_cancel_and_insufficient(self):
        draft = self.receipt()
        self.stock.confirm_receipt(draft['id'], dict(operation_id='confirm'))
        source = self.receive_order()
        def shipment(number, quantity):
            return self.stock.shipment(dict(operation_id=number, account_id=source['id'], order_id='ORDER-001', number=number,
                       lines=[dict(product_id=self.product['id'], quantity=quantity)]))
        main = shipment('OUT-1', 2)
        self.stock.transition(main['id'], 'allocate', dict(operation_id='allocate-1', barcode='0001'))
        self.assertEqual(self.stock.snapshot()['products'][0]['available'], 1)
        sub = shipment('OUT-2', 2)
        with self.assertRaises(ConflictError):
            self.stock.transition(sub['id'], 'allocate', dict(operation_id='too-many'))
        self.stock.transition(main['id'], 'pick', dict(operation_id='pick'))
        self.stock.transition(main['id'], 'cancel', dict(operation_id='cancel'))
        self.stock.transition(main['id'], 'cancel', dict(operation_id='cancel-again'))
        self.assertEqual(self.stock.snapshot()['products'][0]['available'], 3)
        self.stock.transition(sub['id'], 'allocate', dict(operation_id='allocate-2'))
        self.stock.transition(sub['id'], 'ship', dict(operation_id='ship-2'))
        self.stock.transition(sub['id'], 'ship', dict(operation_id='ship-2-again'))
        self.assertEqual(self.stock.snapshot()['products'][0]['on_hand'], 1)
        third = shipment('OUT-3', 1)
        self.stock.transition(third['id'], 'allocate', dict(operation_id='allocate-3'))
        self.stock.transition(third['id'], 'ship', dict(operation_id='ship-3'))
        with self.assertRaises(ConflictError):
            self.stock.transition(third['id'], 'cancel', dict(operation_id='bad-cancel'))
        snapshot = Stock(Store(self.store.path)).snapshot()
        self.assertEqual(snapshot['products'][0]['on_hand'], 0)
        self.assertEqual(len(snapshot['groups']), 1)
        self.assertEqual(len([s for s in snapshot['shipments'] if s['state'] == 'shipped']), 2)
        self.assertEqual(len([e for e in snapshot['events'] if e['kind'] == 'ship']), 3)

    def test_stock_shortage_does_not_invent_stock(self):
        source = self.receive_order(1)
        shipment = self.stock.shipment(dict(operation_id='out', account_id=source['id'], order_id='ORDER-001', number='OUT-1',
                                   lines=[dict(product_id=self.product['id'], quantity=1)]))
        with self.assertRaises(ConflictError):
            self.stock.transition(shipment['id'], 'allocate', dict(operation_id='no-stock'))
        self.assertEqual(self.stock.snapshot()['units'], [])
        self.assertEqual(len(self.store.current_orders()), 1)

    def test_unknown_order_product_and_parallel_allocation(self):
        draft = self.receipt()
        self.stock.confirm_receipt(draft['id'], dict(operation_id='confirm'))
        source = self.receive_order(2)
        base = dict(account_id=source['id'], ordered_at='2026-10-28T10:00:00+08:00')
        with self.assertRaises(KeyError):
            Orders(self.store).receive(dict(base, operation_id='bad-order', order_id='UNKNOWN', lines=[dict(product_id='absent', quantity=1, unit_price='100')]))
        Orders(self.store).receive(dict(base, operation_id='order2', order_id='ORDER-002', lines=[dict(product_id=self.product['id'], quantity=2, unit_price='150')]))
        shipments = [self.stock.shipment(dict(operation_id=f'create-{i}', account_id=source['id'], order_id=f'ORDER-00{i}', number=f'OUT-{i}',
                    lines=[dict(product_id=self.product['id'], quantity=2)])) for i in (1, 2)]
        def allocate(shipment):
            try:
                return self.stock.transition(shipment['id'], 'allocate', dict(operation_id='allocate-' + shipment['id']))['state']
            except ConflictError:
                return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as executor:
            states = list(executor.map(allocate, shipments))
        self.assertEqual(sorted(states), ['allocated', 'rejected'])
        self.assertEqual(self.stock.snapshot()['products'][0]['available'], 1)

    def test_receipt_rolls_back_if_audit_fails(self):
        draft = self.receipt()
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_stock_event BEFORE INSERT ON stock_events BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(Exception):
            self.stock.confirm_receipt(draft['id'], dict(operation_id='confirm'))
        self.assertEqual(self.stock.snapshot()['units'], [])
        self.assertEqual(self.stock.snapshot()['receipts'][0]['state'], 'draft')

    def test_defect_cancel_and_return_pending(self):
        draft = self.receipt()
        self.stock.confirm_receipt(draft['id'], dict(operation_id='confirm'))
        source = self.receive_order(1)
        s = self.stock.shipment(dict(operation_id='out', account_id=source['id'], order_id='ORDER-001', number='OUT-1',
            lines=[dict(product_id=self.product['id'], quantity=1)]))
        self.stock.transition(s['id'], 'allocate', dict(operation_id='allocate'))
        unit = next(u for u in self.stock.snapshot()['units'] if u['shipment_id'])
        self.stock.quality(unit['id'], dict(operation_id='defect', quality='damaged', packaging='good', reason='檢查發現損毀'))
        with self.assertRaises(ConflictError):
            self.stock.transition(s['id'], 'ship', dict(operation_id='bad-ship'))
        self.stock.transition(s['id'], 'cancel', dict(operation_id='cancel'))
        self.assertEqual(self.stock.snapshot()['products'][0]['available'], 2)
        second = self.stock.shipment(dict(operation_id='out2', account_id=source['id'], order_id='ORDER-001', number='OUT-2',
            lines=[dict(product_id=self.product['id'], quantity=1)]))
        self.stock.transition(second['id'], 'allocate', dict(operation_id='allocate2'))
        self.stock.transition(second['id'], 'ship', dict(operation_id='ship2'))
        sent = next(u for u in self.stock.snapshot()['units'] if u['location'] == 'customer')
        result = self.stock.receive_return(sent['id'], dict(operation_id='return', shipment_id=second['id'], reason='客戶實退'))
        self.assertEqual(result['quality'], 'good')
        self.stock.receive_return(sent['id'], dict(operation_id='return-again', shipment_id=second['id'], reason='客戶實退'))
        self.assertEqual(len([e for e in self.stock.snapshot()['events'] if e['kind'] == 'return']), 1)
        self.assertEqual(self.stock.snapshot()['products'][0]['available'], 1)
        with self.assertRaises(ConflictError):
            self.stock.quality(sent['id'], dict(operation_id='release-unapproved', quality='good', packaging='good', reason='政策尚待確認'))
        with self.store.connect() as db:
            self.assertTrue(db.execute("SELECT 1 FROM events WHERE action LIKE '%rejected' AND target='bad-ship'").fetchone())

    def test_return_to_stock_preserves_identity_cost_quality_and_history(self):
        receipt = self.receipt()
        self.stock.confirm_receipt(receipt['id'], dict(operation_id='in'))
        source = self.receive_order(1)
        out = self.stock.shipment(dict(operation_id='out', number='OUT', account_id=source['id'], order_id='ORDER-001'))
        self.stock.transition(out['id'], 'allocate', dict(operation_id='allocate'))
        old = next(u for u in self.stock.snapshot()['units'] if u['shipment_id']==out['id'])
        self.stock.transition(out['id'], 'ship', dict(operation_id='ship'))
        self.stock.receive_return(old['id'], dict(operation_id='return', shipment_id=out['id'], reason='實際退回'))
        returned = next(u for u in self.stock.snapshot()['units'] if u['id']==old['id'])
        self.assertTrue(returned['return_hold'])
        self.assertEqual(self.stock.snapshot()['products'][0]['available'], 2)
        body = dict(operation_id='restock', ship_event_id=returned['return_ship_event_id'], reason='轉回入庫')
        result = self.stock.restock_return(old['id'], body)
        for key in ('id','receipt_id','cost_cents','first_received_at','fifo_at'):
            self.assertEqual(result[key], old[key])
        self.assertEqual(self.stock.snapshot()['products'][0]['available'], 3)
        self.assertEqual(self.stock.restock_return(old['id'], dict(body, operation_id='retry')), result)
        second = self.stock.shipment(dict(operation_id='out2', entry_number='SECOND'))
        self.stock.scan(second['id'], dict(operation_id='scan2', barcode=self.product['barcode']))
        self.stock.transition(second['id'], 'complete', dict(operation_id='ship2'))
        sent = next(u for u in self.stock.snapshot()['units'] if u['location']=='customer')
        self.stock.receive_return(sent['id'], dict(operation_id='return2', shipment_id=second['id'], quality='defective', reason='瑕疵實退'))
        current = next(u for u in self.stock.snapshot()['units'] if u['id']==sent['id'])
        self.stock.restock_return(sent['id'], dict(operation_id='restock2',ship_event_id=current['return_ship_event_id'],reason='瑕疵歸回'))
        self.assertEqual(self.stock.snapshot()['products'][0]['available'], 2)
        self.assertEqual(next(u for u in self.stock.snapshot()['units'] if u['id']==sent['id'])['quality'], 'defective')
        rebuilt = report(self.store)
        item = next(p for p in rebuilt['inventory'] if p['product_id']==self.product['id'])
        self.assertEqual((item['on_hand'],item['available']),(3,2))
        self.stock.restock_return(old['id'], dict(body,operation_id='stale-retry'))
        self.assertEqual(self.stock.snapshot()['products'][0]['available'],2)

    def test_return_hold_survives_intermediate_quality_and_restart(self):
        receipt = self.receipt()
        self.stock.confirm_receipt(receipt['id'], dict(operation_id='confirm'))
        source = self.receive_order(2)
        shipment = self.stock.shipment(dict(operation_id='out', number='OUT', account_id=source['id'], order_id='ORDER-001'))
        self.stock.transition(shipment['id'], 'allocate', dict(operation_id='allocate'))
        self.stock.transition(shipment['id'], 'ship', dict(operation_id='ship'))
        sent = next(u for u in self.stock.snapshot()['units'] if u['location']=='customer')
        self.stock.receive_return(sent['id'], dict(operation_id='return', shipment_id=shipment['id'], reason='合成退回'))
        for index, intermediate in enumerate(('pending','defective','damaged','pending')):
            self.stock.quality(sent['id'], dict(operation_id=f'quality-{index}', quality=intermediate, packaging='good', reason='實際檢查'))
            reopened = Stock(Store(self.store.path))
            with self.assertRaises(ConflictError):
                reopened.quality(sent['id'], dict(operation_id=f'release-{index}', quality='good', packaging='good', reason='不得繞過放行'))
            snapshot = reopened.snapshot()
            self.assertEqual(next(u for u in snapshot['units'] if u['id']==sent['id'])['quality'], intermediate)
            self.assertEqual(snapshot['products'][0]['available'], 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM stock_returns WHERE unit_id=?',(sent['id'],)).fetchone()[0],1)

    def test_explicit_return_release_original_fifo_and_idempotent_cycles(self):
        first=self.receipt(lines=[dict(product_id=self.product['id'],quantity=1,unit_price='90')],freight='0')
        self.stock.confirm_receipt(first['id'],dict(operation_id='in1'))
        second=self.stock.receipt(dict(operation_id='second',number='IN2',lines=[dict(product_id=self.product['id'],quantity=1,unit_price='120')]))
        self.stock.confirm_receipt(second['id'],dict(operation_id='in2'))
        source=self.receive_order(1)
        out=self.stock.shipment(dict(operation_id='out',number='OUT',account_id=source['id'],order_id='ORDER-001'))
        self.stock.transition(out['id'],'allocate',dict(operation_id='allocate'))
        old=next(u for u in self.stock.snapshot()['units'] if u['shipment_id']==out['id'])
        self.assertEqual(old['receipt_id'],first['id'])
        self.stock.transition(out['id'],'ship',dict(operation_id='ship'))
        self.stock.receive_return(old['id'],dict(operation_id='return',shipment_id=out['id'],reason='合成實退'))
        unit=next(u for u in self.stock.snapshot()['units'] if u['id']==old['id'])
        body=dict(operation_id='release',ship_event_id=unit['return_ship_event_id'],inspection_passed=True,reason='已實際檢查品質及包裝')
        with self.assertRaises(ValueError):
            self.stock.release_return(old['id'],dict(body,operation_id='no-inspection',inspection_passed=False))
        self.stock.quality(old['id'],dict(operation_id='defective',quality='defective',packaging='damaged',reason='修復前'))
        self.assertEqual(self.stock.snapshot()['products'][0]['available'],1)
        released=self.stock.release_return(old['id'],body)
        self.assertEqual(released['fifo_at'],old['first_received_at'])
        self.assertEqual(released['cost_cents'],9000)
        self.assertEqual(self.stock.release_return(old['id'],body),released)
        self.assertEqual(self.stock.release_return(old['id'],dict(body,operation_id='retry-new')),released)
        self.assertEqual(self.stock.snapshot()['products'][0]['available'],2)
        self.assertEqual(len([e for e in self.stock.snapshot()['events'] if e['kind']=='return_release']),1)
        # A fresh sales order must pick the original older unit, not the newer batch.
        from ops.orders import Orders
        Orders(self.store).receive(dict(operation_id='order2',account_id=source['id'],order_id='ORDER-002',ordered_at='2026-10-28T10:00:00+08:00',lines=[dict(product_id=self.product['id'],quantity=1,unit_price='150')]))
        again=self.stock.shipment(dict(operation_id='out2',number='OUT2',account_id=source['id'],order_id='ORDER-002'))
        self.stock.transition(again['id'],'allocate',dict(operation_id='allocate2'))
        self.assertEqual(next(u for u in self.stock.snapshot()['units'] if u['shipment_id']==again['id'])['id'],old['id'])
        self.stock.transition(again['id'],'ship',dict(operation_id='ship2'))
        self.stock.receive_return(old['id'],dict(operation_id='return2',shipment_id=again['id'],reason='第二週期實退'))
        self.stock.release_return(old['id'],dict(body,operation_id='stale-release'))
        self.assertEqual(self.stock.snapshot()['products'][0]['available'],1)
        self.assertTrue(next(u for u in self.stock.snapshot()['units'] if u['id']==old['id'])['return_hold'])

    def test_deduct_pending_handoff_reopen_and_retry_do_not_double_deduct(self):
        receipt=self.receipt()
        self.stock.confirm_receipt(receipt['id'],dict(operation_id='in'))
        source=self.receive_order(2)
        out=self.stock.shipment(dict(operation_id='out',number='OUT',account_id=source['id'],order_id='ORDER-001'))
        self.stock.transition(out['id'],'allocate',dict(operation_id='allocate'))
        self.stock.transition(out['id'],'deduct',dict(operation_id='deduct'))
        data=self.stock.snapshot()
        self.assertEqual((data['products'][0]['on_hand'],data['products'][0]['staged'],data['products'][0]['available']),(3,2,1))
        self.stock.transition(out['id'],'deduct',dict(operation_id='deduct-again'))
        self.stock.set_work_state(out['id'],dict(operation_id='reopen',state='open',reason='已扣未寄，重新核對'))
        self.assertEqual(len([e for e in self.stock.snapshot()['events'] if e['kind']=='deduct']),2)
        self.stock.transition(out['id'],'ship',dict(operation_id='send'))
        self.stock.set_work_state(out['id'],dict(operation_id='reopen2',state='open',reason='人工重開追加前確認'))
        self.stock.transition(out['id'],'ship',dict(operation_id='send-again'))
        data=self.stock.snapshot()
        self.assertEqual(data['products'][0]['on_hand'],1)
        self.assertEqual(len([e for e in data['events'] if e['kind']=='deduct']),2)
        self.assertEqual(len([e for e in data['events'] if e['kind']=='ship']),2)
        self.assertEqual(data['shipments'][0]['state'],'shipped')

    def test_staged_cancel_is_explicit_preserves_physical_count_and_audit(self):
        receipt=self.receipt()
        self.stock.confirm_receipt(receipt['id'],dict(operation_id='in'))
        source=self.receive_order(2)
        out=self.stock.shipment(dict(operation_id='out',number='OUT',account_id=source['id'],order_id='ORDER-001'))
        self.stock.transition(out['id'],'allocate',dict(operation_id='allocate'))
        self.stock.transition(out['id'],'deduct',dict(operation_id='deduct'))
        with self.assertRaises(ConflictError):
            self.stock.transition(out['id'],'cancel',dict(operation_id='plain-cancel'))
        with self.assertRaises(ValueError):
            self.stock.cancel_staged(out['id'],dict(operation_id='missing-check',reason='取消'))
        report_now=report(self.store)
        self.assertEqual((report_now['inventory'][0]['on_hand'],report_now['inventory'][0]['staged'],report_now['inventory'][0]['available']),(3,2,1))
        body=dict(operation_id='cancel-staged',reason='尚未寄出，實物已核對歸位',returned_to_saleable=True)
        self.stock.cancel_staged(out['id'],body)
        self.stock.cancel_staged(out['id'],dict(body,operation_id='retry-cancel'))
        snapshot=self.stock.snapshot()
        self.assertEqual((snapshot['products'][0]['on_hand'],snapshot['products'][0]['available']),(3,3))
        self.assertEqual(len([e for e in snapshot['events'] if e['kind']=='deduct_cancel']),2)
        self.assertEqual(report(self.store)['inventory'][0]['available'],3)

    def test_order_reopen_append_only_new_quantity_and_cancel_preserves_shipped(self):
        from ops.orders import Orders
        from ops.sales import sales
        service=Orders(self.store)
        receipt=self.receipt();self.stock.confirm_receipt(receipt['id'],dict(operation_id='in'))
        source=self.receive_order(2)
        origin=dict(account_id=source['id'],order_id='ORDER-001')
        first=self.stock.shipment(dict(operation_id='first',number='OUT',**origin))
        self.stock.transition(first['id'],'allocate',dict(operation_id='alloc'))
        self.stock.transition(first['id'],'ship',dict(operation_id='ship'))
        revenue=sales(self.store)['amount']
        work=service.reopen(dict(operation_id='reopen',reason='人工追加商品',expected_version=0,**origin))
        body=dict(operation_id='append',number='ADD-1',reason='本次人工追加',expected_version=work['version'],lines=[dict(product_id=self.product['id'],quantity=1)],**origin)
        added=service.append(body);self.assertEqual(service.append(body),added)
        with self.assertRaises(ConflictError):service.append(dict(body,operation_id='duplicate-number'))
        second=self.stock.shipment(dict(operation_id='second',number='OUT2',**origin))
        self.assertEqual(second['lines'][0]['quantity'],1)
        self.stock.transition(second['id'],'allocate',dict(operation_id='alloc2'))
        self.stock.transition(second['id'],'cancel',dict(operation_id='cancel2'))
        data=self.stock.snapshot()
        self.assertEqual(next(s for s in data['shipments'] if s['id']==first['id'])['state'],'shipped')
        self.assertEqual(data['products'][0]['on_hand'],1)
        self.assertEqual(sales(self.store)['amount'],revenue)
        self.assertEqual(len([e for e in data['events'] if e['kind']=='ship']),2)

    def test_receipt_draft_edit_checks_snapshot_and_confirmed_is_immutable(self):
        receipt=self.receipt()
        body=dict(operation_id='edit',id=receipt['id'],number=receipt['number'],expected_snapshot={k:receipt[k] for k in ('number','lines','freight_cents','allocation')},lines=[dict(product_id=self.product['id'],quantity=2,unit_price='100')],freight='10',allocation='quantity')
        changed=self.stock.receipt(body)
        self.assertEqual(self.stock.snapshot()['products'][0]['on_hand'],0)
        self.assertEqual(changed['lines'][0]['quantity'],2)
        with self.assertRaises(ConflictError):self.stock.receipt(dict(body,operation_id='stale-edit'))
        self.stock.confirm_receipt(receipt['id'],dict(operation_id='confirm-edited'))
        self.assertEqual(self.stock.snapshot()['products'][0]['on_hand'],2)
        with self.assertRaises(ConflictError):self.stock.receipt(dict(body,operation_id='edit-confirmed',expected_snapshot={k:changed[k] for k in ('number','lines','freight_cents','allocation')}))

    def test_receipt_source_persists_and_cannot_be_stale_overwritten(self):
        receipt=self.receipt(source='  合成工廠  ',comment='本批樣品')
        self.assertEqual(receipt['source'],'合成工廠')
        self.assertEqual(Stock(Store(self.store.path)).snapshot()['receipts'][0]['comment'],'本批樣品')
        self.assertEqual(self.stock.snapshot()['products'][0]['available'],0)
        fields=('number','lines','freight_cents','allocation','source','comment')
        body=dict(operation_id='source-edit',id=receipt['id'],number=receipt['number'],
            expected_snapshot={k:receipt[k] for k in fields},source='合成供應商乙',comment='確認數量後入庫',
            lines=[dict(product_id=self.product['id'],quantity=3,unit_price='100')],freight='10',allocation='quantity')
        changed=self.stock.receipt(body)
        self.assertEqual(self.stock.receipt(body),changed)
        with self.assertRaises(ConflictError):self.stock.receipt(dict(body,operation_id='stale-source'))
        with self.assertRaises(ConflictError):self.stock.receipt(dict(body,operation_id='old-client',expected_snapshot={k:receipt[k] for k in fields[:4]}))
        self.stock.confirm_receipt(receipt['id'],dict(operation_id='source-confirm'))
        self.stock.confirm_receipt(receipt['id'],dict(operation_id='source-confirm-again'))
        self.assertEqual(self.stock.snapshot()['products'][0]['available'],3)
        with self.assertRaises(ConflictError):self.stock.receipt(dict(body,operation_id='confirmed-edit',expected_snapshot={k:changed[k] for k in fields}))
        self.assertEqual(Stock(Store(self.store.path)).snapshot()['receipts'][0]['source'],'合成供應商乙')
        with self.assertRaises(ValueError):self.receipt(operation_id='invalid-source',number='BAD',source=['wrong'])

    def test_receipt_source_upgrade_preserves_old_rows(self):
        import sqlite3
        path=Path(self.temp.name)/'legacy.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE receipts(id TEXT PRIMARY KEY,number TEXT NOT NULL UNIQUE,state TEXT NOT NULL,lines TEXT NOT NULL,freight_cents INTEGER NOT NULL,allocation TEXT NOT NULL,created_at TEXT NOT NULL,confirmed_at TEXT)')
            db.execute("INSERT INTO receipts VALUES('legacy','LEGACY','draft','[]',0,'quantity','2026-01-01',NULL)")
        db.close()
        upgraded=Stock(Store(path)).snapshot()['receipts'][0]
        self.assertEqual((upgraded['id'],upgraded['source'],upgraded['comment']),('legacy','',''))
        self.assertEqual(Stock(Store(path)).snapshot()['receipts'][0],upgraded)

    def test_receipt_order_waits_for_arrival_and_retries_preserve_stock(self):
        r=self.receipt()
        body=dict(operation_id='place-receipt')
        ordered=self.stock.receipt_stage(r['id'],'order',body)
        self.assertEqual(ordered['state'],'ordered')
        self.assertTrue(ordered['ordered_at'])
        self.assertEqual(self.stock.receipt_stage(r['id'],'order',body),ordered)
        self.assertEqual(self.stock.receipt_stage(r['id'],'order',dict(operation_id='order-again')),ordered)
        self.assertEqual(Stock(Store(self.store.path)).snapshot()['products'][0]['available'],0)
        changed=self.stock.receipt(dict(operation_id='ordered-edit',id=r['id'],number=r['number'],expected_snapshot={k:ordered[k] for k in ('number','lines','freight_cents','allocation','source','comment')},lines=[dict(product_id=self.product['id'],quantity=2,unit_price='100')],freight='10'))
        self.assertEqual(changed['state'],'ordered')
        result=self.stock.confirm_receipt(r['id'],dict(operation_id='arrived'))
        self.assertEqual(result['ordered_at'],ordered['ordered_at'])
        self.stock.confirm_receipt(r['id'],dict(operation_id='arrived-again'))
        self.assertEqual(self.stock.snapshot()['products'][0]['available'],2)
        with self.assertRaises(ConflictError):self.stock.receipt_stage(r['id'],'cancel',dict(operation_id='late-cancel',reason='不可取消'))
        with self.assertRaises(ConflictError):self.stock.receipt_stage(r['id'],'order',dict(operation_id='late-order'))

    def test_receipt_cancel_blocks_arrival_and_preserves_reason(self):
        r=self.receipt()
        with self.assertRaises(ValueError):self.stock.receipt_stage(r['id'],'cancel',dict(operation_id='no-reason'))
        cancelled=self.stock.receipt_stage(r['id'],'cancel',dict(operation_id='cancel-in',reason='供應商無貨'))
        self.assertEqual(cancelled['state'],'cancelled')
        self.assertEqual(self.stock.receipt_stage(r['id'],'cancel',dict(operation_id='cancel-in-again',reason='重試')),cancelled)
        with self.assertRaises(ConflictError):self.stock.confirm_receipt(r['id'],dict(operation_id='cancelled-arrive'))
        with self.assertRaises(ConflictError):self.stock.receipt_stage(r['id'],'order',dict(operation_id='cancelled-order'))
        self.assertEqual(Stock(Store(self.store.path)).snapshot()['receipts'][0]['cancel_reason'],'供應商無貨')
        self.assertEqual(self.stock.snapshot()['units'],[])

    def test_receipt_cancel_and_confirm_are_atomic(self):
        r=self.receipt()
        self.stock.receipt_stage(r['id'],'order',dict(operation_id='ordered-race'))
        def attempt(action):
            try:
                if action=='confirm':return self.stock.confirm_receipt(r['id'],dict(operation_id='race-confirm'))['state']
                return self.stock.receipt_stage(r['id'],'cancel',dict(operation_id='race-cancel',reason='取消'))['state']
            except ConflictError:return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,['confirm','cancel']))
        self.assertEqual(results.count('conflict'),1)
        data=self.stock.snapshot()
        self.assertEqual(len(data['units']),3 if data['receipts'][0]['state']=='confirmed' else 0)

    def test_staged_defect_cancel_returns_physical_but_not_saleable(self):
        r=self.receipt();self.stock.confirm_receipt(r['id'],dict(operation_id='confirm-defect'))
        source=self.receive_order(2)
        s=self.stock.shipment(dict(operation_id='staged-s',number='STAGED-S',account_id=source['id'],order_id='ORDER-001'))
        self.stock.transition(s['id'],'allocate',dict(operation_id='staged-a'))
        self.stock.transition(s['id'],'deduct',dict(operation_id='staged-d'))
        unit=next(u for u in self.stock.snapshot()['units'] if u['shipment_id']==s['id'])
        self.stock.quality(unit['id'],dict(operation_id='staged-bad',quality='defective',packaging='damaged',reason='包貨時發現瑕疵'))
        data=self.stock.snapshot()
        self.assertEqual(data['products'][0]['defective'],1)
        self.assertEqual(sum(u['shipment_id']==s['id'] for u in data['units']),2)
        with self.assertRaises(ConflictError):self.stock.transition(s['id'],'ship',dict(operation_id='bad-ship'))
        with self.assertRaises(ConflictError):self.stock.cancel_staged(s['id'],dict(operation_id='bad-saleable',reason='取消',returned_to_saleable=True))
        body=dict(operation_id='return-staged',reason='實物已歸回隔離',returned_to_warehouse=True)
        self.stock.cancel_staged(s['id'],body)
        self.stock.cancel_staged(s['id'],dict(body,operation_id='return-staged-again'))
        data=self.stock.snapshot();p=data['products'][0]
        self.assertEqual((p['on_hand'],p['available'],p['defective'],p['packaging_damaged'],p['staged']),(3,2,1,1,0))
        self.assertEqual(report(self.store)['inventory'][0]['available'],2)
        self.assertEqual(sum(e['kind']=='deduct_cancel' for e in data['events']),2)
        self.assertEqual(next(u for u in data['units'] if u['id']==unit['id'])['cost_cents'],unit['cost_cents'])

    def test_sku_report_daily_and_subshipment_count_share_same_scope(self):
        r=self.receipt();self.stock.confirm_receipt(r['id'],dict(operation_id='sku-in'))
        other=self.catalogue.create(dict(operation_id='other-p',name='其他合成商品',sku='OTHER'))
        r2=self.receipt(operation_id='other-in',number='OTHER-IN',lines=[dict(product_id=other['id'],quantity=2,unit_price='50')])
        self.stock.confirm_receipt(r2['id'],dict(operation_id='other-confirm'))
        source=self.receive_order(2);ships=[]
        for index in range(2):
            s=self.stock.shipment(dict(operation_id=f'sku-s{index}',number=f'SKU-S{index}',account_id=source['id'],order_id='ORDER-001',lines=[dict(product_id=self.product['id'],quantity=1)]))
            self.stock.transition(s['id'],'allocate',dict(operation_id=f'sku-a{index}'))
            self.stock.transition(s['id'],'ship',dict(operation_id=f'sku-ship{index}'));ships.append(s)
        self.assertEqual(ships[1]['parent_id'],ships[0]['id'])
        data=report(self.store,start='2000-01-01',end='2099-01-01',product_id=self.product['id'])
        self.assertEqual((data['totals']['received'],data['totals']['shipped'],data['totals']['shipping_orders'],data['totals']['shipment_count']),(3,2,1,2))
        self.assertEqual([p['product_id'] for p in data['inventory']],[self.product['id']])
        self.assertEqual(sum(d['shipped'] for d in data['daily']),2)
        other_data=report(self.store,start='2000-01-01',end='2099-01-01',product_id=other['id'])
        self.assertEqual((other_data['totals']['received'],other_data['totals']['shipped']),(2,0))
        from ops.stock_reports import export_report
        import csv,io
        rows=list(csv.DictReader(io.StringIO(export_report(data,'daily'))))
        self.assertTrue(all(row['product_id_filter']==self.product['id'] for row in rows))
        with self.assertRaises(KeyError):report(self.store,product_id='not-created')

    def test_receipt_supports_more_than_100_selected_skus(self):
        products=[self.product]
        for i in range(100):products.append(self.catalogue.create(dict(operation_id=f'picker-p{i}',name=f'合成商品{i}',sku=f'PICK-{i}')))
        receipt=self.receipt(operation_id='picker-in',lines=[dict(product_id=p['id'],quantity=1,unit_price='1') for p in products],freight='0')
        self.assertEqual(len(receipt['lines']),101)
        self.assertEqual(len(self.stock.snapshot()['units']),0)
        self.stock.confirm_receipt(receipt['id'],dict(operation_id='picker-confirm'))
        self.stock.confirm_receipt(receipt['id'],dict(operation_id='picker-confirm-retry'))
        self.assertEqual(len(self.stock.snapshot()['units']),101)

    def test_reports_rebuild_history_and_alerts(self):
        receipt = self.receipt()
        self.stock.confirm_receipt(receipt['id'], dict(operation_id='confirm'))
        source = self.receive_order(2)
        shipment = self.stock.shipment(dict(operation_id='out', number='OUT', account_id=source['id'], order_id='ORDER-001',
                    lines=[dict(product_id=self.product['id'], quantity=2)]))
        self.stock.transition(shipment['id'], 'allocate', dict(operation_id='allocate'))
        self.stock.transition(shipment['id'], 'ship', dict(operation_id='ship'))
        with self.store.connect() as db:
            db.execute("UPDATE stock_events SET time='2026-10-01T00:00:00+00:00' WHERE kind='receipt'")
            db.execute("UPDATE stock_events SET time='2026-10-02T00:00:00+00:00' WHERE kind IN ('allocate','ship')")
            db.execute("UPDATE stock_meta SET value='2026-09-01T00:00:00+00:00' WHERE key='coverage_since'")
        save_alert(self.store, dict(operation_id='alert', product_id=self.product['id'], stock_min=2, coverage_days='3'))
        result = report(self.store, start='2026-10-01', end='2026-10-02', as_of='2026-10-03T00:00:00Z')
        self.assertEqual(result['totals']['received'], 3)
        self.assertEqual(result['totals']['shipped'], 2)
        self.assertEqual(result['totals']['shipping_orders'], 1)
        self.assertEqual(result['totals']['shipment_count'], 1)
        p = result['inventory'][0]
        self.assertEqual(p['available'], 1)
        self.assertTrue(p['stock_alert'])
        self.assertTrue(p['coverage_alert'])
        before = report(self.store, start='2026-10-01', end='2026-10-01', as_of='2026-10-01T12:00:00Z')
        self.assertEqual(before['inventory'][0]['on_hand'], 3)

    def test_fifo_uses_receipt_time_and_actual_batch_cost(self):
        first = self.receipt(lines=[dict(product_id=self.product['id'], quantity=1, unit_price='90')], freight='0')
        self.stock.confirm_receipt(first['id'], dict(operation_id='first-in'))
        second = self.receipt(operation_id='second', number='IN-2', lines=[dict(product_id=self.product['id'], quantity=2, unit_price='100')], freight='0')
        self.stock.confirm_receipt(second['id'], dict(operation_id='second-in'))
        source = self.receive_order(2)
        s = self.stock.shipment(dict(operation_id='out', account_id=source['id'], order_id='ORDER-001', number='OUT',
                               lines=[dict(product_id=self.product['id'], quantity=2)]))
        self.stock.transition(s['id'], 'allocate', dict(operation_id='allocate'))
        units = [u for u in self.stock.snapshot()['units'] if u['shipment_id'] == s['id']]
        self.assertEqual(sorted(u['cost_cents'] for u in units), [9000, 10000])


if __name__ == '__main__':
    unittest.main()

