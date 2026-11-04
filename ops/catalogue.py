"""Product identity is shared by orders, stock and BOM; mappings only adapt external SKUs."""
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from .store import ConflictError, encode, now, required_text
from .transactions import command, initialize as initialize_commands

EPOCH = '0001-01-01T00:00:00+00:00'


def timestamp(value):
    value = required_text(value, '生效時間', 60)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if 'T' not in value or parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        raise ValueError('生效時間須包含日期、時間與時區') from None


def initialize(db):
    initialize_commands(db)
    db.executescript('''
      CREATE TABLE IF NOT EXISTS product_details(product_id TEXT PRIMARY KEY REFERENCES products(id),
        sku TEXT NOT NULL UNIQUE, barcode TEXT UNIQUE, group_name TEXT NOT NULL, specification TEXT NOT NULL,
        active INTEGER NOT NULL, attributes TEXT NOT NULL, version INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS product_versions(product_id TEXT REFERENCES products(id),version INTEGER,
        snapshot TEXT NOT NULL,created_at TEXT NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(product_id,version));
      CREATE TABLE IF NOT EXISTS attribute_definitions(key TEXT PRIMARY KEY,label TEXT NOT NULL,kind TEXT NOT NULL,options TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS mapping_revisions(account_id TEXT REFERENCES accounts(id),external_sku TEXT,
        version INTEGER,product_id TEXT REFERENCES products(id),effective_from TEXT NOT NULL,
        PRIMARY KEY(account_id,external_sku,version),UNIQUE(account_id,external_sku,effective_from));
      CREATE TABLE IF NOT EXISTS bundle_revisions(product_id TEXT REFERENCES products(id),version INTEGER,
        effective_from TEXT NOT NULL,components TEXT NOT NULL,PRIMARY KEY(product_id,version),UNIQUE(product_id,effective_from));
    ''')
    migrate(db)


def migrate(db):
    for row in db.execute('SELECT * FROM products').fetchall():
        db.execute("INSERT OR IGNORE INTO product_details VALUES(?,?,NULL,'','',1,'{}',1)", (row['id'], row['id']))
        p = product(db, row['id'])
        db.execute('INSERT OR IGNORE INTO product_versions VALUES(?,?,?,?,?)', (row['id'], p['version'], encode(p), now(), '既有主檔遷移'))
    for row in db.execute('SELECT * FROM mappings').fetchall():
        db.execute('INSERT OR IGNORE INTO mapping_revisions VALUES(?,?,?,?,?)',
                   (row['account_id'], row['external_sku'], 1, row['product_id'], EPOCH))
    for row in db.execute('SELECT DISTINCT bundle_id FROM components').fetchall():
        items = [dict(r) for r in db.execute('SELECT product_id,quantity,version FROM components WHERE bundle_id=? ORDER BY product_id', (row[0],))]
        db.execute('INSERT OR IGNORE INTO bundle_revisions VALUES(?,?,?,?)', (row[0], 1, EPOCH, encode(items)))


def product(db, product_id):
    row = db.execute('SELECT p.*,d.sku,d.barcode,d.group_name,d.specification,d.active,d.attributes,d.version FROM products p JOIN product_details d ON p.id=d.product_id WHERE p.id=?', (product_id,)).fetchone()
    if row is None:
        raise KeyError(product_id)
    result = dict(row)
    result['active'] = bool(result['active'])
    result['attributes'] = json.loads(result['attributes'])
    return result


class Catalogue:
    def __init__(self, store):
        self.store = store

    def account(self, body):
        def apply(db):
            name = required_text(body.get('name'), '來源名稱', 200)
            account_id = body.get('id')
            if account_id:
                previous = db.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone()
                if previous is None:
                    raise KeyError(account_id)
                db.execute('UPDATE accounts SET name=? WHERE id=?', (name, account_id))
                return dict(id=account_id, name=name, platform=previous['platform'])
            platform = body.get('platform')
            if platform not in ('manual', 'SHOPLINE', 'Shopee'):
                raise ValueError('請選擇手動、SHOPLINE或蝦皮來源分類；此處不連接平台')
            account_id = uuid4().hex
            db.execute('INSERT INTO accounts VALUES(?,?,?)', (account_id, platform, name))
            return dict(id=account_id, name=name, platform=platform)
        return command(self.store, 'account_save', body, apply)

    def list(self, q=''):
        if not isinstance(q, str) or len(q) > 200:
            raise ValueError('搜尋最多200字元')
        with self.store.connect() as db:
            products = [product(db, r[0]) for r in db.execute('SELECT id FROM products ORDER BY rowid')]
            if q:
                needle = q.casefold()
                selected = []
                for p in products:
                    names = [json.loads(r[0])['name'] for r in db.execute('SELECT snapshot FROM product_versions WHERE product_id=?', (p['id'],))]
                    skus = [r[0] for r in db.execute('SELECT external_sku FROM mapping_revisions WHERE product_id=?', (p['id'],))]
                    if any(needle in str(v or '').casefold() for v in [p['name'], p['sku'], p['barcode'], p['group_name'], p['specification']] + names + skus):
                        selected.append(p)
                products = selected
            definitions = [dict(r) for r in db.execute('SELECT * FROM attribute_definitions ORDER BY key')]
            for d in definitions:
                d['options'] = json.loads(d['options'])
            mappings = [dict(r) for r in db.execute('SELECT * FROM mapping_revisions ORDER BY account_id,external_sku,version')]
            return dict(products=products, definitions=definitions, mappings=mappings)

    def detail(self, product_id):
        with self.store.connect() as db:
            p = product(db, product_id)
            p['versions'] = [dict(version=r['version'], snapshot=json.loads(r['snapshot']), created_at=r['created_at'], reason=r['reason'])
                             for r in db.execute('SELECT * FROM product_versions WHERE product_id=? ORDER BY version', (product_id,))]
            p['bundle_versions'] = [dict(version=r['version'], effective_from=r['effective_from'], components=json.loads(r['components']))
                                   for r in db.execute('SELECT * FROM bundle_revisions WHERE product_id=? ORDER BY version', (product_id,))]
            p['impacted_orders'] = [dict(account_id=o['account_id'], order_id=o['order_id'], revision=o['revision'], batch_id=o['batch_id'])
                for o in self.store.current_orders(db) if any(line.get('product_id') == product_id or any(c['product_id'] == product_id for c in line.get('components', [])) for line in o['lines'])]
            return p

    def _validate(self, db, data):
        for key in ('name', 'sku'):
            data[key] = required_text(data.get(key), key, 200)
        for key in ('barcode', 'group_name', 'specification'):
            value = data.get(key) or ''
            if not isinstance(value, str) or len(value) > 200:
                raise ValueError(f'{key}最多200字元')
            data[key] = value.strip()
        data['barcode'] = data['barcode'] or None
        if data.get('kind') not in ('product', 'bundle') or type(data.get('active')) is not bool:
            raise ValueError('商品類型或啟用狀態不正確')
        attrs = data.get('attributes')
        if not isinstance(attrs, dict) or len(attrs) > 50:
            raise ValueError('商品屬性須為物件，最多50項')
        for key, value in attrs.items():
            definition = db.execute('SELECT * FROM attribute_definitions WHERE key=?', (key,)).fetchone()
            if not definition or not isinstance(value, str) or len(value) > 200:
                raise ValueError('屬性未定義或值不合法')
            if definition['kind'] == 'choice' and value not in json.loads(definition['options']):
                raise ValueError(f'{key}不在已定義選項內')
            if definition['kind'] == 'number':
                try:
                    valid = Decimal(value).is_finite()
                except InvalidOperation:
                    valid = False
                if not valid:
                    raise ValueError(f'{key}須為有限數字')
        return data

    @staticmethod
    def _save(db, p, reason):
        try:
            db.execute('UPDATE products SET name=? WHERE id=?', (p['name'], p['id']))
            db.execute('''INSERT INTO product_details VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(product_id) DO UPDATE SET
                sku=excluded.sku,barcode=excluded.barcode,group_name=excluded.group_name,specification=excluded.specification,
                active=excluded.active,attributes=excluded.attributes,version=excluded.version''',
                (p['id'], p['sku'], p['barcode'], p['group_name'], p['specification'], int(p['active']), encode(p['attributes']), p['version']))
        except sqlite3.IntegrityError:
            raise ConflictError('內部 SKU 或條碼已被使用') from None
        db.execute('INSERT INTO product_versions VALUES(?,?,?,?,?)', (p['id'], p['version'], encode(p), now(), reason))
        return p

    def create(self, body):
        def apply(db):
            p = self._validate(db, dict(id=uuid4().hex, name=body.get('name'), sku=body.get('sku'), kind=body.get('kind', 'product'),
                barcode=body.get('barcode'), group_name=body.get('group_name'), specification=body.get('specification'),
                active=True, attributes=body.get('attributes', {}), version=1))
            db.execute('INSERT INTO products VALUES(?,?,?)', (p['id'], p['name'], p['kind']))
            return self._save(db, p, '建立商品')
        return command(self.store, 'product_create', body, apply)

    def update(self, product_id, body):
        def apply(db):
            p = product(db, product_id)
            if type(body.get('expected_version')) is not int or body['expected_version'] != p['version']:
                raise ConflictError('商品已被修改，請重新整理')
            if 'kind' in body and body['kind'] != p['kind']:
                raise ValueError('不可變更商品類型，請另建商品')
            p.update({k: body[k] for k in ('name', 'sku', 'barcode', 'group_name', 'specification', 'active', 'attributes') if k in body})
            p['version'] += 1
            return self._save(db, self._validate(db, p), required_text(body.get('reason', '維護商品'), '原因', 200))
        return command(self.store, f'product_update/{product_id}', body, apply)

    def define_attribute(self, body):
        def apply(db):
            key = required_text(body.get('key'), '屬性代碼', 80)
            label = required_text(body.get('label'), '屬性名稱', 80)
            kind, options = body.get('kind'), body.get('options', [])
            if kind not in ('text', 'number', 'choice') or not isinstance(options, list) or len(options) > 50:
                raise ValueError('屬性型別或選項不正確')
            options = [required_text(x, '選項', 200) for x in options]
            if len(set(options)) != len(options) or (kind == 'choice' and not options) or (kind != 'choice' and options):
                raise ValueError('選項不可重複；選擇型必須有選項')
            if db.execute('SELECT 1 FROM attribute_definitions WHERE key=?', (key,)).fetchone():
                raise ConflictError('屬性已定義，不能就地改寫型別或選項')
            db.execute('INSERT INTO attribute_definitions VALUES(?,?,?,?)', (key, label, kind, encode(options)))
            return dict(id=key, key=key, label=label, kind=kind, options=options)
        return command(self.store, 'attribute_define', body, apply)

    def mapping(self, body):
        def apply(db):
            account = required_text(body.get('account_id'), '來源帳戶', 200)
            sku = required_text(body.get('external_sku'), '外部SKU', 200)
            if not db.execute('SELECT 1 FROM accounts WHERE id=?', (account,)).fetchone():
                raise ValueError('來源帳戶不存在')
            p = product(db, body.get('product_id'))
            if not p['active']:
                raise ValueError('不能對照停用商品')
            start = timestamp(body.get('effective_from'))
            previous = db.execute('SELECT * FROM mapping_revisions WHERE account_id=? AND external_sku=? ORDER BY version DESC LIMIT 1', (account, sku)).fetchone()
            if previous and start <= previous['effective_from']:
                raise ConflictError('新版本生效時間須晚於既有版本')
            version = previous['version'] + 1 if previous else 1
            db.execute('INSERT INTO mapping_revisions VALUES(?,?,?,?,?)', (account, sku, version, p['id'], start))
            return dict(id=f'{account}/{sku}/{version}', product_id=p['id'], version=version, effective_from=start)
        return command(self.store, 'mapping_revision', body, apply)

    def bundle(self, product_id, body):
        def apply(db):
            p = product(db, product_id)
            if p['kind'] != 'bundle' or not p['active']:
                raise ValueError('只能維護啟用中的套組')
            if type(body.get('expected_version')) is not int or body['expected_version'] != p['version']:
                raise ConflictError('套組已被修改，請重新整理')
            items = body.get('components')
            if not isinstance(items, list) or not 1 <= len(items) <= 100:
                raise ValueError('套組須包含1至100種單品')
            components, seen = [], set()
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError('組成格式不正確')
                child = product(db, item.get('product_id'))
                quantity = item.get('quantity')
                if child['kind'] != 'product' or not child['active'] or child['id'] in seen or type(quantity) is not int or not 1 <= quantity <= 100000:
                    raise ValueError('組成須為不重複的啟用單品，件數為1至100000整數')
                seen.add(child['id'])
                components.append(dict(product_id=child['id'], quantity=quantity))
            start = timestamp(body.get('effective_from'))
            previous = db.execute('SELECT * FROM bundle_revisions WHERE product_id=? ORDER BY version DESC LIMIT 1', (product_id,)).fetchone()
            if previous and start <= previous['effective_from']:
                raise ConflictError('新套組生效時間須晚於既有版本')
            version = previous['version'] + 1 if previous else 1
            for item in components:
                item['version'] = version
            db.execute('INSERT INTO bundle_revisions VALUES(?,?,?,?)', (product_id, version, start, encode(components)))
            p['version'] += 1
            self._save(db, p, f'套組組成版本{version}')
            return dict(p, bundle_version=version, components=components)
        return command(self.store, f'bundle_revision/{product_id}', body, apply)

    @staticmethod
    def resolve(db, account, sku, order_time):
        at = timestamp(order_time)
        mapping = db.execute('SELECT * FROM mapping_revisions WHERE account_id=? AND external_sku=? AND effective_from<=? ORDER BY version DESC LIMIT 1', (account, sku, at)).fetchone()
        if mapping is None:
            raise ValueError(f'缺SKU對照 {sku}')
        result = Catalogue.resolve_product(db, mapping['product_id'], at)
        result['mapping_version'] = mapping['version']
        return result

    @staticmethod
    def resolve_sku(db, sku, order_time):
        row = db.execute('SELECT product_id FROM product_details WHERE sku=?', (sku,)).fetchone()
        if row is None:
            raise ValueError(f'商品 SKU「{sku}」尚未建立，請先至 SKU 商品庫新增')
        return Catalogue.resolve_product(db, row[0], order_time)

    @staticmethod
    def resolve_product(db, product_id, order_time):
        at = timestamp(order_time)
        p = product(db, product_id)
        if not p['active']:
            raise ValueError(f'商品已停用 {p["sku"]}')
        result = dict(product_id=p['id'], product_version=p['version'], mapping_version=None, components=[])
        if p['kind'] == 'bundle':
            bundle = db.execute('SELECT * FROM bundle_revisions WHERE product_id=? AND effective_from<=? ORDER BY version DESC LIMIT 1', (p['id'], at)).fetchone()
            if bundle is None:
                raise ValueError('缺少適用的套組組成版本')
            result['bundle_version'] = bundle['version']
            result['components'] = json.loads(bundle['components'])
            if any(not product(db, item['product_id'])['active'] for item in result['components']):
                raise ValueError('套組含已停用商品')
        return result
