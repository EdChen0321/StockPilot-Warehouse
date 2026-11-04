"""SQLite ownership boundary: atomic confirmations and immutable source snapshots."""
import copy
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .importing import parse_orders


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def required_text(value, label, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{label}須為非空文字，最多{limit}字元')
    return value.strip()


class ConflictError(ValueError):
    pass


class Store:
    def __init__(self, path, *, initialize=True):
        self.path = Path(path)
        if not initialize:
            if not self.path.is_file():
                raise ValueError('工作資料庫不存在，不會自動建立')
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, platform TEXT NOT NULL, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('product','bundle')));
                CREATE TABLE IF NOT EXISTS components(bundle_id TEXT REFERENCES products(id),
                    product_id TEXT REFERENCES products(id), quantity INTEGER CHECK(quantity>0), version INTEGER,
                    PRIMARY KEY(bundle_id, product_id, version));
                CREATE TABLE IF NOT EXISTS mappings(account_id TEXT REFERENCES accounts(id), external_sku TEXT,
                    product_id TEXT NOT NULL REFERENCES products(id), version INTEGER NOT NULL,
                    PRIMARY KEY(account_id,external_sku));
                CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, account_id TEXT REFERENCES accounts(id),
                    filename TEXT NOT NULL, file_hash TEXT NOT NULL, csv_text TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('draft','confirmed')), parent_id TEXT REFERENCES batches(id),
                    created_at TEXT NOT NULL, results TEXT NOT NULL, raw_rows TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS batch_hash ON batches(account_id,file_hash);
                CREATE TABLE IF NOT EXISTS order_versions(account_id TEXT REFERENCES accounts(id), order_id TEXT,
                    revision INTEGER CHECK(revision>=0), fingerprint TEXT NOT NULL, normalized TEXT NOT NULL,
                    batch_id TEXT NOT NULL REFERENCES batches(id), PRIMARY KEY(account_id,order_id,revision));
                CREATE TABLE IF NOT EXISTS current_orders(account_id TEXT,order_id TEXT,revision INTEGER,
                    PRIMARY KEY(account_id,order_id), FOREIGN KEY(account_id,order_id,revision)
                    REFERENCES order_versions(account_id,order_id,revision));
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,time TEXT NOT NULL,actor TEXT NOT NULL,
                    action TEXT NOT NULL,target TEXT NOT NULL,details TEXT NOT NULL);
            ''')
            from .catalogue import initialize
            initialize(db)
            from .stock import initialize as initialize_stock
            initialize_stock(db)
            from .locations import initialize as initialize_locations
            initialize_locations(db)
            from .stock_reports import initialize as initialize_reports
            initialize_reports(db)
            from .materials import initialize as initialize_materials
            initialize_materials(db)
            from .goods_purchases import initialize as initialize_goods_purchases
            initialize_goods_purchases(db)
            from .kol import initialize as initialize_kol
            initialize_kol(db)
            from .kol_import import initialize as initialize_kol_import
            initialize_kol_import(db)
            from .order_excel import initialize as initialize_order_excel
            initialize_order_excel(db)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def event(db, action, target, details):
        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',
                   (uuid4().hex, now(), 'local-demo', action, target, encode(details)))

    def seed(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.executemany('INSERT OR IGNORE INTO accounts VALUES(?,?,?)',
                           [('demo-sl', 'SHOPLINE', '合成SHOPLINE店'), ('demo-sp', 'Shopee', '合成蝦皮店')])
            db.executemany('INSERT OR IGNORE INTO products VALUES(?,?,?)',
                           [('P-BOX', '小收納盒', 'product'), ('P-POUCH', '收納袋', 'product'), ('SET-01', '收納套組', 'bundle')])
            db.executemany('INSERT OR IGNORE INTO components VALUES(?,?,?,?)',
                           [('SET-01', 'P-BOX', 2, 1), ('SET-01', 'P-POUCH', 1, 1)])
            db.executemany('INSERT OR IGNORE INTO mappings VALUES(?,?,?,?)',
                           [('demo-sl', 'SL-BOX', 'P-BOX', 1), ('demo-sl', 'SL-POUCH', 'P-POUCH', 1),
                            ('demo-sp', 'SP-SET', 'SET-01', 1)])
            from .catalogue import migrate
            migrate(db)
            self.event(db, 'seed', 'synthetic-v1', {'orders_created': 0})
        return {'status': 'ready'}

    @staticmethod
    def _batch(db, batch_id):
        row = db.execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()
        if row is None:
            raise KeyError('找不到匯入批次')
        result = dict(row)
        result['results'] = json.loads(result['results'])
        result['raw_rows'] = json.loads(result['raw_rows'])
        del result['csv_text']
        attachment = db.execute('SELECT metadata FROM operational_excel_files WHERE batch_id=?',(batch_id,)).fetchone()
        if attachment:
            metadata = json.loads(attachment['metadata'])
            result.update(format='xlsx',new_products=metadata['new_products'],
                can_confirm=result['state']=='draft' and not any(o['errors'] for o in result['results']))
        return result

    def batch(self, batch_id):
        with self.connect() as db:
            return self._batch(db, batch_id)

    def bootstrap(self, *, summary=False):
        with self.connect() as db:
            result = {key: [dict(row) for row in db.execute(f'SELECT * FROM {key} ORDER BY rowid')]
                      for key in ('accounts', 'products', 'mappings')}
            result['products'] = [dict(row) for row in db.execute('SELECT p.*,d.sku,d.barcode,d.active FROM products p JOIN product_details d ON d.product_id=p.id ORDER BY p.rowid')]
            if summary:
                result['batches'] = [dict(r) for r in db.execute(
                    'SELECT id,account_id,filename,state,created_at FROM batches ORDER BY rowid DESC')]
            else:
                result['batches'] = [self._batch(db, r['id']) for r in db.execute('SELECT id FROM batches ORDER BY rowid DESC')]
                for batch in result['batches']:
                    batch.pop('raw_rows')
        return dict(result, mode='demo', rule_version='sales-v1')

    @staticmethod
    def import_account(db, account_id):
        """Resolve the single shop inside the import transaction."""
        if account_id not in (None, ''):
            account_id = required_text(account_id, '來源帳戶')
            if not db.execute('SELECT 1 FROM accounts WHERE id=?', (account_id,)).fetchone():
                raise ValueError('未知訂單來源')
            return account_id
        accounts = db.execute('SELECT id FROM accounts LIMIT 2').fetchall()
        if len(accounts) > 1:
            raise ValueError('此資料庫存在多個歷史來源，請先確認資料歸屬')
        if accounts:
            return accounts[0]['id']
        account_id = uuid4().hex
        db.execute('INSERT INTO accounts VALUES(?,?,?)', (account_id, 'manual', '我的商店'))
        Store.event(db, 'create_import_account', account_id, {'name': '我的商店'})
        return account_id

    def preview(self, account_id, filename, csv_text, complete_snapshot, parent_id=None):
        filename = required_text(filename, '檔名', 255)
        if complete_snapshot is not True:
            raise ValueError('必須聲明每張訂單為完整快照')
        if not filename.lower().endswith('.csv'):
            raise ValueError('本切片只接受標準CSV')
        raw_rows, orders = parse_orders(csv_text)
        file_hash = hashlib.sha256(csv_text.encode('utf-8')).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            account_id = self.import_account(db, account_id)
            if parent_id is None:
                existing = db.execute('SELECT id FROM batches WHERE account_id=? AND file_hash=? ORDER BY rowid LIMIT 1',
                                      (account_id, file_hash)).fetchone()
                if existing:
                    self.event(db, 'duplicate_upload', existing['id'], {'filename': filename})
                    return self._batch(db, existing['id'])
            batch_id = uuid4().hex
            results = [self._evaluate(db, account_id, order) for order in orders]
            db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (batch_id, account_id, filename, file_hash, csv_text, 'draft', parent_id, now(), encode(results), encode(raw_rows)))
            self.event(db, 'reprocess' if parent_id else 'preview', batch_id,
                       {'parent_id': parent_id, 'format_version': 'orders-v1', 'status_mapping_version': 'status-v1',
                        'complete_snapshot': True, 'results': results})
            return self._batch(db, batch_id)

    def preview_excel(self, account_id, filename, content_base64, complete_snapshot, parent_id=None):
        from .order_excel import preview
        return preview(self,account_id,filename,content_base64,complete_snapshot,parent_id)

    def _evaluate(self, db, account_id, order, new_products=None):
        result = copy.deepcopy(order)
        result['outcome'] = 'pending'
        if result['errors']:
            return result
        current = db.execute('''SELECT v.* FROM current_orders c JOIN order_versions v
                               USING(account_id,order_id,revision) WHERE c.account_id=? AND c.order_id=?''',
                             (account_id, order['order_id'])).fetchone()
        if current and order['revision'] < current['revision']:
            result['outcome'] = 'stale'
            return result
        same = db.execute('SELECT fingerprint FROM order_versions WHERE account_id=? AND order_id=? AND revision=?',
                          (account_id, order['order_id'], order['revision'])).fetchone()
        if same:
            if same['fingerprint'] == order['fingerprint']:
                result['outcome'] = 'duplicate'
            else:
                result['errors'].append('同一來源版本內容不同，需修正來源證據')
            return result
        if current:
            previous = json.loads(current['normalized'])
            old_time, new_time = previous['source_updated_at'], order['source_updated_at']
            if old_time and new_time and new_time < old_time:
                result['errors'].append('來源版本增加但更新時間倒退')
        for line in result['lines']:
            from .catalogue import Catalogue
            if line.get('line_type') == 'non_stock':
                line.update(product_id=None, product_version=None, mapping_version=None, components=[])
                continue
            try:
                if new_products and line['external_sku'] in new_products and not db.execute('SELECT 1 FROM product_details WHERE sku=?',(line['external_sku'],)).fetchone():
                    line.update(product_id=None,product_version=None,mapping_version=None,components=[])
                    result['warnings'].append(f"SKU {line['external_sku']} 將於確認匯入時建立商品主檔；不增加庫存")
                    continue
                line.update(Catalogue.resolve_sku(db, line['external_sku'], order['ordered_at']))
            except ValueError as exc:
                result['errors'].append(f"原始列{line['row_index']}：{exc}")
        if not result['errors']:
            result['outcome'] = 'ready'
        return result

    def confirm(self, batch_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            batch = self._batch(db, batch_id)
            if batch['state'] == 'confirmed':
                return batch
            attachment = db.execute('SELECT * FROM operational_excel_files WHERE batch_id=?',(batch_id,)).fetchone()
            if attachment:
                from .order_excel import prepare_confirmation
                orders = prepare_confirmation(self,db,batch,attachment)
            else:
                text = db.execute('SELECT csv_text FROM batches WHERE id=?', (batch_id,)).fetchone()[0]
                _, orders = parse_orders(text)
            results = []
            for order in orders:
                result = self._evaluate(db, batch['account_id'], order)
                if result['outcome'] == 'ready':
                    result['outcome'] = 'applied'
                    db.execute('INSERT INTO order_versions VALUES(?,?,?,?,?,?)',
                               (batch['account_id'], result['order_id'], result['revision'], result['fingerprint'], encode(result), batch_id))
                    db.execute('''INSERT INTO current_orders VALUES(?,?,?) ON CONFLICT(account_id,order_id)
                                  DO UPDATE SET revision=excluded.revision''',
                               (batch['account_id'], result['order_id'], result['revision']))
                    if result['status'] == 'cancelled':
                        from .stock import cancel_order_shipments
                        cancel_order_shipments(db, batch['account_id'], result['order_id'],
                            {'operation_id': f'import/{batch_id}/{result["order_id"]}'})
                results.append(result)
            db.execute("UPDATE batches SET state='confirmed',results=? WHERE id=?", (encode(results), batch_id))
            self.event(db, 'confirm', batch_id, {'results': results, 'rule_version': 'orders-v1'})
            return self._batch(db, batch_id)

    def reprocess(self, batch_id):
        with self.connect() as db:
            self._batch(db, batch_id)
            batch = db.execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()
            attachment = db.execute('SELECT source_blob FROM operational_excel_files WHERE batch_id=?',(batch_id,)).fetchone()
            if not attachment and not batch['csv_text']:
                raise ValueError('此筆為本機訂單取消紀錄，沒有可重新匯入的 CSV；請查看訂單歷史')
        if attachment:
            import base64
            return self.preview_excel(batch['account_id'],batch['filename'],base64.b64encode(attachment['source_blob']).decode(),True,parent_id=batch_id)
        return self.preview(batch['account_id'], batch['filename'], batch['csv_text'], True, parent_id=batch_id)

    def add_mapping(self, account_id, external_sku, product_id):
        values = [required_text(v, label) for v, label in
                  [(account_id, '帳戶'), (external_sku, 'SKU'), (product_id, '商品')]]
        account_id, external_sku, product_id = values
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if (not db.execute('SELECT 1 FROM accounts WHERE id=?', (account_id,)).fetchone()
                    or not db.execute('SELECT 1 FROM products WHERE id=?', (product_id,)).fetchone()):
                raise ValueError('未知帳戶或商品')
            previous = db.execute('SELECT product_id FROM mappings WHERE account_id=? AND external_sku=?',
                                  (account_id, external_sku)).fetchone()
            if previous:
                if previous['product_id'] != product_id:
                    raise ConflictError('已存在SKU對照，不可直接覆寫')
                return {'status': 'existing'}
            db.execute('INSERT INTO mappings VALUES(?,?,?,1)', values)
            from .catalogue import EPOCH
            if db.execute('SELECT 1 FROM mapping_revisions WHERE account_id=? AND external_sku=?', (account_id, external_sku)).fetchone():
                raise ConflictError('已有版本化SKU對照，請從商品庫維護')
            db.execute('INSERT INTO mapping_revisions VALUES(?,?,1,?,?)', (*values, EPOCH))
            self.event(db, 'add_mapping', f'{account_id}/{external_sku}', {'product_id': product_id, 'version': 1})
        return {'status': 'created'}

    def current_orders(self, db=None):
        if db is None:
            with self.connect() as connection:
                return self.current_orders(connection)
        rows = db.execute('''SELECT v.*,a.platform FROM current_orders c JOIN order_versions v
                             USING(account_id,order_id,revision) JOIN accounts a ON a.id=v.account_id ORDER BY v.rowid''')
        return [dict(json.loads(r['normalized']), account_id=r['account_id'], platform=r['platform'], batch_id=r['batch_id']) for r in rows]

    def pending_count(self, account_id='', db=None):
        if db is None:
            with self.connect() as connection:
                return self.pending_count(account_id, connection)
        current = {(o['account_id'], o['order_id']): o for o in self.current_orders(db)}
        unresolved = {}
        # Use processing event order, not upload order: an older draft can be confirmed later.
        batches = db.execute('''SELECT b.account_id,b.results FROM batches b LEFT JOIN
            (SELECT target,MAX(rowid) AS sequence FROM events
             WHERE action IN ('preview','reprocess','confirm') GROUP BY target) e ON e.target=b.id
            ORDER BY e.sequence''')
        for batch in batches:
            if account_id and batch['account_id'] != account_id:
                continue
            for item in json.loads(batch['results']):
                key = (batch['account_id'], item['order_id'])
                if item['outcome'] == 'pending':
                    unresolved.setdefault(key, []).append(item)
                elif item['outcome'] == 'applied':
                    unresolved[key] = [pending for pending in unresolved.get(key, [])
                                       if pending['revision'] is None or pending['revision'] > item['revision']]
        pending = 0
        for key, items in unresolved.items():
            valid = current.get(key)
            for item in items:
                if valid and item['revision'] is not None:
                    if valid['revision'] > item['revision'] or (valid['revision'] == item['revision']
                            and valid['fingerprint'] == item.get('fingerprint')):
                        continue
                pending += 1
                break  # One unresolved order, regardless of number of failed versions/uploads.
        return pending
