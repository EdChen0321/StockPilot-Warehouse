import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from ops.store import Store
from ops.catalogue import Catalogue
from ops.stock import Stock
from ops.locations import Locations
from ops.stock_reports import report
from ops.ai_queries import ReadOnlyStore
from ops.ai_inventory import query_inventory, VIEWS
from ops.ai_chat import ChatService


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'inventory.sqlite3'
        self.store = Store(self.path)
        self.catalogue = Catalogue(self.store)
        self.product = self.catalogue.create(dict(operation_id='p', name='保溫杯', sku='CUP', barcode='123'))
        self.catalogue.create(dict(operation_id='empty', name='杯袋', sku='BAG', barcode='456'))
        self.stock = Stock(self.store)
        self.locations = Locations(self.store)
        self.locations.enable(dict(operation_id='enable'))
        self.place = self.locations.save(dict(operation_id='place', address='1F', position='A', name='一般品',
            can_import=True, can_export=True, can_return=True, entry_state='import'))
        self.receipt = self.stock.receipt(dict(operation_id='receipt', number='IN-CUP', source='供應商',
            lines=[dict(product_id=self.product['id'], quantity=4, unit_price='100')]))
        self.stock.confirm_receipt(self.receipt['id'], dict(operation_id='confirm'))

    def query(self, **args):
        return query_inventory(self.path, args)

    def assign(self):
        self.locations.assign(dict(operation_id='assign', receipt_id=self.receipt['id'], place_id=self.place['id']))

    def test_summary_counts_current_not_shipments(self):
        data = self.query()
        self.assertEqual((data['totals']['product_count'], data['totals']['in_stock_sku_count'],
                          data['totals']['on_hand'], data['totals']['available']), (2, 1, 4, 0))
        self.assertEqual(data['totals']['unassigned'], 4)
        self.assign()
        self.assertEqual(self.query()['totals']['available'], 4)
        p = next(p for p in self.stock.snapshot()['products'] if p['id'] == self.product['id'])
        row = self.query(search='CUP')['rows'][0]
        for key in ('on_hand', 'available', 'stock_cost_amount', 'unassigned'):
            self.assertEqual(row[key], p[key])
        self.assertEqual(self.query(search='不存在')['total_rows'], 0)

    def test_all_views_readonly_and_reject_writes(self):
        before = self.path.read_bytes()
        for view in VIEWS:
            with self.subTest(view=view):
                self.assertIn('rows', self.query(view=view))
        self.assertEqual(self.path.read_bytes(), before)
        with ReadOnlyStore(self.path).connect() as db:
            for sql in ('INSERT INTO stock_meta VALUES(\'ai\',\'x\')',
                        "UPDATE stock_units SET location='customer'", 'DELETE FROM stock_units'):
                with self.assertRaises(sqlite3.OperationalError): db.execute(sql)
        for args in ({'sql': 'SELECT * FROM stock_units'}, {'view': 'delete'}, {'limit': 51},
                     {'offset': -1}, {'limit': True}, {'status': 'unknown'}, {'start': '2026-11-01'},
                     {'view': 'report', 'search': 'no-such-sku'}):
            with self.subTest(args=args), self.assertRaises(ValueError): self.query(**args)

    def test_documents_events_return_and_allocation(self):
        self.assign()
        shipment = self.stock.shipment(dict(operation_id='shipment', entry_number='OUT-CUP'))
        unit = self.stock.scan(shipment['id'], dict(operation_id='scan', barcode='123'))['unit_id']
        self.assertEqual(self.query()['totals']['allocated'], 1)
        self.assertEqual(self.query(view='units', status='allocated')['total_rows'], 1)
        self.stock.transition(shipment['id'], 'complete', dict(operation_id='ship'))
        self.assertEqual(self.query()['totals']['on_hand'], 3)
        self.assertEqual(self.query(view='units', status='customer')['total_rows'], 1)
        self.stock.receive_return(unit, dict(operation_id='return', shipment_id=shipment['id'], reason='客退', place_id=self.place['id']))
        self.assertEqual(self.query()['totals']['return_held'], 1)
        self.assertEqual(self.query()['totals']['available'], 3)
        self.assertEqual(self.query(view='shipments', search='OUT-CUP')['rows'][0]['state'], 'shipped')
        self.assertEqual(self.query(view='receipts', search='IN-CUP')['rows'][0]['source'], '供應商')
        events = self.query(view='events', search=unit)['rows']
        self.assertTrue(any(e['kind'] == 'return' for e in events))
        self.assertTrue(any(e['details'].get('item_snapshot', {}).get('sku') == 'CUP' for e in events))
        self.assertEqual(self.query(view='locations')['rows'][0]['unit_count'], 4)

    def test_pending_cost_and_pagination(self):
        r = self.stock.receipt(dict(operation_id='pending', number='IN-PENDING',
            lines=[dict(product_id=self.product['id'], quantity=1, unit_price='')]))
        self.stock.confirm_receipt(r['id'], dict(operation_id='pending-confirm'))
        data = self.query(limit=1)
        self.assertEqual(data['total_rows'], 2)
        self.assertEqual(data['next_offset'], 1)
        self.assertEqual(data['totals']['on_hand'], 5)
        self.assertIsNone(data['rows'][0]['stock_cost_amount'])
        self.assertEqual(data['rows'][0]['stock_cost_pending'], 1)
        self.assertEqual(data['rows'][0]['stock_cost_known_amount'], '400.00')
        self.assertIsNone(data['totals']['stock_cost_amount'])
        self.assertEqual(data['totals']['stock_cost_known_amount'], '400.00')
        self.assertEqual(self.query(offset=1, limit=1)['rows'][0]['sku'], 'BAG')
        self.assertNotIn('comment', self.query(view='receipts')['rows'][0])

    def test_report_matches_existing_and_period(self):
        expected = report(ReadOnlyStore(self.path), start='2026-01-01', end='2026-12-31')
        actual = self.query(view='report', start='2026-01-01', end='2026-12-31')
        self.assertEqual(actual['totals'], expected['totals'])
        self.assertEqual(actual['rows'], expected['inventory'])
        self.assertEqual(actual['data_complete'], expected['data_complete'])
        self.assertEqual(self.query(view='receipts', start='2000-01-01', end='2000-01-02')['total_rows'], 0)

    def test_transfer_and_cost_history(self):
        self.assign()
        target = self.locations.save(dict(operation_id='box-place', address='1F', position='B', name='盒損',
            can_import=True, can_export=True, can_return=True, entry_state='boxDamaged'))
        unit = self.stock.snapshot()['units'][0]['id']
        transfer = self.locations.transfer(dict(operation_id='transfer', number='MOVE-1', from_place_id=self.place['id'],
            to_place_id=target['id'], unit_ids=[unit]))
        self.locations.transition(transfer['id'], 'start', dict(operation_id='move'))
        self.assertEqual(self.query()['totals']['transferring'], 1)
        self.locations.transition(transfer['id'], 'complete', dict(operation_id='arrive'))
        self.assertEqual(self.query(view='transfers', status='complete')['total_rows'], 1)
        self.assertEqual(self.query()['totals']['box_available'], 1)
        version = self.query(view='receipts')['rows'][0]['cost_version']
        self.stock.revise_receipt_cost(self.receipt['id'], dict(operation_id='revise', expected_cost_version=version,
            reason='補運費', lines=[dict(product_id=self.product['id'], quantity=4, unit_price='100')], freight='40', tax='0', allocation='quantity'))
        history = self.query(view='cost_history', search=unit)
        self.assertEqual(history['total_rows'], 1)
        self.assertEqual(history['rows'][0]['details']['after_cost_cents'], 11000)
        self.assertEqual(self.query(search='CUP')['rows'][0]['stock_cost_amount'], '440.00')
        self.assertEqual(self.query(view='cost_history', search='IN-CUP')['total_rows'], 4)

    def test_explicit_historical_inventory_and_daily_monthly(self):
        args = dict(start='2000-01-01', end='2000-01-02', as_of='2000-01-03T00:00:00+08:00')
        expected = report(ReadOnlyStore(self.path), **args)
        historical = self.query(view='report', **args)
        self.assertEqual(historical['rows'], expected['inventory'])
        self.assertFalse(historical['data_complete'])
        for view, key in [('report_daily', 'daily'), ('report_monthly', 'monthly')]:
            self.assertEqual(self.query(view=view, **args)['rows'], expected[key])

    def test_tool_permission_and_model_evidence(self):
        service = ChatService(self.path, Path(self.temp.name) / 'ai')
        service.save(dict(model='fixture', effort='low', enabled_tools=['inventory']))
        service.runtime = Mock()
        service.runtime.rpc.return_value = {'account': {'type': 'chatgpt'}}
        call = dict(tool='inventory', view='summary', start='', end='', status='all', account_id='', search='', offset=0, limit=50, as_of='')
        service.runtime.generate.side_effect = [json.dumps(dict(action='query', calls=[call], message='')), '有 4 件庫存']
        result = service.chat({'question': '現在有幾種商品及各有幾件？'})
        self.assertEqual(result['evidence'][0]['totals']['on_hand'], 4)
        answer_call = service.runtime.generate.call_args_list[1]
        self.assertIn('伺服器已在本次問題中執行', answer_call.args[2])
        self.assertEqual(json.loads(answer_call.args[3])['evidence'][0]['totals']['on_hand'], 4)
        service.save(dict(model='fixture', effort='low', enabled_tools=[]))
        service.runtime.generate.side_effect = [json.dumps(dict(action='query', calls=[call], message=''))]
        with self.assertRaisesRegex(ValueError, '停用'): service.chat({'question': '庫存多少？'})
        self.assertEqual(ChatService(self.path, service.directory).settings()['enabled_tools'], [])

    def test_separate_readonly_source_never_initialized_or_falls_back(self):
        from ops.server import create_server
        before = self.path.read_bytes()
        ui_database = Path(self.temp.name) / 'ui.sqlite3'
        server = create_server(ui_database, port=0, ai_database=self.path)
        try:
            self.assertEqual(server.chat.database, self.path)
            self.assertEqual(query_inventory(server.chat.database, {})['totals']['on_hand'], 4)
            self.assertEqual(self.path.read_bytes(), before)
        finally:
            server.server_close()
        missing = Path(self.temp.name) / 'missing.sqlite3'
        with self.assertRaisesRegex(ValueError, '不存在'):
            create_server(ui_database, port=0, ai_database=missing)
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
