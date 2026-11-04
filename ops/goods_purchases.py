"""Finished goods on order; physical stock remains owned by receipts."""
import json
import re
import sqlite3
from datetime import date
from uuid import uuid4

from .catalogue import product
from .stock import count, money, optional_money, receipt_costs, row_json, confirm_receipt_in_transaction
from .store import ConflictError, encode, now, required_text
from .transactions import command


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS goods_purchases(
        id TEXT PRIMARY KEY, number TEXT NOT NULL UNIQUE, supplier_id TEXT NOT NULL REFERENCES suppliers(id),
        supplier_name TEXT NOT NULL, state TEXT NOT NULL, lines TEXT NOT NULL,
        expected_date TEXT NOT NULL, comment TEXT NOT NULL, version INTEGER NOT NULL,
        created_at TEXT NOT NULL, ordered_at TEXT, closed_at TEXT, cancel_reason TEXT);
      CREATE TABLE IF NOT EXISTS goods_purchase_receipts(
        receipt_id TEXT PRIMARY KEY REFERENCES receipts(id), purchase_id TEXT NOT NULL REFERENCES goods_purchases(id));
      CREATE TABLE IF NOT EXISTS goods_purchase_history(
        id TEXT PRIMARY KEY, purchase_id TEXT NOT NULL REFERENCES goods_purchases(id),
        action TEXT NOT NULL, time TEXT NOT NULL, details TEXT NOT NULL);
    ''')


def schedule(body):
    expected = body.get('expected_date', '')
    if not isinstance(expected, str):
        raise ValueError('預計到貨日須為 YYYY-MM-DD 或空白')
    if expected:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', expected):
            raise ValueError('預計到貨日須為 YYYY-MM-DD')
        try:
            date.fromisoformat(expected)
        except ValueError:
            raise ValueError('預計到貨日無效') from None
    comment = body.get('comment', '')
    if not isinstance(comment, str) or len(comment) > 2000:
        raise ValueError('備註須為最多2000字的文字')
    return expected, comment.strip()


def detail(db, purchase_id):
    result = row_json(db.execute('SELECT * FROM goods_purchases WHERE id=?', (purchase_id,)).fetchone(), ['lines'])
    for line in result['lines']:
        left = line['quantity'] - line['received']
        line['cancelled'] = left if result['state'] == 'cancelled' else 0
        line['pending'] = left if result['state'] in ('ordered', 'partial') else 0
    result['receipts'] = [row_json(r, ['lines']) for r in db.execute('''SELECT r.* FROM receipts r
        JOIN goods_purchase_receipts g ON g.receipt_id=r.id WHERE g.purchase_id=? ORDER BY r.rowid''', (purchase_id,))]
    result['history'] = [row_json(r, ['details']) for r in db.execute(
        'SELECT * FROM goods_purchase_history WHERE purchase_id=? ORDER BY rowid', (purchase_id,))]
    return result


def check_version(purchase, body):
    if type(body.get('expected_version')) is not int or body['expected_version'] != purchase['version']:
        raise ConflictError('採購單已更新，請重新整理後再操作')


def active_product(db, product_id):
    p = product(db, product_id)
    if not p['active'] or p['kind'] != 'product':
        raise ValueError('採購與收貨只接受啟用中的單品 SKU')
    return p


def history(db, purchase_id, action, before, after, body, **extra):
    db.execute('INSERT INTO goods_purchase_history VALUES(?,?,?,?,?)',
        (uuid4().hex, purchase_id, action, now(), encode(dict(
            operation_id=body['operation_id'], before=before, after=after, **extra))))


def record(db, purchase_id):
    return row_json(db.execute('SELECT * FROM goods_purchases WHERE id=?', (purchase_id,)).fetchone(), ['lines'])


class GoodsPurchases:
    def __init__(self, store):
        self.store = store

    def snapshot(self):
        with self.store.connect() as db:
            db.execute('BEGIN')
            return dict(purchases=[detail(db, r[0]) for r in db.execute('SELECT id FROM goods_purchases ORDER BY rowid DESC')],
                suppliers=[dict(r) for r in db.execute('SELECT * FROM suppliers ORDER BY name')],
                products=[product(db, r[0]) for r in db.execute("SELECT id FROM products WHERE kind='product'")])

    def save(self, body):
        def apply(db):
            previous = record(db, body['id']) if body.get('id') else None
            if previous:
                check_version(previous, body)
                if previous['state'] != 'draft':
                    raise ConflictError('只有草稿可以修改採購品項；已叫貨可調整交期或取消餘量')
            number = required_text(body.get('number'), '採購單號', 100)
            supplier = db.execute('SELECT * FROM suppliers WHERE id=? AND active=1', (body.get('supplier_id'),)).fetchone()
            if supplier is None:
                raise ValueError('請選擇啟用中的供應商')
            expected, comment = schedule(body)
            items = body.get('lines')
            if not isinstance(items, list) or not items or len(items) > 500:
                raise ValueError('請填寫1至500項採購商品')
            lines, seen = [], set()
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError('商品明細格式錯誤')
                p = active_product(db, item.get('product_id'))
                if p['id'] in seen:
                    raise ValueError('同一 SKU 請合併成一列')
                seen.add(p['id'])
                quote = item.get('quoted_price')
                lines.append(dict(product_id=p['id'], sku=p['sku'], name=p['name'],
                    quantity=count(item.get('quantity')), received=0,
                    quoted_price_cents=None if quote in (None, '') else money(quote)))
            purchase_id = previous['id'] if previous else uuid4().hex
            try:
                if previous:
                    db.execute('''UPDATE goods_purchases SET number=?,supplier_id=?,supplier_name=?,lines=?,
                        expected_date=?,comment=?,version=version+1 WHERE id=?''',
                        (number, supplier['id'], supplier['name'], encode(lines), expected, comment, purchase_id))
                else:
                    db.execute('''INSERT INTO goods_purchases(id,number,supplier_id,supplier_name,state,lines,
                        expected_date,comment,version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                        (purchase_id, number, supplier['id'], supplier['name'], 'draft', encode(lines), expected, comment, 1, now()))
            except sqlite3.IntegrityError:
                raise ConflictError('採購單號已存在') from None
            history(db, purchase_id, 'edit' if previous else 'create', previous, record(db, purchase_id), body)
            return detail(db, purchase_id)
        return command(self.store, 'goods_purchase_save', body, apply)

    def action(self, purchase_id, action, body):
        if action not in ('confirm', 'schedule', 'cancel', 'receive'):
            raise ValueError('無效的採購操作')
        def apply(db):
            p = record(db, purchase_id)
            check_version(p, body)
            extra = {}
            if action == 'confirm':
                if p['state'] != 'draft':
                    raise ConflictError('僅草稿可確認叫貨')
                if not db.execute('SELECT 1 FROM suppliers WHERE id=? AND active=1', (p['supplier_id'],)).fetchone():
                    raise ConflictError('供應商已停用')
                for line in p['lines']:
                    active_product(db, line['product_id'])
                db.execute("UPDATE goods_purchases SET state='ordered',ordered_at=? WHERE id=?", (now(), purchase_id))
            elif action == 'schedule':
                if p['state'] not in ('draft', 'ordered', 'partial'):
                    raise ConflictError('已結案採購單不可修改')
                expected, comment = schedule(body)
                db.execute('UPDATE goods_purchases SET expected_date=?,comment=? WHERE id=?', (expected, comment, purchase_id))
            elif action == 'cancel':
                if p['state'] not in ('draft', 'ordered', 'partial'):
                    raise ConflictError('此採購單已結案')
                reason = required_text(body.get('reason'), '取消原因', 2000)
                db.execute("UPDATE goods_purchases SET state='cancelled',closed_at=?,cancel_reason=? WHERE id=?",
                    (now(), reason, purchase_id))
            else:
                extra = self._receive(db, p, body)
            db.execute('UPDATE goods_purchases SET version=version+1 WHERE id=?', (purchase_id,))
            history(db, purchase_id, action, p, record(db, purchase_id), body, **extra)
            return detail(db, purchase_id)
        return command(self.store, f'goods_purchase_{action}/{purchase_id}', body, apply)

    @staticmethod
    def _receive(db, p, body):
        if p['state'] not in ('ordered', 'partial'):
            raise ConflictError('只有已叫貨或部分到貨單可以收貨')
        number = required_text(body.get('number'), '本次入庫單號', 100)
        freight = optional_money(body.get('freight'))
        tax = optional_money(body.get('tax', '0'))
        allocation = body.get('allocation')
        if allocation not in ('quantity', 'value'):
            raise ValueError('請選擇依件數或貨款分攤運費')
        items = body.get('lines')
        if not isinstance(items, list) or not items or len(items) > 500:
            raise ValueError('請選擇本批實際收到的商品')
        ordered = {line['product_id']: dict(line) for line in p['lines']}
        lines, seen = [], set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('收貨明細格式錯誤')
            pid = item.get('product_id')
            if not isinstance(pid, str) or pid not in ordered or pid in seen:
                raise ValueError('收貨品項須屬於本張採購單且不可重複')
            seen.add(pid)
            active_product(db, pid)
            quantity = count(item.get('quantity'))
            if quantity > ordered[pid]['quantity'] - ordered[pid]['received']:
                raise ConflictError('本次收貨超過剩餘待收數量')
            ordered[pid]['received'] += quantity
            lines.append(dict(product_id=pid, quantity=quantity, unit_price_cents=optional_money(item.get('unit_price'))))
        if sum(line['quantity'] for line in lines) > 10000:
            raise ValueError('每批收貨最多10000件')
        costs = receipt_costs(lines, freight, tax, allocation)
        receipt_id = uuid4().hex
        try:
            db.execute('''INSERT INTO receipts(id,number,state,lines,freight_cents,allocation,created_at,source,comment,
                freight_known,tax_cents,tax_known,cost_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (receipt_id, number, 'draft', encode(lines), freight or 0, allocation,
                now(), p['supplier_name'], f"商品採購：{p['number']}", freight is not None,tax or 0,tax is not None,
                'pending' if costs is None else 'complete'))
        except sqlite3.IntegrityError:
            raise ConflictError('入庫單號已存在，請核對是否已收貨') from None
        db.execute('INSERT INTO goods_purchase_receipts VALUES(?,?)', (receipt_id, p['id']))
        confirm_receipt_in_transaction(db, receipt_id, body)
        complete = all(line['received'] == line['quantity'] for line in ordered.values())
        db.execute('UPDATE goods_purchases SET lines=?,state=?,closed_at=? WHERE id=?',
            (encode(list(ordered.values())), 'received' if complete else 'partial', now() if complete else None, p['id']))
        return dict(receipt_id=receipt_id, receipt_number=number)
