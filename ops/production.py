"""Factory production orders sharing the existing material ledger and receipts."""
import sqlite3
from uuid import uuid4

from .store import ConflictError, encode, now, required_text
from .stock import count, confirm_receipt_in_transaction, row_json
from .goods_purchases import schedule
from .catalogue import product as get_product


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS production_details(
        plan_id TEXT PRIMARY KEY REFERENCES production_plans(id),
        factory_id TEXT NOT NULL REFERENCES suppliers(id), factory_name TEXT NOT NULL,
        received INTEGER NOT NULL DEFAULT 0, expected_date TEXT NOT NULL, comment TEXT NOT NULL,
        created_at TEXT NOT NULL, cancel_reason TEXT);
      CREATE TABLE IF NOT EXISTS production_receipts(
        receipt_id TEXT PRIMARY KEY REFERENCES receipts(id), plan_id TEXT NOT NULL REFERENCES production_plans(id));
      CREATE TABLE IF NOT EXISTS production_purchases(
        purchase_id TEXT PRIMARY KEY REFERENCES material_purchases(id), plan_id TEXT NOT NULL REFERENCES production_plans(id));
    ''')


def enrich(db, plan):
    extra = db.execute('SELECT * FROM production_details WHERE plan_id=?', (plan['id'],)).fetchone()
    if extra:
        plan.update(dict(extra))
        left = plan['quantity'] - plan['received']
        plan.update(mode='reported', pending=left if plan['state'] in ('ordered','partial') else 0,
                    cancelled_quantity=left if plan['state']=='cancelled' else 0)
        plan['receipts'] = [row_json(r, ['lines']) for r in db.execute('''SELECT r.* FROM receipts r
            JOIN production_receipts p ON p.receipt_id=r.id WHERE p.plan_id=? ORDER BY r.rowid''', (plan['id'],))]
        plan['purchase_ids'] = [r[0] for r in db.execute('SELECT purchase_id FROM production_purchases WHERE plan_id=?', (plan['id'],))]
    return plan


def create(db, body, product):
    factory_id = required_text(body.get('factory_id'),'產品工廠',100)
    factory = db.execute('SELECT * FROM suppliers WHERE id=? AND active=1', (factory_id,)).fetchone()
    if factory is None:
        raise ValueError('請選擇啟用中的產品工廠')
    if product['kind'] != 'product':
        raise ValueError('生產只接受單品 SKU')
    number, qty = required_text(body.get('number'),'生產單號',100),count(body.get('quantity'))
    expected, comment = schedule(body)
    identifier = uuid4().hex
    try:
        db.execute('INSERT INTO production_plans VALUES(?,?,?,?,?,?,?,?)',
            (identifier,number,product['id'],0,qty,'draft',1,'[]'))
    except sqlite3.IntegrityError:
        raise ConflictError('生產單號已存在') from None
    db.execute('INSERT INTO production_details VALUES(?,?,?,?,?,?,?,?)',
        (identifier,factory['id'],factory['name'],0,expected,comment,now(),None))
    return identifier


def action(db, plan, action, body):
    if type(body.get('expected_version')) is not int or body['expected_version'] != plan['version']:
        raise ConflictError('生產單已更新，請重新整理後操作')
    state = plan['state']
    if action == 'confirm':
        if state != 'draft':
            raise ConflictError('只有草稿可以確認生產')
        if not get_product(db,plan['product_id'])['active']:
            raise ConflictError('商品已停用，請先核對生產內容')
        if not db.execute('SELECT 1 FROM suppliers WHERE id=? AND active=1',(plan['factory_id'],)).fetchone():
            raise ConflictError('產品工廠已停用')
        plan['state'] = 'ordered'
    elif action == 'receive':
        if state not in ('ordered','partial'):
            raise ConflictError('只有已下單或部分到貨生產單可以收貨')
        qty = count(body.get('quantity'))
        if qty > plan['quantity']-plan['received']:
            raise ConflictError('本次成品實收超過待收量')
        number = required_text(body.get('number'),'本次入庫單號',100)
        receipt_id = uuid4().hex
        lines = [dict(product_id=plan['product_id'],quantity=qty,unit_price_cents=None)]
        try:
            db.execute('''INSERT INTO receipts(id,number,state,lines,freight_cents,allocation,created_at,source,comment,
                freight_known,tax_cents,tax_known,cost_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (receipt_id,number,'draft',encode(lines),0,'quantity',now(),plan['factory_name'],
                 '生產單：'+plan['number'],0,0,0,'pending'))
        except sqlite3.IntegrityError:
            raise ConflictError('入庫單號已存在，請查核原收貨紀錄') from None
        db.execute('INSERT INTO production_receipts VALUES(?,?)',(receipt_id,plan['id']))
        confirm_receipt_in_transaction(db,receipt_id,body)
        plan['received'] += qty
        plan['state'] = 'received' if plan['received']==plan['quantity'] else 'partial'
        db.execute('UPDATE production_details SET received=? WHERE plan_id=?',(plan['received'],plan['id']))
    elif action == 'cancel':
        if state not in ('draft','ordered','partial'):
            raise ConflictError('生產單已結案')
        reason = required_text(body.get('reason'),'取消原因',2000)
        db.execute('UPDATE production_details SET cancel_reason=? WHERE plan_id=?',(reason,plan['id']))
        plan['state'] = 'cancelled'
    elif action == 'revise':
        if state not in ('draft','ordered','partial'):
            raise ConflictError('生產單已結案')
        qty = count(body.get('quantity'))
        if qty < plan['received']:
            raise ConflictError('生產量不可少於已實收成品')
        plan['quantity'] = qty
        if qty == plan['received']:
            plan['state'] = 'received'
    elif action == 'schedule':
        if state not in ('draft','ordered','partial'):
            raise ConflictError('生產單已結案')
        expected, comment = schedule(body)
        db.execute('UPDATE production_details SET expected_date=?,comment=? WHERE plan_id=?',
            (expected,comment,plan['id']))
    else:
        raise ValueError('未知生產單操作')
    plan['version'] += 1
    return plan
