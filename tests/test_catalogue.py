import tempfile
import unittest
from pathlib import Path

from ops.store import Store, ConflictError
from ops.catalogue import Catalogue


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'db.sqlite3')
        self.catalogue = Catalogue(self.store)

    def create(self, **changes):
        body = dict(operation_id='create-1', name='合成收納盒', sku='BOX-001', kind='product', barcode='00001234')
        body.update(changes)
        return self.catalogue.create(body)

    def test_create_retry_and_history_search(self):
        product = self.create()
        self.assertEqual(product['barcode'], '00001234')
        self.assertEqual(self.create(), product)
        with self.assertRaises(ConflictError):
            self.create(name='different')
        changed = self.catalogue.update(product['id'], dict(operation_id='rename', expected_version=1,
                                        name='新版盒子', reason='改名', active=False))
        self.assertEqual(changed['version'], 2)
        self.assertEqual(self.catalogue.list('合成收納盒')['products'][0]['id'], product['id'])
        self.assertFalse(changed['active'])
        with self.assertRaises(ConflictError):
            self.catalogue.update(product['id'], dict(operation_id='stale', expected_version=1, name='錯誤'))
        self.assertEqual(len(self.catalogue.detail(product['id'])['versions']), 2)

    def test_unique_and_atomic_event_failure(self):
        self.create()
        with self.assertRaises(ConflictError):
            self.create(operation_id='dup', sku='different')
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(Exception):
            self.create(operation_id='rollback', sku='other', barcode='00009999')
        self.assertEqual(len(self.catalogue.list()['products']), 1)

    def test_seed_migration_preserves_edits(self):
        self.store.seed()
        p = self.catalogue.detail('P-BOX')
        self.catalogue.update(p['id'], dict(operation_id='rename', expected_version=p['version'], name='我的盒子', reason='改名'))
        self.store.seed()
        second = Catalogue(Store(self.store.path))
        self.assertEqual(second.detail('P-BOX')['name'], '我的盒子')
        self.assertEqual(len(second.detail('P-BOX')['versions']), 2)

    def test_attributes(self):
        self.catalogue.define_attribute(dict(operation_id='def', key='material', label='材質', kind='choice', options=['布', '紙']))
        p = self.create(attributes={'material': '紙'})
        self.assertEqual(p['attributes'], {'material': '紙'})
        with self.assertRaises(ValueError):
            self.catalogue.update(p['id'], dict(operation_id='bad', expected_version=1, attributes={'material': '鋼'}))

    def test_empty_database_user_records_persist(self):
        self.assertEqual(self.catalogue.list()['products'], [])
        self.assertEqual(self.store.bootstrap()['accounts'], [])
        p = self.create()
        account = self.catalogue.account(dict(operation_id='source', name='我的線下店', platform='manual'))
        reopened = Catalogue(Store(self.store.path))
        self.assertEqual(reopened.detail(p['id'])['sku'], 'BOX-001')
        self.assertEqual(reopened.store.bootstrap()['accounts'][0]['id'], account['id'])
        changed = reopened.update(p['id'], dict(operation_id='after-reopen', expected_version=1, specification='小號'))
        self.assertEqual(changed['specification'], '小號')
        self.assertEqual(reopened.account(dict(operation_id='rename-source', id=account['id'], name='門市訂單'))['name'], '門市訂單')

    def test_mapping_and_bundle_versions(self):
        self.store.seed()
        self.catalogue.mapping(dict(operation_id='map-v2', account_id='demo-sl', external_sku='SL-BOX',
                                    product_id='P-POUCH', effective_from='2026-11-01T00:00:00+08:00'))
        with self.store.connect() as db:
            old = self.catalogue.resolve(db, 'demo-sl', 'SL-BOX', '2026-10-01T00:00:00+00:00')
            new = self.catalogue.resolve(db, 'demo-sl', 'SL-BOX', '2026-11-02T00:00:00+00:00')
        self.assertEqual(old['product_id'], 'P-BOX')
        self.assertEqual(new['product_id'], 'P-POUCH')
        p = self.catalogue.detail('SET-01')
        body = dict(operation_id='bundle-v2', expected_version=p['version'], effective_from='2026-11-01T00:00:00+08:00',
                    components=[dict(product_id='P-BOX', quantity=1), dict(product_id='P-POUCH', quantity=1)])
        updated = self.catalogue.bundle('SET-01', body)
        self.assertEqual(self.catalogue.bundle('SET-01', body), updated)
        with self.store.connect() as db:
            old = self.catalogue.resolve(db, 'demo-sp', 'SP-SET', '2026-10-01T00:00:00+00:00')
            new = self.catalogue.resolve(db, 'demo-sp', 'SP-SET', '2026-11-02T00:00:00+00:00')
        self.assertEqual(old['components'][0]['quantity'], 2)
        self.assertEqual(new['components'][0]['quantity'], 1)

    def test_reject_invalid_bundle(self):
        self.store.seed()
        with self.assertRaises(ValueError):
            self.catalogue.bundle('SET-01', dict(operation_id='bad', expected_version=1,
                effective_from='2026-11-01T00:00:00Z', components=[dict(product_id='SET-01', quantity=1)]))


if __name__ == '__main__':
    unittest.main()
