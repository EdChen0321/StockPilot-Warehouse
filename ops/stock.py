"""Physical stock is created only by confirmed receipts, never by sales import."""
import json
import re
import sqlite3
from decimal import Decimal
from uuid import uuid4

from .catalogue import product
from .store import ConflictError, encode, now, required_text
from .transactions import command
from .locations import enabled as locations_enabled, eligible as location_eligible, exportable, LOCATION_SQL


def money(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,12}(\.\d{1,2})?', value):
        raise ValueError('金額須為非負小數，最多12位整數及2位小數')
    return int(Decimal(value) * 100)


def count(value):
    if type(value) is not int or not 1 <= value <= 10000:
        raise ValueError('數量須為1至10000的整數')
    return value


def optional_money(value):
    return None if value is None or value == '' else money(value)


def receipt_costs(lines, freight, tax, allocation):
    """Return exact per-unit cents in receipt-line order; None means incomplete."""
    if allocation not in ('quantity', 'value'):
        raise ValueError('費用請選依件數或貨款金額分攤')
    if freight is None or tax is None or any(x['unit_price_cents'] is None for x in lines):
        return None
    weights = [x['quantity'] * (x['unit_price_cents'] if allocation == 'value' else 1) for x in lines]
    denominator = sum(weights)
    expense = freight + tax
    if allocation == 'value' and expense and not denominator:
        raise ValueError('零貨款不能依金額分攤費用，請選件數')
    shares = [expense * w // denominator if denominator else 0 for w in weights]
    ranking = sorted(range(len(lines)), key=lambda i: (-(expense * weights[i] % denominator) if denominator else 0, i))
    for i in ranking[:expense - sum(shares)]:
        shares[i] += 1
    costs = []
    for line, share in zip(lines, shares):
        each, extra = divmod(share, line['quantity'])
        costs.extend(line['unit_price_cents'] + each + (i < extra) for i in range(line['quantity']))
    return costs


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,number TEXT NOT NULL UNIQUE,state TEXT NOT NULL,
        lines TEXT NOT NULL,freight_cents INTEGER NOT NULL,allocation TEXT NOT NULL,created_at TEXT NOT NULL,confirmed_at TEXT);
      CREATE TABLE IF NOT EXISTS stock_units(id TEXT PRIMARY KEY,product_id TEXT NOT NULL REFERENCES products(id),
        receipt_id TEXT NOT NULL REFERENCES receipts(id),first_received_at TEXT NOT NULL,fifo_at TEXT NOT NULL,
        location TEXT NOT NULL,quality TEXT NOT NULL,packaging TEXT NOT NULL,cost_cents INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS fulfillment_groups(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,order_id TEXT NOT NULL,
        revision INTEGER NOT NULL,requirements TEXT NOT NULL,UNIQUE(account_id,order_id));
      CREATE TABLE IF NOT EXISTS shipments(id TEXT PRIMARY KEY,group_id TEXT NOT NULL REFERENCES fulfillment_groups(id),
        parent_id TEXT REFERENCES shipments(id),number TEXT NOT NULL UNIQUE,state TEXT NOT NULL,lines TEXT NOT NULL,
        created_at TEXT NOT NULL,shipped_at TEXT);
      CREATE TABLE IF NOT EXISTS allocations(unit_id TEXT PRIMARY KEY REFERENCES stock_units(id),shipment_id TEXT NOT NULL REFERENCES shipments(id));
      CREATE TABLE IF NOT EXISTS stock_events(id TEXT PRIMARY KEY,operation_id TEXT NOT NULL,kind TEXT NOT NULL,
        unit_id TEXT REFERENCES stock_units(id),shipment_id TEXT REFERENCES shipments(id),receipt_id TEXT REFERENCES receipts(id),
        time TEXT NOT NULL,details TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS stock_returns(ship_event_id TEXT PRIMARY KEY REFERENCES stock_events(id),
        unit_id TEXT NOT NULL REFERENCES stock_units(id),snapshot TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS stock_ship_reversals(ship_event_id TEXT PRIMARY KEY REFERENCES stock_events(id),
        reopened_at TEXT NOT NULL,operation_id TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS stock_return_releases(ship_event_id TEXT PRIMARY KEY REFERENCES stock_returns(ship_event_id),
        unit_id TEXT NOT NULL REFERENCES stock_units(id),released_at TEXT NOT NULL,snapshot TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS shipment_work(shipment_id TEXT PRIMARY KEY REFERENCES shipments(id),state TEXT NOT NULL,reason TEXT NOT NULL,updated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS manual_dispatch_groups(group_id TEXT PRIMARY KEY REFERENCES fulfillment_groups(id));
      CREATE TABLE IF NOT EXISTS scanned_shipments(shipment_id TEXT PRIMARY KEY REFERENCES shipments(id));
      CREATE TABLE IF NOT EXISTS order_work(account_id TEXT NOT NULL,order_id TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,version INTEGER NOT NULL,PRIMARY KEY(account_id,order_id));
      CREATE TABLE IF NOT EXISTS order_additions(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,order_id TEXT NOT NULL,number TEXT NOT NULL,reason TEXT NOT NULL,lines TEXT NOT NULL,requirements TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(account_id,order_id,number));
    ''')
    receipt_columns = {row[1] for row in db.execute('PRAGMA table_info(receipts)')}
    shipment_columns = {row[1] for row in db.execute('PRAGMA table_info(shipments)')}
    if 'reopen_count' not in shipment_columns:
        db.execute('ALTER TABLE shipments ADD COLUMN reopen_count INTEGER NOT NULL DEFAULT 0')
    for field in ('channel', 'comment'):
        if field not in shipment_columns:
            db.execute(f"ALTER TABLE shipments ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
    for field in ('source', 'comment'):
        if field not in receipt_columns:
            db.execute(f"ALTER TABLE receipts ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
    for field in ('ordered_at', 'cancelled_at', 'cancel_reason'):
        if field not in receipt_columns:
            db.execute(f'ALTER TABLE receipts ADD COLUMN {field} TEXT')
    for field, declaration in (
        ('cost_version', 'INTEGER NOT NULL DEFAULT 0'),
        ('cost_status', "TEXT NOT NULL DEFAULT 'complete'"),
        ('freight_known', 'INTEGER NOT NULL DEFAULT 1'),
        ('tax_cents', 'INTEGER NOT NULL DEFAULT 0'),
        ('tax_known', 'INTEGER NOT NULL DEFAULT 1')):
        if field not in receipt_columns:
            db.execute(f'ALTER TABLE receipts ADD COLUMN {field} {declaration}')
    db.execute('''CREATE TABLE IF NOT EXISTS receipt_cost_revisions(
        receipt_id TEXT NOT NULL REFERENCES receipts(id), version INTEGER NOT NULL,
        time TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE, reason TEXT NOT NULL,
        before_json TEXT NOT NULL, after_json TEXT NOT NULL, PRIMARY KEY(receipt_id,version))''')


def row_json(row, fields):
    if row is None:
        raise KeyError('單據不存在')
    result = dict(row)
    for field in fields:
        result[field] = json.loads(result[field])
    if 'freight_known' in result:
        if not result['freight_known']: result['freight_cents'] = None
        if not result['tax_known']: result['tax_cents'] = None
    return result


def return_hold(db, unit_id):
    return db.execute('''SELECT r.ship_event_id FROM stock_returns r LEFT JOIN stock_return_releases x
        ON x.ship_event_id=r.ship_event_id WHERE r.unit_id=? AND x.ship_event_id IS NULL''', (unit_id,)).fetchone() is not None


def check_reopen_count(shipment, body):
    expected = body.get('expected_reopen_count')
    if shipment['reopen_count'] or 'expected_reopen_count' in body:
        if type(expected) is not int or expected != shipment['reopen_count']:
            raise ConflictError('出貨單已重開，請重新整理後再操作')


def stock_event(db, body, kind, unit_id, details, receipt_id=None, shipment_id=None, time=None):
    details=dict(details)
    if unit_id:
        item=db.execute('''SELECT p.name,d.sku,d.barcode,u.receipt_id,r.number AS receipt_number,
            r.source,u.cost_cents,r.cost_status,r.cost_version FROM stock_units u JOIN products p ON p.id=u.product_id
            LEFT JOIN product_details d ON d.product_id=p.id JOIN receipts r ON r.id=u.receipt_id WHERE u.id=?''',(unit_id,)).fetchone()
        if item:
            details['item_snapshot']=dict(item)
            if item['cost_status']=='pending':
                details['item_snapshot']['cost_cents']=None
                details['cost_cents']=None
            details['cost_status']=item['cost_status']
        current=db.execute('SELECT * FROM stock_units WHERE id=?',(unit_id,)).fetchone()
        if current:
            details.setdefault('after',dict(current))
            for key,state in [('from_place',details.get('before',dict(current) if kind!='receipt' else None)),('to_place',details.get('after'))]:
                pid=state.get('place_id') if state else None
                loc=db.execute('SELECT * FROM storage_places WHERE id=?',(pid,)).fetchone() if pid else None
                details.setdefault(key,dict(loc) if loc else None)
        if details.get('transfer_id'):
            transfer=db.execute('SELECT number FROM storage_transfers WHERE id=?',(details['transfer_id'],)).fetchone()
            if transfer:details['transfer_number']=transfer[0]
        if shipment_id is None and kind=='quality':
            owner=db.execute('SELECT shipment_id FROM allocations WHERE unit_id=?',(unit_id,)).fetchone()
            if owner is None:
                owner=db.execute('''SELECT e.shipment_id FROM stock_returns r JOIN stock_events e ON e.id=r.ship_event_id
                    LEFT JOIN stock_return_releases x ON x.ship_event_id=r.ship_event_id
                    WHERE r.unit_id=? AND x.ship_event_id IS NULL ORDER BY r.rowid DESC LIMIT 1''',(unit_id,)).fetchone()
            shipment_id=owner[0] if owner else None
        if shipment_id is None and kind=='return_release':
            owner=db.execute("SELECT shipment_id FROM stock_events WHERE id=? AND unit_id=? AND kind='ship'",
                             (details.get('ship_event_id'),unit_id)).fetchone()
            shipment_id=owner[0] if owner else None
    db.execute('INSERT INTO stock_events VALUES(?,?,?,?,?,?,?,?)',
        (uuid4().hex, body['operation_id'], kind, unit_id, shipment_id, receipt_id, time or now(), encode(details)))


def sync_scanned_lines(db, shipment_id):
    lines=[dict(product_id=r[0],quantity=r[1]) for r in db.execute('''SELECT u.product_id,COUNT(*) FROM allocations a
        JOIN stock_units u ON u.id=a.unit_id WHERE a.shipment_id=? GROUP BY u.product_id ORDER BY u.product_id''',(shipment_id,))]
    db.execute('UPDATE shipments SET lines=?,state=? WHERE id=?',(encode(lines),'allocated' if lines else 'draft',shipment_id))
    group_id=db.execute('SELECT group_id FROM shipments WHERE id=?',(shipment_id,)).fetchone()[0]
    if db.execute('SELECT 1 FROM manual_dispatch_groups WHERE group_id=?',(group_id,)).fetchone():
        requirements={}
        for row in db.execute("SELECT lines FROM shipments WHERE group_id=? AND state!='cancelled'",(group_id,)):
            for line in json.loads(row[0]):
                requirements[line['product_id']]=requirements.get(line['product_id'],0)+line['quantity']
        db.execute('UPDATE fulfillment_groups SET requirements=? WHERE id=?',(encode(requirements),group_id))
    return lines


def cancel_order_shipments(db, account_id, order_id, body):
    """Called within the order transaction; shipped units and quality stay intact."""
    rows = db.execute('''SELECT s.* FROM shipments s JOIN fulfillment_groups g ON g.id=s.group_id
        WHERE g.account_id=? AND g.order_id=? AND s.state IN ('draft','allocated','picked')''',
        (account_id, order_id)).fetchall()
    for shipment in rows:
        for unit in db.execute('SELECT unit_id FROM allocations WHERE shipment_id=?', (shipment['id'],)).fetchall():
            stock_event(db, body, 'release', unit[0],
                dict(group_id=shipment['group_id'], reason='original_order_cancelled'), shipment_id=shipment['id'])
        db.execute('DELETE FROM allocations WHERE shipment_id=?', (shipment['id'],))
        db.execute("UPDATE shipments SET state='cancelled' WHERE id=?", (shipment['id'],))
        stock_event(db, body, 'order_cancel', None, dict(order_id=order_id, previous_state=shipment['state']), shipment_id=shipment['id'])


def guard_manual_source(db, group):
    """Do not let a dispatch predating an import bypass the imported order."""
    if db.execute('SELECT 1 FROM manual_dispatch_groups WHERE group_id=?', (group['id'],)).fetchone():
        if db.execute('SELECT 1 FROM current_orders WHERE order_id=?', (group['order_id'],)).fetchone():
            raise ConflictError('此獨立出貨單已有同號來源訂單，請先取消未出貨單並核對原訂單；不能繼續獨立出貨')


def confirm_receipt_in_transaction(db, receipt_id, body):
    receipt = row_json(db.execute('SELECT * FROM receipts WHERE id=?', (receipt_id,)).fetchone(), ['lines'])
    if receipt['state'] == 'confirmed':
        return receipt
    if receipt['state'] not in ('draft','ordered'):
        raise ConflictError('已取消的入庫單不可確認入庫，請另建新單')
    lines = receipt['lines']
    for line in lines:
        p = product(db, line['product_id'])
        if not p['active'] or p['kind'] != 'product':
            raise ConflictError('商品已停用或無法入庫，請核對草稿')
    costs = receipt_costs(lines, receipt['freight_cents'], receipt['tax_cents'], receipt['allocation'])
    cost_index = 0
    time = now()
    for line in lines:
        for index in range(line['quantity']):
            unit_id = uuid4().hex
            cost = costs[cost_index] if costs is not None else 0
            cost_index += 1
            db.execute('INSERT INTO stock_units(id,product_id,receipt_id,first_received_at,fifo_at,location,quality,packaging,cost_cents) VALUES(?,?,?,?,?,?,?,?,?)', (unit_id, line['product_id'], receipt_id, time, time, 'warehouse', 'good', 'good', cost))
            stock_event(db, body, 'receipt', unit_id, dict(product_id=line['product_id'], cost_cents=cost if costs is not None else None,cost_status=receipt['cost_status'],can_export=not locations_enabled(db)), receipt_id=receipt_id, time=time)
    db.execute("UPDATE receipts SET state='confirmed',confirmed_at=? WHERE id=?", (time, receipt_id))
    return row_json(db.execute('SELECT * FROM receipts WHERE id=?', (receipt_id,)).fetchone(), ['lines'])

def unit_cost_history(db, revisions):
    """Project saved receipt revisions onto units in original allocation order.

    Keep this separate from physical stock events: historical revisions remain
    the durable audit source, including corrections made before this view existed.
    """
    batches = {}
    history = []
    for revision in revisions:
        receipt_id = revision['receipt_id']
        if receipt_id not in batches:
            batches[receipt_id] = [dict(r) for r in db.execute(
                'SELECT id,product_id FROM stock_units WHERE receipt_id=? ORDER BY rowid', (receipt_id,))]
        batch = batches[receipt_id]
        values = []
        for key in ('before_json', 'after_json'):
            receipt = revision[key]
            expected = [line['product_id'] for line in receipt['lines'] for _ in range(line['quantity'])]
            if expected != [unit['product_id'] for unit in batch]:
                raise ConflictError('成本修訂與入庫商品件不一致，無法顯示逐件成本紀錄')
            costs = None if receipt['cost_status'] == 'pending' else receipt_costs(
                receipt['lines'], receipt['freight_cents'], receipt['tax_cents'], receipt['allocation'])
            values.append(costs if costs is not None else [None] * len(batch))
        for index, unit in enumerate(batch):
            history.append(dict(id=f"cost/{receipt_id}/{revision['version']}/{unit['id']}",
                kind='cost_revision', unit_id=unit['id'], receipt_id=receipt_id,
                time=revision['time'], details=dict(before_cost_cents=values[0][index],
                    after_cost_cents=values[1][index], reason=revision['reason'],
                    cost_version=revision['version'], operation_id=revision['operation_id'])))
    return history


def shipment_time_snapshot(db, shipment_ids=None):
    """Read original operation times without changing historic business rows."""
    condition = '' if shipment_ids is None else ' WHERE id IN ('+','.join('?' for _ in shipment_ids)+')'
    shipments = [row_json(r, ['lines']) for r in db.execute('SELECT * FROM shipments'+condition+' ORDER BY rowid DESC',shipment_ids or [])]
    by_id = {s['id']: s for s in shipments}
    for shipment in shipments:
        shipment.update(first_completed_at=None, cancelled_at=None)
    cancelled_cycles = set()
    for event in db.execute("""SELECT * FROM events WHERE action='shipment_ship/'||target
            OR action='shipment_cancel/'||target OR action='shipment_cancel_staged/'||target ORDER BY rowid"""):
        shipment = by_id.get(event['target'])
        if shipment is None:
            continue
        result = json.loads(event['details']).get('result', {})
        if result.get('state') == 'shipped' and result.get('shipped_at'):
            if shipment['first_completed_at'] is None:
                shipment['first_completed_at'] = result['shipped_at']
        elif result.get('state') == 'cancelled':
            cycle = (shipment['id'], result.get('reopen_count', 0))
            if cycle not in cancelled_cycles:
                cancelled_cycles.add(cycle)
                shipment['cancelled_at'] = event['time']
    # Source-order cancellations have their own durable per-document events.
    for event in db.execute("SELECT shipment_id,time FROM stock_events WHERE kind='order_cancel' ORDER BY rowid"):
        shipment = by_id.get(event['shipment_id'])
        if shipment and (not shipment['cancelled_at'] or event['time'] > shipment['cancelled_at']):
            shipment['cancelled_at'] = event['time']
    # Legacy rows may have physical events but no command audit; never invent a date.
    for event in db.execute("SELECT shipment_id,MIN(time) AS time FROM stock_events WHERE kind='ship' GROUP BY shipment_id"):
        shipment = by_id.get(event['shipment_id'])
        if shipment and shipment['first_completed_at'] is None:
            shipment['first_completed_at'] = event['time']
    return shipments


class Stock:
    def __init__(self, store):
        self.store = store

    def shipment_page(self, page=1, q='', state='', source=''):
        if type(page) is not int or page<1 or page>1000000:
            raise ValueError('頁碼須為正整數')
        states={'preparing':('draft','allocated','picked','staged'),'complete':('shipped',),'cancel':('cancelled',)}
        if not all(isinstance(v,str) and len(v)<=200 for v in (q,state,source)) or state not in ('',*states):
            raise ValueError('出貨篩選條件不正確')
        with self.store.connect() as db:
            db.execute('BEGIN')
            db.create_function('casefold',1,lambda value:str(value or '').casefold(),deterministic=True)
            where=[];args=[]
            if state:
                where.append('s.state IN ('+','.join('?' for _ in states[state])+')');args.extend(states[state])
            if source:
                where.append('s.channel=?');args.append(source)
            if q.strip():
                where.append('''(instr(casefold(s.number),?)>0 OR instr(casefold(s.channel),?)>0 OR
                    instr(casefold(s.comment),?)>0 OR instr(casefold(g.order_id),?)>0 OR EXISTS(
                    SELECT 1 FROM json_each(s.lines) l LEFT JOIN products p ON p.id=json_extract(l.value,'$.product_id')
                    LEFT JOIN product_details d ON d.product_id=p.id WHERE instr(casefold(p.name),?)>0 OR
                    instr(casefold(d.sku),?)>0 OR instr(casefold(json_extract(l.value,'$.item_name')),?)>0))''')
                args.extend([q.strip().casefold()]*7)
            condition=' WHERE '+' AND '.join(where) if where else ''
            tables=' FROM shipments s JOIN fulfillment_groups g ON g.id=s.group_id'
            total=db.execute('SELECT count(*)'+tables+condition,args).fetchone()[0]
            pages=max(1,(total+49)//50);page=min(page,pages)
            ids=[r[0] for r in db.execute('SELECT s.id'+tables+condition+' ORDER BY s.rowid DESC LIMIT 50 OFFSET ?',[*args,(page-1)*50])]
            shipments=shipment_time_snapshot(db,ids)
            # List rows do not need the potentially large document line payload.
            for shipment in shipments:
                shipment.pop('lines')
            channels=[r[0] for r in db.execute("SELECT DISTINCT channel FROM shipments WHERE channel<>'' ORDER BY channel")]
            return dict(shipments=shipments,total=total,page=page,pages=pages,page_size=50,channels=channels)

    def history(self, scope, record_id):
        tables = {'unit': 'stock_units', 'shipment': 'shipments', 'receipt': 'receipts'}
        if scope not in tables:
            raise ValueError('未知歷史範圍')
        with self.store.connect() as db:
            db.execute('BEGIN')
            record = db.execute(f'SELECT * FROM {tables[scope]} WHERE id=?', (record_id,)).fetchone()
            if record is None:
                raise KeyError('單據不存在')
            receipt_id = record['receipt_id'] if scope == 'unit' else record_id if scope == 'receipt' else None
            revisions = [row_json(r, ['before_json', 'after_json']) for r in db.execute(
                'SELECT * FROM receipt_cost_revisions WHERE receipt_id=? ORDER BY rowid DESC', (receipt_id,))]
            if scope == 'shipment':
                # Include other cycles/shipments of these units so returns and reassignment
                # use the same effective physical history as the complete snapshot.
                events = db.execute('''SELECT * FROM stock_events WHERE shipment_id=? OR unit_id IN
                    (SELECT unit_id FROM stock_events WHERE shipment_id=? AND unit_id IS NOT NULL
                     UNION SELECT unit_id FROM allocations WHERE shipment_id=?) ORDER BY rowid DESC''',
                    (record_id, record_id, record_id))
            elif scope == 'unit':
                events = db.execute('SELECT * FROM stock_events WHERE unit_id=? ORDER BY rowid DESC', (record_id,))
            else:
                events = []
            return dict(events=[row_json(r, ['details']) for r in events], cost_revisions=revisions,
                unit_cost_revisions=[e for e in unit_cost_history(db, revisions) if e['unit_id'] == record_id] if scope == 'unit' else [])

    def snapshot(self, *, summary=False):
        with self.store.connect() as db:
            db.execute('BEGIN')
            units = [dict(r) for r in db.execute('''SELECT u.*,a.shipment_id,r.cost_status,r.cost_version FROM stock_units u
                JOIN receipts r ON r.id=u.receipt_id LEFT JOIN allocations a ON a.unit_id=u.id ORDER BY u.fifo_at,u.id''')]
            storage_enabled = locations_enabled(db)
            places = {r['id']: dict(r) for r in db.execute('SELECT * FROM storage_places')}
            transfers = {r['unit_id']: r['transfer_id'] for r in db.execute('SELECT * FROM storage_transfer_units')}
            holds = {r[0] for r in db.execute('''SELECT r.unit_id FROM stock_returns r LEFT JOIN stock_return_releases x
                ON x.ship_event_id=r.ship_event_id WHERE x.ship_event_id IS NULL''')}
            returned = {r['unit_id']: r['ship_event_id'] for r in db.execute('SELECT * FROM stock_returns ORDER BY rowid')}
            last_shipments = {r['unit_id']: r['shipment_id'] for r in db.execute('''SELECT unit_id,shipment_id FROM stock_events e
                WHERE kind='ship' AND NOT EXISTS(SELECT 1 FROM stock_ship_reversals r WHERE r.ship_event_id=e.id) ORDER BY rowid''')}
            by_product = {}
            for unit in units:
                if unit['cost_status'] == 'pending': unit['cost_cents'] = None
                unit['storage_required'] = storage_enabled
                place = places.get(unit['place_id'])
                unit['transfer_id'] = transfers.get(unit['id'])
                unit['place_eligible'] = bool(exportable(unit, place) and not unit['transfer_id'])
                unit['place_label'] = ' / '.join(str(place[k]) for k in ('address','position','name') if place[k]) if place else '未配櫃'
                unit['return_hold'] = unit['id'] in holds
                unit['return_ship_event_id'] = returned.get(unit['id'])
                unit['last_shipment_id'] = last_shipments.get(unit['id'])
                by_product.setdefault(unit['product_id'], []).append(unit)
            products = []
            for row in db.execute("SELECT id FROM products WHERE kind='product' ORDER BY rowid"):
                p = product(db, row[0])
                product_units = by_product.get(p['id'], [])
                own = [u for u in product_units if u['location'] == 'warehouse']
                physical = [u for u in product_units if u['location'] in ('warehouse','staging')]
                staged = sum(u['location']=='staging' for u in product_units)
                p.update(on_hand=len(own)+staged, warehouse=len(own), staged=staged, allocated=sum(bool(u['shipment_id']) for u in own),
                    available=sum(u['quality'] == 'good' and u['packaging'] == 'good' and not u['shipment_id'] and not u['return_hold'] and u['place_eligible'] for u in own),
                    box_available=sum(u['quality'] == 'good' and u['packaging'] == 'damaged' and not u['shipment_id'] and not u['return_hold'] and u['place_eligible'] for u in own) if locations_enabled(db) else 0,
                    unassigned=sum(not u['place_id'] for u in physical),
                    transferring=sum(bool(u['transfer_id']) for u in physical),
                    sellable=sum(u['quality'] == 'good' and u['packaging'] == 'good' and not u['return_hold'] for u in own),
                    pending=sum(u['quality']=='pending' for u in physical), defective=sum(u['quality']=='defective' for u in physical),
                    damaged=sum(u['quality']=='damaged' for u in physical), packaging_damaged=sum(u['packaging']=='damaged' for u in physical),
                    return_held=sum(u['return_hold'] for u in own),
                    stock_cost_pending=sum(u['cost_cents'] is None for u in physical),
                    stock_cost_cents=sum(u['cost_cents'] or 0 for u in physical))
                total_cost = p['stock_cost_cents']
                p['stock_cost_known_amount'] = f'{total_cost // 100}.{total_cost % 100:02d}'
                p['stock_cost_amount'] = None if p['stock_cost_pending'] else p['stock_cost_known_amount']
                if p['stock_cost_pending']: p['stock_cost_cents'] = None
                products.append(p)
            revisions = [] if summary else [row_json(r, ['before_json','after_json']) for r in db.execute('SELECT * FROM receipt_cost_revisions ORDER BY rowid DESC')]
            return dict(products=products, units=units,unit_cost_revisions=unit_cost_history(db, revisions),storage_enabled=locations_enabled(db),places=[dict(r) for r in db.execute('SELECT * FROM storage_places WHERE active=1')],
                alert_settings=[dict(r) for r in db.execute('SELECT * FROM alert_settings')],
                receipts=[row_json(r, ['lines']) for r in db.execute('SELECT r.*,g.purchase_id,p.plan_id AS production_plan_id FROM receipts r LEFT JOIN goods_purchase_receipts g ON g.receipt_id=r.id LEFT JOIN production_receipts p ON p.receipt_id=r.id ORDER BY r.rowid DESC')],
                groups=[row_json(r, ['requirements']) for r in db.execute('SELECT g.*,EXISTS(SELECT 1 FROM manual_dispatch_groups m WHERE m.group_id=g.id) AS manual FROM fulfillment_groups g ORDER BY g.rowid DESC')],
                shipments=shipment_time_snapshot(db),
                work=[dict(r) for r in db.execute('SELECT * FROM shipment_work')],
                order_work=[dict(r) for r in db.execute('SELECT * FROM order_work')],
                additions=[row_json(r,['lines','requirements']) for r in db.execute('SELECT * FROM order_additions ORDER BY rowid')],
                cost_revisions=revisions,
                events=[] if summary else [row_json(r, ['details']) for r in db.execute('SELECT * FROM stock_events ORDER BY rowid DESC')])

    def receipt(self, body):
        def apply(db):
            number = required_text(body.get('number'), '入庫單號', 100)
            metadata = {}
            for field, label, limit in (('source', '進貨來源', 200), ('comment', '備註', 2000)):
                value = body.get(field, '')
                if not isinstance(value, str) or len(value.strip()) > limit:
                    raise ValueError(f'{label}須為最多{limit}字的文字')
                metadata[field] = value.strip()
            items = body.get('lines')
            if not isinstance(items, list) or not items:
                raise ValueError('尚未選擇進貨商品，請先從商品表加入品項')
            lines = []
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError('商品明細格式錯誤')
                p = product(db, item.get('product_id'))
                if not p['active'] or p['kind'] != 'product':
                    raise ValueError('僅啟用中的單品可入庫，套組須以組成商品入庫')
                lines.append(dict(product_id=p['id'], quantity=count(item.get('quantity')), unit_price_cents=optional_money(item.get('unit_price'))))
            if sum(item['quantity'] for item in lines) > 10000:
                raise ValueError('一張入庫單最多10000件')
            freight, tax, allocation = optional_money(body.get('freight', '0')), optional_money(body.get('tax', '0')), body.get('allocation', 'quantity')
            costs = receipt_costs(lines, freight, tax, allocation)
            cost_status = 'pending' if costs is None else 'complete'
            if body.get('id'):
                previous=row_json(db.execute('SELECT * FROM receipts WHERE id=?',(body['id'],)).fetchone(),['lines'])
                if previous['state'] not in ('draft','ordered'):
                    raise ConflictError('已入庫或已取消的單據不可覆寫')
                expected={k:previous[k] for k in ('number','lines','freight_cents','allocation','source','comment')}
                provided=body.get('expected_snapshot')
                if isinstance(provided,dict):
                    provided={'source':'','comment':'',**provided}
                    if 'tax_cents' in provided or 'cost_version' in provided:
                        expected.update(tax_cents=previous['tax_cents'],cost_version=previous['cost_version'])
                if provided!=expected:
                    raise ConflictError('入庫草稿已更新，請重新讀取後再編輯')
                try:
                    db.execute('''UPDATE receipts SET number=?,lines=?,freight_cents=?,allocation=?,source=?,comment=?,
                        freight_known=?,tax_cents=?,tax_known=?,cost_status=?,cost_version=cost_version+1 WHERE id=?''',
                        (number,encode(lines),freight or 0,allocation,metadata['source'],metadata['comment'],freight is not None,tax or 0,tax is not None,cost_status,body['id']))
                except sqlite3.IntegrityError:
                    raise ConflictError('入庫單號已存在') from None
                changed=row_json(db.execute('SELECT * FROM receipts WHERE id=?',(body['id'],)).fetchone(),['lines'])
                self.store.event(db,'receipt_edit',body['id'],dict(operation_id=body['operation_id'],before=previous,after=changed))
                return changed
            receipt_id = uuid4().hex
            try:
                db.execute('''INSERT INTO receipts(id,number,state,lines,freight_cents,allocation,created_at,source,comment,
                    freight_known,tax_cents,tax_known,cost_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (receipt_id, number, 'draft', encode(lines), freight or 0, allocation, now(),metadata['source'],metadata['comment'],freight is not None,tax or 0,tax is not None,cost_status))
            except sqlite3.IntegrityError:
                raise ConflictError('入庫單號已存在') from None
            return row_json(db.execute('SELECT * FROM receipts WHERE id=?', (receipt_id,)).fetchone(), ['lines'])
        return command(self.store, 'receipt_create', body, apply)

    def receipt_stage(self, receipt_id, action, body):
        if action not in ('order','cancel'):
            raise ValueError('無效的進貨單操作')
        def apply(db):
            receipt=row_json(db.execute('SELECT * FROM receipts WHERE id=?',(receipt_id,)).fetchone(),['lines'])
            reason=required_text(body.get('reason'),'取消原因',500) if action=='cancel' else None
            target='ordered' if action=='order' else 'cancelled'
            if receipt['state']==target:
                return receipt
            if receipt['state'] not in ('draft','ordered'):
                raise ConflictError('已入庫或已取消的單據不能執行此操作')
            if action=='order':
                for line in receipt['lines']:
                    if not product(db,line['product_id'])['active']:
                        raise ConflictError('商品已停用，請先核對進貨內容')
                db.execute("UPDATE receipts SET state='ordered',ordered_at=? WHERE id=?",(now(),receipt_id))
            else:
                db.execute("UPDATE receipts SET state='cancelled',cancelled_at=?,cancel_reason=? WHERE id=?",(now(),reason,receipt_id))
            result=row_json(db.execute('SELECT * FROM receipts WHERE id=?',(receipt_id,)).fetchone(),['lines'])
            self.store.event(db,'receipt_state',receipt_id,dict(operation_id=body['operation_id'],before=receipt,after=result))
            return result
        return command(self.store,f'receipt_{action}/{receipt_id}',body,apply)

    def confirm_receipt(self, receipt_id, body):
        return command(self.store, f'receipt_confirm/{receipt_id}', body,
                       lambda db: confirm_receipt_in_transaction(db, receipt_id, body))

    def revise_receipt_cost(self, receipt_id, body):
        def apply(db):
            before = row_json(db.execute('SELECT * FROM receipts WHERE id=?', (receipt_id,)).fetchone(), ['lines'])
            if before['state'] != 'confirmed':
                raise ConflictError('僅已入庫單可更正成本，草稿請使用編輯')
            if type(body.get('expected_cost_version')) is not int or body['expected_cost_version'] != before['cost_version']:
                raise ConflictError('入庫費用已更新，請重新整理後再操作')
            reason = required_text(body.get('reason'), '費用修正原因', 2000)
            items = body.get('lines')
            if not isinstance(items, list) or len(items) != len(before['lines']):
                raise ValueError('成本修正不可變更入庫商品與數量')
            lines = []
            for old, item in zip(before['lines'], items):
                if not isinstance(item, dict) or item.get('product_id') != old['product_id'] or item.get('quantity', old['quantity']) != old['quantity']:
                    raise ValueError('成本修正不可變更入庫商品與數量或順序')
                lines.append({**old, 'unit_price_cents': optional_money(item.get('unit_price'))})
            freight, tax = optional_money(body.get('freight')), optional_money(body.get('tax', '0'))
            allocation = body.get('allocation', before['allocation'])
            costs = receipt_costs(lines, freight, tax, allocation)
            units = db.execute('SELECT * FROM stock_units WHERE receipt_id=? ORDER BY rowid', (receipt_id,)).fetchall()
            expected_products = [line['product_id'] for line in lines for _ in range(line['quantity'])]
            if [u['product_id'] for u in units] != expected_products:
                raise ConflictError('入庫商品件與原明細不一致，無法更正費用')
            status = 'pending' if costs is None else 'complete'
            for index, unit in enumerate(units):
                db.execute('UPDATE stock_units SET cost_cents=? WHERE id=?', (costs[index] if costs is not None else 0, unit['id']))
            db.execute('''UPDATE receipts SET lines=?,freight_cents=?,freight_known=?,tax_cents=?,tax_known=?,
                allocation=?,cost_status=?,cost_version=cost_version+1 WHERE id=?''',
                (encode(lines),freight or 0,freight is not None,tax or 0,tax is not None,allocation,status,receipt_id))
            after = row_json(db.execute('SELECT * FROM receipts WHERE id=?', (receipt_id,)).fetchone(), ['lines'])
            db.execute('INSERT INTO receipt_cost_revisions VALUES(?,?,?,?,?,?,?)',
                (receipt_id,after['cost_version'],now(),body['operation_id'],reason,encode(before),encode(after)))
            self.store.event(db,'receipt_cost_revision',receipt_id,dict(before=before,after=after,reason=reason,operation_id=body['operation_id']))
            return after
        return command(self.store, f'receipt_cost/{receipt_id}', body, apply)

    def shipment(self, body):
        def apply(db):
            def blank(number, group_id=None, parent_id=None):
                metadata={}
                for field, limit in [('channel',200),('comment',2000)]:
                    value=body.get(field,'')
                    if not isinstance(value,str) or len(value)>limit:
                        raise ValueError('通路或備註格式不正確')
                    metadata[field]=value.strip()
                if group_id is None:
                    group_id=uuid4().hex
                    db.execute('INSERT INTO fulfillment_groups VALUES(?,?,?,?,?)',(group_id,'',number,0,'{}'))
                    db.execute('INSERT INTO manual_dispatch_groups VALUES(?)',(group_id,))
                sid=uuid4().hex
                db.execute('INSERT INTO shipments(id,group_id,parent_id,number,state,lines,created_at,channel,comment) VALUES(?,?,?,?,?,?,?,?,?)',
                           (sid,group_id,parent_id,number,'draft','[]',now(),metadata['channel'],metadata['comment']))
                return row_json(db.execute('SELECT * FROM shipments WHERE id=?',(sid,)).fetchone(),['lines'])
            number = body.get('number')
            if 'entry_number' in body and not body.get('parent_id'):
                entry = required_text(body.get('entry_number'), '訂單編號', 100)
                sources = [o for o in self.store.current_orders(db) if o['order_id'] == entry]
                if len(sources) > 1:
                    raise ConflictError('不同來源有相同訂單編號，請指定來源訂單建立出貨單')
                if sources and sources[0]['status'] != 'active':
                    raise ConflictError('來源訂單已取消，不能建立出貨單；歷史出貨請從列表查看')
                existing = db.execute('SELECT * FROM shipments WHERE number=?', (entry,)).fetchone()
                if existing is None:
                    matches = db.execute('''SELECT s.* FROM shipments s JOIN fulfillment_groups g ON g.id=s.group_id
                        WHERE g.order_id=? AND s.parent_id IS NULL''', (entry,)).fetchall()
                    if len(matches) > 1:
                        raise ConflictError('不同來源有相同訂單編號，請從既有出貨列表開啟')
                    existing = matches[0] if matches else None
                if existing is not None:
                    if sources:
                        origin = db.execute('SELECT * FROM fulfillment_groups WHERE id=?', (existing['group_id'],)).fetchone()
                        if (origin['account_id'], origin['order_id']) != (sources[0]['account_id'], entry):
                            raise ConflictError('此號碼已用於其他或獨立出貨單，請從原訂單使用不同出貨單號建立，不自動改寫關聯')
                    return row_json(existing, ['lines'])
                if not sources:
                    return blank(entry)
                account, order_id, number = sources[0]['account_id'], entry, entry
            elif body.get('parent_id'):
                main = db.execute('SELECT * FROM shipments WHERE id=?', (body['parent_id'],)).fetchone()
                if main is None or main['parent_id']:
                    raise ValueError('請從主單建立副單')
                original = db.execute('SELECT * FROM fulfillment_groups WHERE id=?', (main['group_id'],)).fetchone()
                account, order_id = original['account_id'], original['order_id']
                number = required_text(body.get('number', body.get('entry_number')), '副單條碼', 100)
                if db.execute('SELECT 1 FROM shipments WHERE number=?', (number,)).fetchone():
                    raise ConflictError('此出貨單條碼已存在，請使用不同條碼')
                if db.execute('SELECT 1 FROM manual_dispatch_groups WHERE group_id=?',(main['group_id'],)).fetchone():
                    return blank(number,main['group_id'],main['id'])
            else:
                account = required_text(body.get('account_id'), '訂單來源', 200)
                order_id = required_text(body.get('order_id'), '訂單編號', 100)
            order = next((o for o in self.store.current_orders(db) if o['account_id'] == account and o['order_id'] == order_id), None)
            if order is None or order['status'] != 'active':
                raise ValueError('只可為已保存的有效訂單建立出貨單')
            group = db.execute('SELECT * FROM fulfillment_groups WHERE account_id=? AND order_id=?', (account, order_id)).fetchone()
            if group is None:
                requirements = {}
                for line in order['lines']:
                    if line.get('line_type') == 'non_stock':
                        continue
                    for part in line.get('components') or [dict(product_id=line['product_id'], quantity=1)]:
                        key = part['product_id']
                        requirements[key] = requirements.get(key, 0) + line['quantity'] * part['quantity']
                group_id = uuid4().hex
                for addition in db.execute('SELECT requirements FROM order_additions WHERE account_id=? AND order_id=?',(account,order_id)):
                    for pid,q in json.loads(addition[0]).items():
                        requirements[pid]=requirements.get(pid,0)+q
                db.execute('INSERT INTO fulfillment_groups VALUES(?,?,?,?,?)', (group_id, account, order_id, order['revision'], encode(requirements)))
                group = db.execute('SELECT * FROM fulfillment_groups WHERE id=?', (group_id,)).fetchone()
            if group['revision'] != order['revision']:
                raise ConflictError('訂單已修訂，履約需求待核對；不自動改寫出貨需求')
            if body.get('parent_id'):
                # A child document has its own lifecycle. Quantity limits apply
                # when adding stock, not when creating an empty document.
                return blank(number, group['id'], main['id'])
            requirements = json.loads(group['requirements'])
            remaining = dict(requirements)
            for committed in db.execute("SELECT lines FROM shipments WHERE group_id=? AND state IN ('allocated','picked','staged','shipped')", (group['id'],)):
                for line in json.loads(committed[0]):
                    remaining[line['product_id']] -= line['quantity']
            if not any(quantity > 0 for quantity in remaining.values()):
                raise ConflictError('此訂單已全部配貨或出貨，請查看既有出貨單；取消未出貨單後才能重新安排')
            items = body.get('lines', [dict(product_id=p, quantity=q) for p, q in remaining.items() if q > 0])
            if not isinstance(items, list) or not items:
                raise ValueError('出貨明細為空，請重新選擇原訂單帶入待出貨商品')
            lines, seen = [], set()
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError('出貨商品格式錯誤')
                p = product(db, item.get('product_id'))
                quantity = count(item.get('quantity'))
                if p['id'] in seen or quantity > requirements.get(p['id'], 0):
                    raise ValueError('商品須在訂單需求內且不得重複或超出原需求')
                seen.add(p['id'])
                lines.append(dict(product_id=p['id'], quantity=quantity))
            parent = db.execute('SELECT id FROM shipments WHERE group_id=? ORDER BY rowid LIMIT 1', (group['id'],)).fetchone()
            shipment_id = uuid4().hex
            metadata = {}
            for field, label, limit in [('channel', '出貨通路', 200), ('comment', '備註', 2000)]:
                value = body.get(field, '')
                if not isinstance(value, str) or len(value) > limit:
                    raise ValueError(f'{label}須為最多{limit}字元的文字')
                metadata[field] = value.strip()
            try:
                db.execute('INSERT INTO shipments(id,group_id,parent_id,number,state,lines,created_at,channel,comment) VALUES(?,?,?,?,?,?,?,?,?)', (shipment_id, group['id'], parent[0] if parent else None,
                    required_text(number, '出貨單號', 100), 'draft', encode(lines), now(), metadata['channel'], metadata['comment']))
            except sqlite3.IntegrityError:
                raise ConflictError('出貨單號已存在') from None
            return row_json(db.execute('SELECT * FROM shipments WHERE id=?', (shipment_id,)).fetchone(), ['lines'])
        return command(self.store, 'shipment_create', body, apply)

    def update_shipment_lines(self, shipment_id, body):
        def apply(db):
            if db.execute('SELECT 1 FROM scanned_shipments WHERE shipment_id=?',(shipment_id,)).fetchone():
                raise ConflictError('此單使用逐件掃碼，請掃描加入或取消該件，不能直接修改數量')
            shipment = row_json(db.execute('SELECT * FROM shipments WHERE id=?', (shipment_id,)).fetchone(), ['lines'])
            if shipment['state'] != 'draft':
                raise ConflictError('只有尚未配貨的出貨單可修改商品')
            if body.get('expected_lines') != shipment['lines']:
                raise ConflictError('出貨商品已更新，請重新讀取')
            group = db.execute('SELECT * FROM fulfillment_groups WHERE id=?', (shipment['group_id'],)).fetchone()
            manual = db.execute('SELECT 1 FROM manual_dispatch_groups WHERE group_id=?',(group['id'],)).fetchone() is not None
            remaining = json.loads(group['requirements'])
            for row in db.execute("SELECT lines FROM shipments WHERE group_id=? AND state IN ('allocated','picked','staged','shipped')", (group['id'],)):
                for line in json.loads(row[0]):
                    remaining[line['product_id']] -= line['quantity']
            items = body.get('lines')
            if not isinstance(items, list) or not items:
                raise ValueError('請保留至少一項出貨商品')
            lines, seen = [], set()
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError('出貨商品格式錯誤')
                p = product(db, item.get('product_id'))
                quantity = count(item.get('quantity'))
                if p['id'] in seen or (not manual and quantity > remaining.get(p['id'], 0)):
                    raise ValueError('商品不得重複或超出訂單待出貨數量')
                seen.add(p['id'])
                lines.append(dict(product_id=p['id'], quantity=quantity))
            db.execute('UPDATE shipments SET lines=? WHERE id=?', (encode(lines), shipment_id))
            if manual:
                requirements={}
                for row in db.execute("SELECT lines FROM shipments WHERE group_id=? AND state!='cancelled'",(group['id'],)):
                    for line in json.loads(row[0]):
                        requirements[line['product_id']]=requirements.get(line['product_id'],0)+line['quantity']
                db.execute('UPDATE fulfillment_groups SET requirements=? WHERE id=?',(encode(requirements),group['id']))
            stock_event(db, body, 'shipment_lines', None, dict(before=shipment['lines'], after=lines), shipment_id=shipment_id)
            return row_json(db.execute('SELECT * FROM shipments WHERE id=?', (shipment_id,)).fetchone(), ['lines'])
        return command(self.store, 'shipment_lines', body, apply)

    def scan(self, shipment_id, body):
        def apply(db):
            shipment=row_json(db.execute('SELECT * FROM shipments WHERE id=?',(shipment_id,)).fetchone(),['lines'])
            check_reopen_count(shipment, body)
            if shipment['state'] not in ('draft','allocated'):
                raise ConflictError('此單已進入揀貨或出貨階段，不能再加入商品')
            barcode=required_text(body.get('barcode'),'商品條碼',200)
            box_requested=barcode.startswith('#') and locations_enabled(db)
            if box_requested:barcode=barcode[1:]
            row=db.execute('SELECT product_id FROM product_details WHERE barcode=?',(barcode,)).fetchone()
            if row is None:
                raise ValueError('找不到此商品條碼，請先在SKU商品庫建立')
            p=product(db,row[0])
            group=row_json(db.execute('SELECT * FROM fulfillment_groups WHERE id=?',(shipment['group_id'],)).fetchone(),['requirements'])
            guard_manual_source(db, group)
            if not db.execute('SELECT 1 FROM manual_dispatch_groups WHERE group_id=?',(group['id'],)).fetchone():
                order=next((o for o in self.store.current_orders(db) if o['account_id']==group['account_id'] and o['order_id']==group['order_id']),None)
                if not order or order['status']!='active' or order['revision']!=group['revision']:
                    raise ConflictError('來源訂單已變動，不能加入商品')
                committed=sum(line['quantity'] for s in db.execute("SELECT lines FROM shipments WHERE group_id=? AND state IN ('allocated','picked','staged','shipped')",(group['id'],)) for line in json.loads(s[0]) if line['product_id']==p['id'])
                if committed+1>group['requirements'].get(p['id'],0):
                    raise ConflictError('已達原訂單待出貨數量，不能再加入此商品')
            unit=db.execute('''SELECT u.* FROM stock_units u LEFT JOIN allocations a ON a.unit_id=u.id
                WHERE u.product_id=? AND u.location='warehouse' AND u.quality='good' AND u.packaging=? AND a.unit_id IS NULL
                AND NOT EXISTS (SELECT 1 FROM stock_returns r LEFT JOIN stock_return_releases x ON x.ship_event_id=r.ship_event_id WHERE r.unit_id=u.id AND x.ship_event_id IS NULL)
                '''+LOCATION_SQL+''' ORDER BY u.fifo_at,u.id LIMIT 1''',(p['id'],'damaged' if box_requested else 'good')).fetchone()
            if unit is None:
                raise ConflictError('此商品沒有可出貨的庫存件，本次未加入')
            db.execute('INSERT OR IGNORE INTO scanned_shipments VALUES(?)',(shipment_id,))
            db.execute('INSERT INTO allocations VALUES(?,?)',(unit['id'],shipment_id))
            stock_event(db,body,'allocate',unit['id'],dict(product_id=p['id'],group_id=group['id'],barcode=barcode),shipment_id=shipment_id)
            lines=sync_scanned_lines(db,shipment_id)
            stock_event(db,body,'shipment_lines',None,dict(before=shipment['lines'],after=lines,reason='barcode_scan'),shipment_id=shipment_id)
            return dict(id=shipment_id,unit_id=unit['id'],lines=lines)
        return command(self.store,f'shipment_scan/{shipment_id}',body,apply)

    def cancel_item(self, shipment_id, unit_id, body):
        def apply(db):
            shipment=row_json(db.execute('SELECT * FROM shipments WHERE id=?',(shipment_id,)).fetchone(),['lines'])
            check_reopen_count(shipment, body)
            owned=db.execute('SELECT 1 FROM allocations WHERE shipment_id=? AND unit_id=?',(shipment_id,unit_id)).fetchone()
            if not owned:
                if db.execute("SELECT 1 FROM stock_events WHERE shipment_id=? AND unit_id=? AND kind='release'",(shipment_id,unit_id)).fetchone():
                    return dict(id=shipment_id,unit_id=unit_id,cancelled=True)
                raise ConflictError('此件不屬於本單，不能取消')
            unit=db.execute('SELECT * FROM stock_units WHERE id=?',(unit_id,)).fetchone()
            if shipment['state'] not in ('allocated','picked') or unit['location']!='warehouse':
                raise ConflictError('已扣庫或交寄的商品，請走待寄取消或實際退貨入庫')
            db.execute('DELETE FROM allocations WHERE shipment_id=? AND unit_id=?',(shipment_id,unit_id))
            stock_event(db,body,'release',unit_id,dict(reason='cancel_item'),shipment_id=shipment_id)
            db.execute('INSERT OR IGNORE INTO scanned_shipments VALUES(?)',(shipment_id,))
            lines=sync_scanned_lines(db,shipment_id)
            stock_event(db,body,'shipment_lines',None,dict(before=shipment['lines'],after=lines,reason='cancel_item'),shipment_id=shipment_id)
            return dict(id=shipment_id,unit_id=unit_id,cancelled=True)
        return command(self.store,f'shipment_cancel_item/{shipment_id}/{unit_id}',body,apply)

    def transition(self, shipment_id, action, body):
        if action == 'reopen':
            return self.reopen_shipment(shipment_id, body)
        if action == 'complete':
            action = 'ship'
        if action not in ('allocate', 'pick', 'deduct', 'ship', 'cancel'):
            raise ValueError('未知出貨操作')
        def apply(db):
            shipment = row_json(db.execute('SELECT * FROM shipments WHERE id=?', (shipment_id,)).fetchone(), ['lines'])
            check_reopen_count(shipment, body)
            target = dict(allocate='allocated', pick='picked', deduct='staged', ship='shipped', cancel='cancelled')[action]
            if shipment['state'] == target:
                return shipment
            if shipment['state'] in ('cancelled', 'shipped'):
                raise ConflictError('完成單須先回到準備中才能再完成或取消；已取消單不能改回')
            if shipment['state']=='staged' and action!='ship':
                raise ConflictError('此單已扣庫待寄；重開不會補庫，實際取消須走待寄撤銷與歸回確認')
            group = row_json(db.execute('SELECT * FROM fulfillment_groups WHERE id=?', (shipment['group_id'],)).fetchone(), ['requirements'])
            if action != 'cancel':
                guard_manual_source(db, group)
            if action in ('allocate', 'deduct', 'ship', 'pick') and not db.execute('SELECT 1 FROM manual_dispatch_groups WHERE group_id=?',(group['id'],)).fetchone():
                order = next((o for o in self.store.current_orders(db) if o['account_id'] == group['account_id'] and o['order_id'] == group['order_id']), None)
                if order is None or order['revision'] != group['revision'] or order['status'] != 'active':
                    raise ConflictError('來源訂單已變動，請核對履約需求；目前不自動改實物流')
            automatic_allocation = action == 'deduct' and shipment['state'] == 'draft'
            if action == 'allocate' or automatic_allocation:
                if not shipment['lines']:
                    raise ConflictError('請先新增並保存出貨商品，再配貨')
                if shipment['state'] != 'draft':
                    raise ConflictError('只有草稿可以配貨')
                barcode = body.get('barcode')
                if barcode:
                    p = db.execute('SELECT product_id FROM product_details WHERE barcode=?', (required_text(barcode, '條碼', 200),)).fetchone()
                    if p is None or p[0] not in {x['product_id'] for x in shipment['lines']}:
                        raise ValueError('條碼不屬於此出貨單的商品')
                committed = {}
                for row in db.execute("SELECT lines FROM shipments WHERE group_id=? AND state IN ('allocated','picked','staged','shipped')", (group['id'],)):
                    for line in json.loads(row[0]):
                        committed[line['product_id']] = committed.get(line['product_id'], 0) + line['quantity']
                for line in shipment['lines']:
                    product_id, quantity = line['product_id'], line['quantity']
                    if committed.get(product_id, 0) + quantity > group['requirements'].get(product_id, 0):
                        raise ConflictError('主副單合計超過此商品剩餘需求')
                    units = db.execute('''SELECT u.* FROM stock_units u LEFT JOIN allocations a ON a.unit_id=u.id
                        WHERE u.product_id=? AND u.location='warehouse' AND u.quality='good' AND u.packaging='good' AND a.unit_id IS NULL
                        AND NOT EXISTS (SELECT 1 FROM stock_returns r LEFT JOIN stock_return_releases x ON x.ship_event_id=r.ship_event_id WHERE r.unit_id=u.id AND x.ship_event_id IS NULL)
                        '''+LOCATION_SQL+''' ORDER BY u.fifo_at,u.id LIMIT ?''', (product_id, quantity)).fetchall()
                    if len(units) < quantity:
                        raise ConflictError(f'庫存不足，需{quantity}件、可用{len(units)}件；本次未配貨')
                    for unit in units:
                        db.execute('INSERT INTO allocations VALUES(?,?)', (unit['id'], shipment_id))
                        stock_event(db, body, 'allocate', unit['id'], dict(product_id=product_id, group_id=group['id']), shipment_id=shipment_id)
                if automatic_allocation:
                    shipment['state'] = 'allocated'
            if action in ('ship', 'pick', 'deduct'):
                if shipment['state'] not in ('allocated', 'picked', 'staged'):
                    raise ConflictError('請先完成配貨')
                units = db.execute('SELECT u.* FROM stock_units u JOIN allocations a ON a.unit_id=u.id WHERE a.shipment_id=?', (shipment_id,)).fetchall()
                expected_location='staging' if shipment['state']=='staged' else 'warehouse'
                if len(units) != sum(x['quantity'] for x in shipment['lines']) or any(u['location'] != expected_location or u['quality'] != 'good' or (u['packaging'] != 'good' and not locations_enabled(db)) or return_hold(db,u['id']) or not location_eligible(db,u) for u in units):
                    raise ConflictError('占用件或品質狀態已變動，不能確認出貨')
                for unit in units:
                    if action in ('deduct','ship') and expected_location=='warehouse':
                        db.execute("UPDATE stock_units SET location='staging' WHERE id=?",(unit['id'],))
                        stock_event(db,body,'deduct',unit['id'],dict(before=dict(unit),product_id=unit['product_id'],group_id=group['id'],cost_cents=unit['cost_cents']),shipment_id=shipment_id)
                    if action == 'ship':
                        db.execute("UPDATE stock_units SET location='customer' WHERE id=?", (unit['id'],))
                    if action!='deduct':
                        stock_event(db, body, action, unit['id'], dict(before={**dict(unit),'location':'staging'} if action=='ship' else dict(unit),product_id=unit['product_id'], group_id=group['id'], cost_cents=unit['cost_cents']), shipment_id=shipment_id)
                if action == 'ship':
                    db.execute('DELETE FROM allocations WHERE shipment_id=?', (shipment_id,))
            elif action == 'cancel':
                for unit in db.execute('SELECT unit_id FROM allocations WHERE shipment_id=?', (shipment_id,)).fetchall():
                    stock_event(db, body, 'release', unit[0], dict(group_id=group['id']), shipment_id=shipment_id)
                db.execute('DELETE FROM allocations WHERE shipment_id=?', (shipment_id,))
            db.execute('UPDATE shipments SET state=?,shipped_at=? WHERE id=?', (target, now() if action == 'ship' else None, shipment_id))
            return row_json(db.execute('SELECT * FROM shipments WHERE id=?', (shipment_id,)).fetchone(), ['lines'])
        return command(self.store, f'shipment_{action}/{shipment_id}', body, apply)

    def reopen_shipment(self, shipment_id, body):
        def apply(db):
            shipment=row_json(db.execute('SELECT * FROM shipments WHERE id=?',(shipment_id,)).fetchone(),['lines'])
            if 'expected_reopen_count' not in body:
                raise ConflictError('請重新整理出貨單後再回到準備中')
            check_reopen_count(shipment, body)
            if body.get('expected_state') not in (None,shipment['state']):
                raise ConflictError('出貨單狀態已變動，請重新整理後再操作')
            if shipment['state']=='cancelled':
                if body.get('expected_state')!='cancelled':
                    raise ConflictError('請重新讀取取消單，再回到準備中')
                if db.execute('SELECT 1 FROM allocations WHERE shipment_id=?',(shipment_id,)).fetchone():
                    raise ConflictError('取消單仍有占用紀錄，請先核對，不能直接重開')
                before_lines=shipment['lines']
                sync_scanned_lines(db,shipment_id)
                db.execute('INSERT OR IGNORE INTO scanned_shipments VALUES(?)',(shipment_id,))
                db.execute('UPDATE shipments SET shipped_at=NULL,reopen_count=reopen_count+1 WHERE id=?',(shipment_id,))
                stock_event(db,body,'cancel_reopen',None,dict(before_state='cancelled',after_state='draft',
                    before_lines=before_lines,after_lines=[],reason='取消單回準備中；原商品不變，須重新加入商品'),shipment_id=shipment_id)
                return row_json(db.execute('SELECT * FROM shipments WHERE id=?',(shipment_id,)).fetchone(),['lines'])
            if (shipment['state']!='shipped' or not shipment['shipped_at']
                    or body.get('expected_shipped_at')!=shipment['shipped_at']):
                raise ConflictError('只能將目前的完成單回到準備中，請重新整理確認狀態')
            events=db.execute('''SELECT e.* FROM stock_events e WHERE e.shipment_id=? AND e.kind='ship'
                AND NOT EXISTS (SELECT 1 FROM stock_ship_reversals r WHERE r.ship_event_id=e.id) ORDER BY e.rowid''',(shipment_id,)).fetchall()
            expected={line['product_id']:line['quantity'] for line in shipment['lines']}
            actual,units={},[]
            for event in events:
                unit=db.execute('SELECT * FROM stock_units WHERE id=?',(event['unit_id'],)).fetchone()
                latest=db.execute('''SELECT e.id FROM stock_events e WHERE e.unit_id=? AND e.kind='ship'
                    AND NOT EXISTS (SELECT 1 FROM stock_ship_reversals r WHERE r.ship_event_id=e.id)
                    ORDER BY e.rowid DESC LIMIT 1''',(event['unit_id'],)).fetchone()
                if (unit is None or unit['location']!='customer' or latest is None or latest[0]!=event['id']
                        or db.execute('SELECT 1 FROM stock_returns WHERE ship_event_id=?',(event['id'],)).fetchone()
                        or return_hold(db,unit['id'])
                        or db.execute('SELECT 1 FROM allocations WHERE unit_id=?',(unit['id'],)).fetchone()):
                    raise ConflictError('原商品已退貨、轉單或庫存狀態已變動，不能整單回到準備中')
                actual[unit['product_id']]=actual.get(unit['product_id'],0)+1
                units.append((event,unit))
            if not units or actual!=expected or len({u['id'] for _,u in units})!=len(units):
                raise ConflictError('原出貨品號或數量不完整，不能回到準備中')
            at=now()
            for event,unit in units:
                db.execute('INSERT INTO stock_ship_reversals VALUES(?,?,?)',(event['id'],at,body['operation_id']))
                db.execute("UPDATE stock_units SET location='warehouse' WHERE id=?",(unit['id'],))
                db.execute('INSERT INTO allocations VALUES(?,?)',(unit['id'],shipment_id))
                stock_event(db,body,'ship_reopen',unit['id'],dict(ship_event_id=event['id'],product_id=unit['product_id'],
                    group_id=shipment['group_id'],cost_cents=unit['cost_cents'],reason='完成回準備中',
                    previous_shipped_at=shipment['shipped_at']),shipment_id=shipment_id,time=at)
            db.execute("UPDATE shipments SET state='allocated',shipped_at=NULL,reopen_count=reopen_count+1 WHERE id=?",(shipment_id,))
            return row_json(db.execute('SELECT * FROM shipments WHERE id=?',(shipment_id,)).fetchone(),['lines'])
        return command(self.store,f'shipment_reopen/{shipment_id}',body,apply)

    def cancel_staged(self, shipment_id, body):
        def apply(db):
            shipment=row_json(db.execute('SELECT * FROM shipments WHERE id=?',(shipment_id,)).fetchone(),['lines'])
            check_reopen_count(shipment, body)
            reason=required_text(body.get('reason'),'待寄取消原因',200)
            saleable=body.get('returned_to_saleable') is True
            if not saleable and body.get('returned_to_warehouse') is not True:
                raise ValueError('須確認實物尚未交寄且已實際歸回倉內')
            if shipment['state']=='cancelled' and db.execute("SELECT 1 FROM stock_events WHERE shipment_id=? AND kind='deduct_cancel'",(shipment_id,)).fetchone():
                return shipment
            if shipment['state']!='staged':
                raise ConflictError('只可撤銷已扣庫待寄；已交寄須走實際退回')
            units=db.execute('SELECT u.* FROM stock_units u JOIN allocations a ON a.unit_id=u.id WHERE a.shipment_id=?',(shipment_id,)).fetchall()
            if len(units)!=sum(x['quantity'] for x in shipment['lines']) or any(u['location']!='staging' for u in units):
                raise ConflictError('待寄件數或位置不符，不能撤銷扣庫')
            if saleable and any(u['quality']!='good' or u['packaging']!='good' or return_hold(db,u['id']) for u in units):
                raise ConflictError('品質不符，不能直接歸回可售；請確認實物歸回倉內並保留異常')
            for u in units:
                db.execute("UPDATE stock_units SET location='warehouse' WHERE id=?",(u['id'],))
                stock_event(db,body,'deduct_cancel',u['id'],dict(before=dict(u),after_location='warehouse',reason=reason,cost_cents=u['cost_cents']),shipment_id=shipment_id)
            db.execute('DELETE FROM allocations WHERE shipment_id=?',(shipment_id,))
            db.execute("UPDATE shipments SET state='cancelled' WHERE id=?",(shipment_id,))
            return row_json(db.execute('SELECT * FROM shipments WHERE id=?',(shipment_id,)).fetchone(),['lines'])
        return command(self.store,f'shipment_cancel_staged/{shipment_id}',body,apply)

    def set_work_state(self, shipment_id, body):
        def apply(db):
            shipment=row_json(db.execute('SELECT * FROM shipments WHERE id=?',(shipment_id,)).fetchone(),['lines'])
            state=body.get('state')
            if state not in ('open','complete'):
                raise ValueError('工作狀態只接受open或complete')
            reason=required_text(body.get('reason'),'重開／完成原因',200)
            if shipment['state']=='cancelled':
                raise ConflictError('已取消單不能以重開取代新需求')
            old=db.execute('SELECT * FROM shipment_work WHERE shipment_id=?',(shipment_id,)).fetchone()
            before=dict(old) if old else None
            db.execute('INSERT INTO shipment_work VALUES(?,?,?,?) ON CONFLICT(shipment_id) DO UPDATE SET state=excluded.state,reason=excluded.reason,updated_at=excluded.updated_at',(shipment_id,state,reason,now()))
            after=dict(db.execute('SELECT * FROM shipment_work WHERE shipment_id=?',(shipment_id,)).fetchone())
            stock_event(db,body,'work_state',None,dict(before=before,after=after),shipment_id=shipment_id)
            return dict(shipment=shipment,work=after)
        return command(self.store,f'shipment_work/{shipment_id}',body,apply)

    def quality(self, unit_id, body):
        def apply(db):
            row = db.execute('SELECT * FROM stock_units WHERE id=?', (unit_id,)).fetchone()
            if row is None:
                raise KeyError(unit_id)
            before = dict(row)
            if db.execute('SELECT 1 FROM storage_transfer_units WHERE unit_id=?',(unit_id,)).fetchone():
                raise ConflictError('調撥中的商品不能修改品質，請先完成或取消調撥')
            quality, packaging = body.get('quality'), body.get('packaging')
            reason = required_text(body.get('reason') or ('標記瑕疵' if quality == 'defective' else None), '異動原因', 200)
            if before['location'] not in ('warehouse','staging'):
                raise ConflictError('只能變更實際在庫件的品質')
            if quality not in ('good', 'pending', 'defective', 'damaged') or packaging not in ('good', 'damaged'):
                raise ValueError('品質或包裝狀態不正確')
            # The return hold is a durable fact, independent of inspection quality.
            # Ordinary quality changes never replace explicit return inspection.
            if quality == 'good' and return_hold(db,unit_id):
                raise ConflictError('此件仍為退貨入庫；請使用轉回入庫操作，普通品質修改不能解除退貨限制')
            db.execute('UPDATE stock_units SET quality=?,packaging=? WHERE id=?', (quality, packaging, unit_id))
            after = dict(db.execute('SELECT * FROM stock_units WHERE id=?', (unit_id,)).fetchone())
            stock_event(db, body, 'quality', unit_id, dict(before=before, after=after, reason=reason))
            if quality != 'good' or packaging != 'good':
                allocation = db.execute('SELECT shipment_id FROM allocations WHERE unit_id=?', (unit_id,)).fetchone()
                if allocation and before['location']=='warehouse':
                    shipment_id = allocation[0]
                    if db.execute('SELECT 1 FROM scanned_shipments WHERE shipment_id=?',(shipment_id,)).fetchone():
                        db.execute('DELETE FROM allocations WHERE unit_id=?',(unit_id,))
                        stock_event(db,body,'release',unit_id,dict(reason='quality_changed'),shipment_id=shipment_id)
                        sync_scanned_lines(db,shipment_id)
                        return after
                    # Stop the whole unshipped pick so it can be allocated again atomically.
                    for allocated in db.execute('SELECT unit_id FROM allocations WHERE shipment_id=?', (shipment_id,)).fetchall():
                        stock_event(db, body, 'release', allocated[0], dict(reason='quality_changed', affected_unit_id=unit_id), shipment_id=shipment_id)
                    db.execute('DELETE FROM allocations WHERE shipment_id=?', (shipment_id,))
                    db.execute("UPDATE shipments SET state='draft' WHERE id=? AND state IN ('allocated','picked')", (shipment_id,))
                    stock_event(db, body, 'quality_unallocate', None, dict(reason=reason, affected_unit_id=unit_id), shipment_id=shipment_id)
            return after
        return command(self.store, f'unit_quality/{unit_id}', body, apply)

    def restock_return(self, unit_id, body):
        return self.release_return(unit_id, body, restock=True)

    def release_return(self, unit_id, body, restock=False):
        def apply(db):
            ship_event_id=required_text(body.get('ship_event_id'),'原出貨事件',100)
            reason=required_text(body.get('reason'),'入庫原因' if restock else '驗收原因',200)
            if not restock and body.get('inspection_passed') is not True:
                raise ValueError('須明確確認實際驗收品質及包裝合格')
            returned=db.execute('SELECT 1 FROM stock_returns WHERE ship_event_id=? AND unit_id=?',(ship_event_id,unit_id)).fetchone()
            if not returned:
                raise ValueError('找不到此件對應的實退紀錄')
            previous=db.execute('SELECT snapshot FROM stock_return_releases WHERE ship_event_id=?',(ship_event_id,)).fetchone()
            if previous:
                return json.loads(previous[0])
            row=db.execute('SELECT * FROM stock_units WHERE id=?',(unit_id,)).fetchone()
            if row is None or row['location']!='warehouse':
                raise ConflictError('僅實際在庫的退貨件可轉回入庫')
            latest=db.execute('SELECT ship_event_id FROM stock_returns WHERE unit_id=? ORDER BY rowid DESC LIMIT 1',(unit_id,)).fetchone()
            if latest[0]!=ship_event_id:
                raise ConflictError('退回週期已更新，請重新讀取')
            before=dict(row)
            quality = ('good' if row['quality']=='pending' else row['quality']) if restock else 'good'
            packaging = row['packaging'] if restock else 'good'
            db.execute("UPDATE stock_units SET quality=?,packaging=?,fifo_at=first_received_at WHERE id=?",(quality,packaging,unit_id))
            after=dict(db.execute('SELECT * FROM stock_units WHERE id=?',(unit_id,)).fetchone())
            at=now()
            db.execute('INSERT INTO stock_return_releases VALUES(?,?,?,?)',(ship_event_id,unit_id,at,encode(after)))
            stock_event(db,body,'return_release',unit_id,dict(before=before,after=after,reason=reason,ship_event_id=ship_event_id,fifo_policy='original-receipt',flow='return-to-stock' if restock else 'legacy-inspection'),time=at)
            return after
        return command(self.store,f'unit_return_{"restock" if restock else "release"}/{unit_id}',body,apply)

    def receive_return(self, unit_id, body):
        def apply(db):
            shipment_id = required_text(body.get('shipment_id'), '原出貨單', 100)
            reason = required_text(body.get('reason'), '實退原因', 200)
            quality=body.get('quality','good')
            if quality not in ('good','pending','defective'):
                raise ValueError('退回件品質不合法')
            shipped = db.execute("""SELECT e.* FROM stock_events e WHERE e.kind='ship' AND e.unit_id=? AND e.shipment_id=?
                AND NOT EXISTS (SELECT 1 FROM stock_ship_reversals r WHERE r.ship_event_id=e.id) ORDER BY e.rowid DESC LIMIT 1""", (unit_id, shipment_id)).fetchone()
            if shipped is None:
                raise ValueError('找不到此件在原出貨單的實際出貨紀錄')
            returned = db.execute('SELECT snapshot FROM stock_returns WHERE ship_event_id=?', (shipped['id'],)).fetchone()
            if returned:
                return json.loads(returned[0])
            unit = db.execute('SELECT * FROM stock_units WHERE id=?', (unit_id,)).fetchone()
            if unit is None or unit['location'] != 'customer':
                raise ConflictError('此件不在已出貨狀態，不能再次實收入庫')
            target=None
            if locations_enabled(db):
                from .locations import place
                target=place(db,body.get('place_id'))
                if not target['can_return']:raise ConflictError('請選擇允許退貨的櫃位')
            latest=db.execute("SELECT id FROM stock_events WHERE kind='ship' AND unit_id=? ORDER BY rowid DESC LIMIT 1",(unit_id,)).fetchone()
            if latest[0]!=shipped['id']:
                raise ConflictError('此件已有較新的出貨紀錄，請由最新出貨單辦理退貨')
            db.execute("UPDATE stock_units SET location='warehouse',quality=? WHERE id=?", (quality,unit_id))
            if target:
                from .locations import move_unit
                move_unit(db,body,unit,target,'putaway')
            after = dict(db.execute('SELECT * FROM stock_units WHERE id=?', (unit_id,)).fetchone())
            db.execute('INSERT INTO stock_returns VALUES(?,?,?)', (shipped['id'], unit_id, encode(after)))
            stock_event(db, body, 'return', unit_id, dict(before=dict(unit),after=after,ship_event_id=shipped['id'], product_id=unit['product_id'], reason=reason, quality=after['quality'], cost_cents=unit['cost_cents'],place_id=after['place_id'],can_export=bool(target['can_export']) if target else True), shipment_id=shipment_id)
            return after
        return command(self.store, f'unit_return/{unit_id}', body, apply)

