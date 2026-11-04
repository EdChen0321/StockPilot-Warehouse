import tempfile
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import unittest
from pathlib import Path
from test_orders import row, as_csv
from ops.store import Store


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.db'
        self.store = Store(self.path)
        self.store.seed()

    def tearDown(self):
        self.tmp.cleanup()

    def upload(self, *rows, account='demo-sl', confirm=True):
        batch = self.store.preview(account, 'test.csv', as_csv(rows), True)
        return self.store.confirm(batch['id']) if confirm else batch

    def current(self):
        return self.store.current_orders()

    def test_seed_idempotent_and_persistent_without_orders(self):
        self.store.seed()
        boot = Store(self.path).bootstrap()
        self.assertEqual(len(boot['products']), 3)
        self.assertEqual(len(boot['mappings']), 3)
        self.assertEqual(self.current(), [])

    def test_preview_does_not_post_and_confirm_is_idempotent(self):
        batch = self.upload(row(), confirm=False)
        self.assertEqual(self.current(), [])
        result = self.store.confirm(batch['id'])
        self.assertEqual(result['results'][0]['outcome'], 'applied')
        self.assertEqual(self.store.confirm(batch['id']), result)
        self.assertEqual(len(Store(self.path).current_orders()), 1)

    def test_revisions_duplicates_and_cancel_preserve_history(self):
        self.upload(row())
        self.upload(row(), account='demo-sp')  # Same master SKU is valid in both sources.
        self.assertEqual(len(self.current()), 2)
        self.upload(row(source_revision='2', quantity='3', line_item_amount='300', order_item_amount='300'))
        self.upload(row())
        self.assertEqual(next(o for o in self.current() if o['account_id'] == 'demo-sl')['revision'], 2)
        conflict = self.upload(row(source_revision='2', item_name='另一名稱'))
        self.assertEqual(conflict['results'][0]['outcome'], 'pending')
        self.upload(row(source_revision='3', raw_status='cancelled'))
        self.assertEqual(next(o for o in self.current() if o['account_id'] == 'demo-sl')['status'], 'cancelled')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM order_versions').fetchone()[0], 4)

    def test_missing_master_sku_reprocess_and_trace(self):
        batch = self.upload(row(external_sku='NEW'))
        self.assertEqual(self.current(), [])
        self.store.add_mapping('demo-sl', 'NEW', 'P-BOX')
        self.assertEqual(self.store.reprocess(batch['id'])['results'][0]['outcome'], 'pending')
        from ops.catalogue import Catalogue
        Catalogue(self.store).create(dict(operation_id='new-product', name='合成新品', sku='NEW'))
        new = self.store.reprocess(batch['id'])
        self.assertEqual(new['parent_id'], batch['id'])
        self.assertEqual(self.current(), [])
        self.store.confirm(new['id'])
        self.assertEqual(len(self.current()), 1)
        self.assertEqual(self.store.pending_count(), 0)
        self.assertTrue(self.store.batch(batch['id'])['results'][0]['errors'])
        self.assertEqual(self.store.batch(batch['id'])['raw_rows'][0]['row_index'], 2)
        with self.assertRaises(ValueError):
            self.store.add_mapping('demo-sl', 'NEW', 'P-POUCH')

    def test_confirm_rechecks_and_mixed_batch_is_order_atomic(self):
        draft = self.upload(row(), confirm=False)
        self.upload(row(source_revision='2'))
        self.assertEqual(self.store.confirm(draft['id'])['results'][0]['outcome'], 'stale')
        batch = self.upload(row(order_id='BAD', expected_line_count='2'), row(order_id='GOOD'))
        self.assertEqual([r['outcome'] for r in batch['results']], ['pending', 'applied'])
        self.assertEqual({o['order_id'] for o in self.current()}, {'O-001', 'GOOD'})

    def test_source_updated_at_cannot_go_backwards(self):
        self.upload(row(source_updated_at='2026-01-05T00:00:00+08:00'))
        batch = self.upload(row(source_revision='2', source_updated_at='2026-01-04T00:00:00+08:00'))
        self.assertEqual(batch['results'][0]['outcome'], 'pending')
        self.assertEqual(self.current()[0]['revision'], 1)

    def test_preview_requires_account_complete_snapshot_and_types(self):
        for account, filename, text, complete in [('fake', 'x.csv', as_csv([row()]), True),
                                                 ('demo-sl', 'x.csv', as_csv([row()]), False),
                                                 ('demo-sl', [], as_csv([row()]), True)]:
            with self.subTest(account=account, complete=complete), self.assertRaises(ValueError):
                self.store.preview(account, filename, text, complete)

    def test_concurrent_confirmations_post_once(self):
        batch = self.upload(row(), confirm=False)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.store.confirm(batch['id']), range(4)))
        self.assertTrue(all(r == results[0] for r in results))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM order_versions').fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE action='confirm'").fetchone()[0], 1)

    def test_transaction_rolls_back_when_required_audit_fails(self):
        batch = self.upload(row(), confirm=False)
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER reject_confirm BEFORE INSERT ON events WHEN NEW.action='confirm' BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.confirm(batch['id'])
        self.assertEqual(self.current(), [])
        self.assertEqual(self.store.batch(batch['id'])['state'], 'draft')
        with self.store.connect() as db:
            db.execute('DROP TRIGGER reject_confirm')
        self.store.confirm(batch['id'])
        self.assertEqual(len(self.current()), 1)

    def test_renamed_or_reordered_file_deduplicates_business_key(self):
        original = self.upload(row(), confirm=False)
        renamed = self.store.preview('demo-sl', 'renamed.csv', as_csv([row()]), True)
        self.assertEqual(original['id'], renamed['id'])
        self.store.confirm(original['id'])
        # A distinct CSV representation produces a new batch but not a second order.
        batch = self.store.preview('demo-sl', 'newline.csv', as_csv([row()]).replace('\r\n', '\n'), True)
        self.assertEqual(self.store.confirm(batch['id'])['results'][0]['outcome'], 'duplicate')
        self.assertEqual(len(self.current()), 1)

    def test_corrected_invalid_snapshot_resolves_pending_without_erasing_history(self):
        bad = self.upload(row(expected_line_count='2'))
        self.assertEqual(self.store.pending_count(), 1)
        self.upload(row())
        self.assertEqual(self.store.pending_count(), 0)
        self.assertTrue(self.store.batch(bad['id'])['results'][0]['errors'])

    def test_newer_pending_not_hidden_by_old_valid_or_duplicate(self):
        self.upload(row(source_revision='3', expected_line_count='2'))
        self.upload(row())
        self.assertEqual(self.store.pending_count(), 1)
        self.upload(row(source_revision='1', item_name='衝突'))
        self.upload(row())
        self.assertEqual(self.store.pending_count(), 1)


if __name__ == '__main__':
    unittest.main()
