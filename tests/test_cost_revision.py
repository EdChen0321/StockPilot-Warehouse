"""Receipt cost corrections propagate through actual units, without rewriting facts."""
import csv
import io
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ops.catalogue import Catalogue
from ops.goods_purchases import GoodsPurchases
from ops.locations import Locations
from ops.materials import Materials
from ops.shipping_statistics import shipping_statistics
from ops.stock import Stock
from ops.stock import initialize as initialize_stock
from ops.stock_reports import export_report, report
from ops.store import ConflictError, Store


class CostRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'cost-revision.sqlite3')
        self.stock = Stock(self.store)
        self.product = Catalogue(self.store).create(dict(
            operation_id='product', name='合成成本驗證商品', sku='COST', barcode='COST-001'))
        self.receipt = self.stock.receipt(dict(operation_id='receipt', number='COST-IN',
            freight='0', allocation='quantity', lines=[dict(
                product_id=self.product['id'], quantity=10, unit_price='100')]))
        self.stock.confirm_receipt(self.receipt['id'], dict(operation_id='receive'))
        self.shipment = self.stock.shipment(dict(operation_id='shipment', entry_number='COST-OUT'))
        for index in range(4):
            self.stock.scan(self.shipment['id'], dict(operation_id=f'scan-{index}', barcode='COST-001'))
        self.stock.transition(self.shipment['id'], 'complete', dict(operation_id='ship'))
        # Put the actual movements in an earlier period: later fee correction must
        # change historical cost reports without changing the movement dates.
        with self.store.connect() as db:
            db.execute("UPDATE stock_events SET time='2026-01-01T00:00:00+00:00' WHERE kind='receipt'")
            db.execute("UPDATE stock_events SET time='2026-01-02T00:00:00+00:00' WHERE kind IN ('allocate','ship','deduct')")

    def correction(self, **changes):
        body = dict(operation_id='revise-cost', expected_cost_version=0,
            reason='補登本批稅費', lines=[dict(product_id=self.product['id'], unit_price='100')],
            freight='0', tax='50', allocation='quantity')
        body.update(changes)
        return body

    def business_state(self):
        with self.store.connect() as db:
            return {
                name: [tuple(row) for row in db.execute(f'SELECT * FROM {name} ORDER BY rowid')]
                for name in ('receipts', 'stock_units', 'stock_events', 'shipments')
            }

    def test_cost_correction_reaches_shipped_units_historical_reports_and_csv(self):
        other = self.stock.receipt(dict(operation_id='other-receipt', number='OTHER-IN',
            freight='0', allocation='quantity', lines=[dict(
                product_id=self.product['id'], quantity=1, unit_price='77')]))
        self.stock.confirm_receipt(other['id'], dict(operation_id='other-receive'))
        before = self.stock.snapshot()
        original_units = {u['id']: u for u in before['units']}
        original_events = {e['id']: e for e in before['events']}
        self.stock.revise_receipt_cost(self.receipt['id'], self.correction())
        after = self.stock.snapshot()
        batch = [u for u in after['units'] if u['receipt_id'] == self.receipt['id']]
        self.assertEqual([u['cost_cents'] for u in batch], [10500] * 10)
        self.assertEqual(sum(u['cost_cents'] for u in batch if u['location'] == 'warehouse'), 63000)
        self.assertEqual(sum(u['cost_cents'] for u in batch if u['location'] == 'customer'), 42000)
        self.assertEqual(next(u['cost_cents'] for u in after['units'] if u['receipt_id'] == other['id']), 7700)
        for unit in after['units']:
            for field in ('id', 'receipt_id', 'product_id', 'fifo_at', 'first_received_at', 'location', 'quality', 'packaging'):
                self.assertEqual(unit[field], original_units[unit['id']][field])
        current_events = {e['id']: e for e in after['events']}
        for event_id, event in original_events.items():
            self.assertEqual(current_events[event_id], event)
        receipt = next(r for r in after['receipts'] if r['id'] == self.receipt['id'])
        self.assertEqual(receipt['cost_version'], 1)
        historical = shipping_statistics(self.store, start='2026-01-01', end='2026-01-31')
        self.assertEqual(historical['totals']['shipped_cost'], '420.00')
        self.assertEqual(historical['shipping_details'][0]['shipped_cost'], '420.00')
        inventory = report(self.store, start='2026-01-01', end='2026-01-31')
        self.assertEqual(inventory['totals']['received_cost'], '1050.00')
        self.assertEqual(inventory['totals']['shipped_cost'], '420.00')
        rows = list(csv.DictReader(io.StringIO(export_report(historical, 'shipping_details'))))
        self.assertEqual(rows[0]['shipped_cost'], '420.00')
        # Read again through a new service instance, not just an in-memory result.
        self.assertEqual(Stock(Store(self.store.path)).snapshot()['units'], after['units'])

    def test_retry_is_idempotent_and_stale_or_reused_operation_is_rejected(self):
        body = self.correction()
        result = self.stock.revise_receipt_cost(self.receipt['id'], body)
        before_retry = self.business_state()
        self.assertEqual(self.stock.revise_receipt_cost(self.receipt['id'], body), result)
        self.assertEqual(self.business_state(), before_retry)
        with self.assertRaises(ConflictError):
            self.stock.revise_receipt_cost(self.receipt['id'], self.correction(operation_id='stale'))
        with self.assertRaises(ConflictError):
            self.stock.revise_receipt_cost(self.receipt['id'], self.correction(tax='60'))
        self.assertEqual(self.business_state(), before_retry)

    def test_unit_cost_history_reads_saved_revisions_without_rewriting_events(self):
        original = self.business_state()['stock_events']
        self.stock.revise_receipt_cost(self.receipt['id'], self.correction())
        snapshot = self.stock.snapshot()
        history = snapshot['unit_cost_revisions']
        self.assertEqual(len(history), 10)
        self.assertEqual({e['unit_id'] for e in history}, {u['id'] for u in snapshot['units']})
        for event in history:
            self.assertEqual(event['details']['before_cost_cents'], 10000)
            self.assertEqual(event['details']['after_cost_cents'], 10500)
            self.assertEqual(event['details']['reason'], '補登本批稅費')
            self.assertEqual(event['time'], snapshot['cost_revisions'][0]['time'])
        self.assertEqual(self.business_state()['stock_events'], original)
        self.assertEqual(Stock(Store(self.store.path)).snapshot()['unit_cost_revisions'], history)

    def test_unit_cost_history_preserves_rounding_pending_zero_and_multiple_versions(self):
        other = self.stock.receipt(dict(operation_id='other', number='OTHER', freight='0',
            allocation='quantity', lines=[dict(product_id=self.product['id'], quantity=1, unit_price='7')]))
        self.stock.confirm_receipt(other['id'], dict(operation_id='other-confirm'))
        self.stock.revise_receipt_cost(self.receipt['id'], self.correction(tax='0.03'))
        snapshot = self.stock.snapshot()
        costs = {u['id']: u['cost_cents'] for u in snapshot['units']}
        first = snapshot['unit_cost_revisions']
        self.assertEqual(sum(e['details']['after_cost_cents'] for e in first), 100003)
        for event in first:
            self.assertEqual(event['details']['after_cost_cents'], costs[event['unit_id']])
            self.assertEqual(event['receipt_id'], self.receipt['id'])
        self.stock.revise_receipt_cost(self.receipt['id'], self.correction(
            operation_id='pending', expected_cost_version=1, freight=''))
        self.stock.revise_receipt_cost(self.receipt['id'], self.correction(
            operation_id='zero', expected_cost_version=2, freight='0', tax='0',
            lines=[dict(product_id=self.product['id'], unit_price='0')]))
        history = self.stock.snapshot()['unit_cost_revisions']
        self.assertEqual(len(history), 30)
        self.assertEqual(len({e['id'] for e in history}), 30)
        for event in history[:10]:
            self.assertIsNone(event['details']['before_cost_cents'])
            self.assertEqual(event['details']['after_cost_cents'], 0)
        self.assertEqual(history[20:], first)

    def test_failure_midway_rolls_back_receipt_units_and_events(self):
        target = self.stock.snapshot()['units'][4]['id']
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_cost_update BEFORE UPDATE OF cost_cents ON stock_units "
                       "WHEN OLD.id='" + target + "' BEGIN SELECT RAISE(ABORT,'injected cost failure'); END")
        before = self.business_state()
        with self.assertRaises(sqlite3.IntegrityError):
            self.stock.revise_receipt_cost(self.receipt['id'], self.correction())
        self.assertEqual(self.business_state(), before)
        with self.store.connect() as db:
            self.assertIsNone(db.execute('SELECT id FROM operations WHERE id=?', ('revise-cost',)).fetchone())
            db.execute('DROP TRIGGER fail_cost_update')
        self.stock.revise_receipt_cost(self.receipt['id'], self.correction())
        self.assertEqual({u['cost_cents'] for u in self.stock.snapshot()['units']}, {10500})

    def test_correction_reaches_returns_without_rewriting_return_event(self):
        unit = next(u for u in self.stock.snapshot()['units'] if u['location'] == 'customer')
        self.stock.receive_return(unit['id'], dict(operation_id='return', shipment_id=self.shipment['id'], reason='合成實退'))
        with self.store.connect() as db:
            db.execute("UPDATE stock_events SET time='2026-02-02T00:00:00+00:00' WHERE kind='return'")
        return_event = next(e for e in self.stock.snapshot()['events'] if e['kind'] == 'return')
        self.stock.revise_receipt_cost(self.receipt['id'], self.correction())
        returned = shipping_statistics(self.store, start='2026-02-01', end='2026-02-28')
        self.assertEqual(returned['totals']['returned_cost'], '105.00')
        self.assertEqual(returned['totals']['shipped'], 0)
        self.assertEqual(next(e for e in self.stock.snapshot()['events'] if e['id'] == return_event['id']), return_event)

    def test_explicit_zero_cost_is_valid_and_bad_denominator_does_not_mutate(self):
        zero = self.correction(lines=[dict(product_id=self.product['id'], unit_price='0')], tax='0')
        self.stock.revise_receipt_cost(self.receipt['id'], zero)
        self.assertEqual({u['cost_cents'] for u in self.stock.snapshot()['units']}, {0})
        before = self.business_state()
        with self.assertRaises(ValueError):
            self.stock.revise_receipt_cost(self.receipt['id'], self.correction(
                operation_id='bad-denominator', expected_cost_version=1,
                lines=[dict(product_id=self.product['id'], unit_price='0')],
                allocation='value', freight='1', tax='0'))
        self.assertEqual(self.business_state(), before)


class PendingCostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'pending-cost.sqlite3')
        self.stock = Stock(self.store)
        self.product = Catalogue(self.store).create(dict(
            operation_id='product', name='合成待填費用商品', sku='PENDING', barcode='PENDING'))
        self.today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()

    def receive(self, price='', freight='', tax='', quantity=2):
        receipt = self.stock.receipt(dict(operation_id='receipt', number='PENDING-IN',
            freight=freight, tax=tax, allocation='quantity', lines=[dict(
                product_id=self.product['id'], quantity=quantity, unit_price=price)]))
        self.stock.confirm_receipt(receipt['id'], dict(operation_id='receive'))
        return receipt

    def correction(self, **changes):
        body = dict(operation_id='fill-cost', expected_cost_version=0, reason='補填費用',
            lines=[dict(product_id=self.product['id'], unit_price='10')], freight='0', tax='0', allocation='quantity')
        body.update(changes)
        return body

    def place(self):
        locations = Locations(self.store)
        locations.enable(dict(operation_id='enable-places'))
        place = locations.save(dict(operation_id='place', address='1F', position='A', name='測試櫃',
            can_import=True, can_export=True, can_return=True, entry_state='import'))
        return locations, place

    def test_unknown_cost_stays_unknown_in_snapshot_reports_and_csv(self):
        self.receive()
        snapshot = self.stock.snapshot()
        self.assertTrue(all(u['cost_cents'] is None for u in snapshot['units']))
        self.assertIsNone(snapshot['products'][0]['stock_cost_amount'])
        self.assertEqual(snapshot['receipts'][0]['cost_status'], 'pending')
        inventory = report(self.store, start=self.today, end=self.today)
        self.assertIsNone(inventory['inventory'][0]['stock_cost'])
        self.assertEqual(inventory['inventory'][0]['stock_cost_pending'], 2)
        inventory_csv = list(csv.DictReader(io.StringIO(export_report(inventory, 'inventory'))))[0]
        self.assertEqual(inventory_csv['stock_cost'], '')
        self.assertEqual(inventory_csv['stock_cost_pending'], '2')
        shipment = self.stock.shipment(dict(operation_id='shipment', entry_number='PENDING-OUT'))
        self.stock.scan(shipment['id'], dict(operation_id='scan', barcode='PENDING'))
        self.stock.transition(shipment['id'], 'complete', dict(operation_id='ship'))
        shipping = shipping_statistics(self.store, start=self.today, end=self.today)
        self.assertIsNone(shipping['totals']['shipped_cost'])
        self.assertEqual(shipping['totals']['shipped_cost_pending'], 1)
        shipping_csv = list(csv.DictReader(io.StringIO(export_report(shipping, 'shipping_details'))))[0]
        self.assertEqual(shipping_csv['shipped_cost'], '')
        self.assertEqual(shipping_csv['shipped_cost_pending'], '1')
        totals = report(self.store, start=self.today, end=self.today)
        totals_csv = list(csv.DictReader(io.StringIO(export_report(totals, 'totals'))))[0]
        self.assertEqual(totals_csv['received_cost_pending'], '2')
        self.assertEqual(totals_csv['shipped_cost_pending'], '1')

    def test_pending_putaway_needs_ack_and_rejects_ack_for_old_cost_version(self):
        receipt = self.receive()
        locations, place = self.place()
        body = dict(receipt_id=receipt['id'], place_id=place['id'], quantity=1)
        for index, extra in enumerate(({}, {'cost_acknowledged': True},
                {'cost_acknowledged': False, 'expected_cost_version': 0})):
            with self.subTest(extra=extra), self.assertRaises(ConflictError):
                locations.assign(dict(operation_id=f'blocked-{index}', **body, **extra))
        locations.assign(dict(operation_id='ack', cost_acknowledged=True, expected_cost_version=0, **body))
        self.assertEqual(self.stock.snapshot()['receipts'][0]['cost_status'], 'pending')
        self.stock.revise_receipt_cost(receipt['id'], self.correction())
        with self.assertRaises(ConflictError):
            locations.assign(dict(operation_id='stale-ack', cost_acknowledged=True, expected_cost_version=0, **body))
        locations.assign(dict(operation_id='current-ack', cost_acknowledged=True, expected_cost_version=1, **body))
        self.assertTrue(all(u['place_id'] == place['id'] for u in self.stock.snapshot()['units']))

    def test_explicit_zero_putaway_needs_ack_but_is_complete(self):
        receipt = self.receive(price='0', freight='0', tax='0')
        locations, place = self.place()
        body = dict(receipt_id=receipt['id'], place_id=place['id'])
        self.assertEqual(self.stock.snapshot()['receipts'][0]['cost_status'], 'complete')
        self.assertEqual(self.stock.snapshot()['products'][0]['stock_cost_amount'], '0.00')
        with self.assertRaises(ConflictError):
            locations.assign(dict(operation_id='no-ack', **body))
        locations.assign(dict(operation_id='ack-zero', cost_acknowledged=True, expected_cost_version=0, **body))
        self.assertTrue(all(u['cost_cents'] == 0 for u in self.stock.snapshot()['units']))

    def test_positive_cost_putaway_preserves_existing_api(self):
        receipt = self.receive(price='10', freight='0', tax='0')
        locations, place = self.place()
        locations.assign(dict(operation_id='positive-assign', receipt_id=receipt['id'], place_id=place['id']))
        self.assertTrue(all(u['place_id'] == place['id'] for u in self.stock.snapshot()['units']))

    def test_goods_purchase_can_receive_without_cost_and_fill_receipt_later(self):
        supplier = Materials(self.store).supplier(dict(operation_id='supplier', name='合成供應商'))
        purchases = GoodsPurchases(self.store)
        purchase = purchases.save(dict(operation_id='purchase', number='PENDING-PO', supplier_id=supplier['id'],
            lines=[dict(product_id=self.product['id'], quantity=2)]))
        purchase = purchases.action(purchase['id'], 'confirm', dict(operation_id='order', expected_version=purchase['version']))
        purchases.action(purchase['id'], 'receive', dict(operation_id='po-receive', expected_version=purchase['version'],
            number='PO-IN', freight='', allocation='quantity',
            lines=[dict(product_id=self.product['id'], quantity=2, unit_price='')]))
        snapshot = self.stock.snapshot()
        receipt = snapshot['receipts'][0]
        self.assertEqual(receipt['purchase_id'], purchase['id'])
        self.assertEqual(receipt['cost_status'], 'pending')
        self.assertTrue(all(u['cost_cents'] is None for u in snapshot['units']))
        self.stock.revise_receipt_cost(receipt['id'], self.correction())
        snapshot = self.stock.snapshot()
        self.assertEqual(snapshot['receipts'][0]['cost_status'], 'complete')
        self.assertEqual([u['cost_cents'] for u in snapshot['units']], [1000, 1000])

    def test_same_sku_multiple_lines_keep_unit_identity_and_stable_rounding(self):
        lines = [dict(product_id=self.product['id'], quantity=2, unit_price='1'),
                 dict(product_id=self.product['id'], quantity=1, unit_price='3')]
        receipt = self.stock.receipt(dict(operation_id='multi', number='MULTI', lines=lines,
            freight='0.03', tax='0', allocation='value'))
        self.stock.confirm_receipt(receipt['id'], dict(operation_id='multi-receive'))
        with self.store.connect() as db:
            original = [dict(u) for u in db.execute('SELECT * FROM stock_units ORDER BY rowid')]
        correction = self.correction(lines=[dict(product_id=line['product_id'], unit_price=line['unit_price']) for line in lines],
            freight='0.03', tax='0.02', allocation='value')
        self.stock.revise_receipt_cost(receipt['id'], correction)
        with self.store.connect() as db:
            first = [dict(u) for u in db.execute('SELECT * FROM stock_units ORDER BY rowid')]
        self.assertEqual([u['cost_cents'] for u in first], [101, 101, 303])
        self.assertEqual(sum(u['cost_cents'] for u in first), 505)
        self.assertEqual([u['id'] for u in first], [u['id'] for u in original])
        self.stock.revise_receipt_cost(receipt['id'], {**correction, 'operation_id': 'same-values', 'expected_cost_version': 1})
        with self.store.connect() as db:
            again = [dict(u) for u in db.execute('SELECT * FROM stock_units ORDER BY rowid')]
        self.assertEqual(first, again)

    def test_concurrent_corrections_only_one_version_wins(self):
        receipt = self.receive(price='10', freight='0', tax='0')
        def run(index):
            try:
                result = self.stock.revise_receipt_cost(receipt['id'], self.correction(
                    operation_id=f'concurrent-{index}', tax=str(index + 1)))
                return result['cost_version']
            except ConflictError:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(run, range(2)))
        self.assertCountEqual(outcomes, [1, 'conflict'])
        current = self.stock.snapshot()['receipts'][0]
        self.assertEqual(current['cost_version'], 1)
        self.assertIn(sum(u['cost_cents'] for u in self.stock.snapshot()['units']), (2100, 2200))


class LegacyCostMigrationTests(unittest.TestCase):
    def test_legacy_rows_keep_values_and_explicit_zero_is_not_marked_unknown(self):
        # Schema intentionally predates cost versions and known/pending flags.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'legacy.sqlite3'
            with closing(sqlite3.connect(path)) as db:
                db.row_factory = sqlite3.Row
                db.executescript('''
                    CREATE TABLE products(id TEXT PRIMARY KEY,name TEXT,kind TEXT);
                    CREATE TABLE receipts(id TEXT PRIMARY KEY,number TEXT NOT NULL UNIQUE,state TEXT NOT NULL,
                        lines TEXT NOT NULL,freight_cents INTEGER NOT NULL,allocation TEXT NOT NULL,
                        created_at TEXT NOT NULL,confirmed_at TEXT,source TEXT NOT NULL DEFAULT '',comment TEXT NOT NULL DEFAULT '');
                    CREATE TABLE stock_units(id TEXT PRIMARY KEY,product_id TEXT NOT NULL REFERENCES products(id),
                        receipt_id TEXT NOT NULL REFERENCES receipts(id),first_received_at TEXT NOT NULL,fifo_at TEXT NOT NULL,
                        location TEXT NOT NULL,quality TEXT NOT NULL,packaging TEXT NOT NULL,cost_cents INTEGER NOT NULL);
                    INSERT INTO products VALUES('p','合成舊商品','product');
                    INSERT INTO receipts VALUES('r','LEGACY','confirmed','[{"product_id":"p","quantity":1,"unit_price_cents":100}]',3,'quantity','2026-01-01','2026-01-02','舊來源','舊備註');
                    INSERT INTO receipts VALUES('z','ZERO','confirmed','[{"product_id":"p","quantity":1,"unit_price_cents":0}]',0,'quantity','2026-01-01','2026-01-02','','');
                    INSERT INTO stock_units VALUES('u','p','r','2026-01-02','2026-01-02','customer','good','good',103);
                    INSERT INTO stock_units VALUES('zero','p','z','2026-01-02','2026-01-02','warehouse','good','good',0);
                ''')
                originals = {table: [dict(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                             for table in ('products', 'receipts', 'stock_units')}
                initialize_stock(db)
                initialize_stock(db)
                for table, rows in originals.items():
                    current = [dict(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                    self.assertEqual(len(rows), len(current))
                    self.assertEqual(rows, [{key: row[key] for key in rows[0]} for row in current])
                for row in db.execute('SELECT * FROM receipts'):
                    self.assertEqual(row['cost_version'], 0)
                    self.assertEqual(row['cost_status'], 'complete')
                    self.assertEqual(row['freight_known'], 1)
                    self.assertEqual(row['tax_known'], 1)
                    self.assertEqual(row['tax_cents'], 0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM receipt_cost_revisions').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
