"""Material plans and physical movements, backed by the shared local database."""
import json
import re
from decimal import Decimal
from uuid import uuid4
from .catalogue import product
from .stock import count
from .store import ConflictError, encode, now, required_text
from .transactions import command


def quantity(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,9}(\.\d{1,3})?', value):
        raise ValueError('物料數量須為正數字串，最多9位整數及3位小數；不自動換算單位')
    result = int(Decimal(value)*1000)
    if result <= 0:
        raise ValueError('物料數量須大於零')
    return result


def display(value):
    return format(Decimal(value)/1000, 'f').rstrip('0').rstrip('.') if value % 1000 else str(value//1000)


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS suppliers(id TEXT PRIMARY KEY,name TEXT NOT NULL,active INTEGER NOT NULL,version INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS materials(id TEXT PRIMARY KEY,code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,unit TEXT NOT NULL,
        supplier_id TEXT NOT NULL REFERENCES suppliers(id),active INTEGER NOT NULL,version INTEGER NOT NULL,on_hand INTEGER NOT NULL CHECK(on_hand>=0));
      CREATE TABLE IF NOT EXISTS boms(product_id TEXT NOT NULL REFERENCES products(id),version INTEGER NOT NULL,lines TEXT NOT NULL,
        created_at TEXT NOT NULL,PRIMARY KEY(product_id,version));
      CREATE TABLE IF NOT EXISTS production_plans(id TEXT PRIMARY KEY,number TEXT NOT NULL UNIQUE,product_id TEXT NOT NULL REFERENCES products(id),
        bom_version INTEGER NOT NULL,quantity INTEGER NOT NULL,state TEXT NOT NULL,version INTEGER NOT NULL,lines TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS plan_versions(plan_id TEXT NOT NULL REFERENCES production_plans(id),version INTEGER NOT NULL,
        snapshot TEXT NOT NULL,time TEXT NOT NULL,PRIMARY KEY(plan_id,version));
      CREATE TABLE IF NOT EXISTS material_purchases(id TEXT PRIMARY KEY,number TEXT NOT NULL UNIQUE,supplier_id TEXT NOT NULL REFERENCES suppliers(id),state TEXT NOT NULL,lines TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS material_movements(id TEXT PRIMARY KEY,number TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,
        plan_id TEXT REFERENCES production_plans(id),purchase_id TEXT REFERENCES material_purchases(id),
        source_id TEXT REFERENCES material_movements(id),lines TEXT NOT NULL,time TEXT NOT NULL);
    ''')
    from .production import initialize as initialize_production
    initialize_production(db)


def record(db, table, identifier):
    row = db.execute(f'SELECT * FROM {table} WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise ValueError('指定的物料、供應商或單據不存在')
    result = dict(row)
    if 'lines' in result:
        result['lines'] = json.loads(result['lines'])
    if table == 'production_plans':
        from .production import enrich
        enrich(db,result)
    elif table == 'material_purchases':
        link = db.execute('''SELECT p.plan_id,d.factory_name FROM production_purchases p
            JOIN production_details d ON d.plan_id=p.plan_id WHERE p.purchase_id=?''',(identifier,)).fetchone()
        if link:
            result.update(dict(link))
    return result


def outward(data):
    result = dict(data)
    if 'on_hand' in result:
        result['on_hand'] = display(result['on_hand'])
    if 'lines' in result:
        result['lines'] = [{k: display(v) if k in ('quantity','required','reserved','issued','returned','ordered','received','per_unit','before','after') else v
            for k,v in line.items()} for line in result['lines']]
    return result


def reserved(db):
    totals = {}
    for row in db.execute('SELECT lines FROM production_plans'):
        for line in json.loads(row[0]):
            mid = line['material_id']
            totals[mid] = totals.get(mid, 0)+line['reserved']
    return totals


def save_plan(db, plan):
    db.execute('UPDATE production_plans SET quantity=?,state=?,version=?,lines=? WHERE id=?',
        (plan['quantity'],plan['state'],plan['version'],encode(plan['lines']),plan['id']))
    db.execute('INSERT INTO plan_versions VALUES(?,?,?,?)',(plan['id'],plan['version'],encode(plan),now()))


class Materials:
    def __init__(self, store):
        self.store = store

    def supplier(self, body):
        def apply(db):
            name = required_text(body.get('name'), '供應商名稱', 200)
            if body.get('id'):
                old = record(db,'suppliers',body['id'])
                if body.get('expected_version') != old['version']:
                    raise ConflictError('供應商已更新，請重新讀取')
                active = body.get('active',bool(old['active']))
                if type(active) is not bool:
                    raise ValueError('啟用須為布林值')
                db.execute('UPDATE suppliers SET name=?,active=?,version=version+1 WHERE id=?',(name,active,old['id']))
                return record(db,'suppliers',old['id'])
            identifier = uuid4().hex
            db.execute('INSERT INTO suppliers VALUES(?,?,1,1)',(identifier,name))
            return record(db,'suppliers',identifier)
        return command(self.store,'supplier_save',body,apply)

    def material(self, body):
        def apply(db):
            fields = {k:required_text(body.get(k),label,maximum) for k,label,maximum in
                [('code','物料代碼',100),('name','物料名稱',200),('unit','基礎單位',30),('supplier_id','供應商',100)]}
            if not record(db,'suppliers',fields['supplier_id'])['active']:
                raise ValueError('供應商已停用')
            existing = db.execute('SELECT id FROM materials WHERE code=?',(fields['code'],)).fetchone()
            if existing and existing[0] != body.get('id'):
                raise ConflictError('物料代碼已存在')
            if body.get('id'):
                old = record(db,'materials',body['id'])
                if body.get('expected_version') != old['version']:
                    raise ConflictError('物料已更新，請重新讀取')
                if fields['unit'] != old['unit']:
                    raise ValueError('基礎單位不能就地更換，請另建物料以保留歷史用量')
                active = body.get('active',bool(old['active']))
                if type(active) is not bool:
                    raise ValueError('啟用須為布林值')
                db.execute('UPDATE materials SET code=?,name=?,supplier_id=?,active=?,version=version+1 WHERE id=?',
                    (fields['code'],fields['name'],fields['supplier_id'],active,old['id']))
                return outward(record(db,'materials',old['id']))
            identifier = uuid4().hex
            db.execute('INSERT INTO materials VALUES(?,?,?,?,?,1,1,0)',(identifier,fields['code'],fields['name'],fields['unit'],fields['supplier_id']))
            return outward(record(db,'materials',identifier))
        return command(self.store,'material_save',body,apply)

    def _lines(self, db, items, unit_check=False):
        if not isinstance(items,list) or not items:
            raise ValueError('請加入至少一項物料')
        lines, seen = [], set()
        for item in items:
            if not isinstance(item,dict):
                raise ValueError('物料明細格式不正確')
            material = record(db,'materials',item.get('material_id'))
            if material['id'] in seen:
                raise ValueError('同一物料不可重複列入，請合併數量')
            if unit_check and (not material['active'] or item.get('unit') != material['unit']):
                raise ValueError('物料須啟用且 BOM 單位須與基礎單位一致，不作隱含換算')
            seen.add(material['id'])
            lines.append(dict(material_id=material['id'],quantity=quantity(item.get('quantity')),unit=material['unit']))
        return lines

    def bom(self, body):
        def apply(db):
            try:
                p = product(db,body.get('product_id'))
            except KeyError:
                raise ValueError('商品尚未建立') from None
            if not p['active'] or p['kind'] != 'product':
                raise ValueError('生產 BOM 須選啟用單品；銷售套組組成另在商品庫維護')
            old = db.execute('SELECT MAX(version) FROM boms WHERE product_id=?',(p['id'],)).fetchone()[0] or 0
            if body.get('expected_version') != old:
                raise ConflictError('BOM 已更新，請讀取最新版本')
            lines = self._lines(db,body.get('lines'),True)
            result = dict(product_id=p['id'],version=old+1,lines=lines,created_at=now())
            db.execute('INSERT INTO boms VALUES(?,?,?,?)',(p['id'],old+1,encode(lines),result['created_at']))
            return outward(result)
        return command(self.store,'bom_save',body,apply)

    def plan(self, body):
        def apply(db):
            try:
                p = product(db,body.get('product_id'))
            except KeyError:
                raise ValueError('商品尚未建立') from None
            if not p['active']:
                raise ValueError('商品已停用')
            if 'factory_id' in body:
                from .production import create
                identifier = create(db,body,p)
                plan = record(db,'production_plans',identifier)
                db.execute('INSERT INTO plan_versions VALUES(?,?,?,?)',(identifier,1,encode(plan),now()))
                return outward(plan)
            bom = db.execute('SELECT * FROM boms WHERE product_id=? ORDER BY version DESC LIMIT 1',(p['id'],)).fetchone()
            if bom is None:
                raise ValueError('此商品尚無 BOM，請先建立物料用量')
            number, qty = required_text(body.get('number'),'計畫單號',100),count(body.get('quantity'))
            if db.execute('SELECT 1 FROM production_plans WHERE number=?',(number,)).fetchone():
                raise ConflictError('計畫單號已存在')
            lines = []
            for line in json.loads(bom['lines']):
                if not record(db,'materials',line['material_id'])['active']:
                    raise ValueError('BOM 含停用物料，請先維護')
                lines.append(dict(material_id=line['material_id'],unit=line['unit'],per_unit=line['quantity'],required=line['quantity']*qty,reserved=0,issued=0,returned=0))
            plan = dict(id=uuid4().hex,number=number,product_id=p['id'],bom_version=bom['version'],quantity=qty,state='draft',version=1,lines=lines)
            db.execute('INSERT INTO production_plans VALUES(?,?,?,?,?,?,?,?)',(plan['id'],number,p['id'],bom['version'],qty,'draft',1,encode(lines)))
            db.execute('INSERT INTO plan_versions VALUES(?,?,?,?)',(plan['id'],1,encode(plan),now()))
            return outward(plan)
        return command(self.store,'production_plan_create',body,apply)

    def plan_action(self, identifier, action, body):
        def apply(db):
            plan = record(db,'production_plans',identifier)
            if plan.get('mode') == 'reported':
                from .production import action as production_action, enrich
                production_action(db,plan,action,body)
                enrich(db,plan)
                save_plan(db,plan)
                return outward(record(db,'production_plans',identifier))
            if action not in ('confirm','cancel','revise'):
                raise ValueError('未知生產計畫操作')
            if action == 'cancel':
                if plan['state'] == 'cancelled':
                    return outward(plan)
                for line in plan['lines']:
                    line['reserved'] = 0
                plan['state'] = 'cancelled'
            else:
                if plan['state'] == 'cancelled':
                    raise ConflictError('已取消計畫不能重新預留或改量')
                if action == 'confirm' and plan['state'] == 'confirmed':
                    return outward(plan)
                if action == 'revise':
                    if body.get('expected_version') != plan['version']:
                        raise ConflictError('計畫已更新，請重新讀取')
                    plan['quantity'] = count(body.get('quantity'))
                totals = reserved(db)
                for line in plan['lines']:
                    material = record(db,'materials',line['material_id'])
                    if not material['active']:
                        raise ValueError('計畫含停用物料')
                    needed = line['per_unit']*plan['quantity']
                    if needed < line['issued']:
                        raise ConflictError('修訂需求不可低於已實際發料量，退料不抹去發料歷史')
                    target = needed-line['issued'] if action == 'confirm' or plan['state'] == 'confirmed' else 0
                    if target > material['on_hand']-totals.get(material['id'],0)+line['reserved']:
                        raise ConflictError(f'物料 {material["code"]} 不足，整張計畫未新增或變更預留')
                    line.update(required=needed,reserved=target)
                if action == 'confirm':
                    plan['state'] = 'confirmed'
            plan['version'] += 1
            save_plan(db,plan)
            return outward(plan)
        return command(self.store,f'production_{action}/{identifier}',body,apply)

    def purchase(self, body):
        def apply(db):
            supplier = record(db,'suppliers',body.get('supplier_id'))
            if not supplier['active']:
                raise ValueError('供應商已停用')
            number = required_text(body.get('number'),'採購單號',100)
            if db.execute('SELECT 1 FROM material_purchases WHERE number=?',(number,)).fetchone():
                raise ConflictError('採購單號已存在')
            lines = self._lines(db,body.get('lines'))
            for line in lines:
                material = record(db,'materials',line['material_id'])
                if not material['active'] or material['supplier_id'] != supplier['id']:
                    raise ValueError('採購物料須啟用且屬於所選供應商，請按供應商分單')
                line.update(ordered=line.pop('quantity'),received=0)
            identifier = uuid4().hex
            db.execute('INSERT INTO material_purchases VALUES(?,?,?,?,?)',(identifier,number,supplier['id'],'draft',encode(lines)))
            if body.get('plan_id'):
                plan = record(db,'production_plans',body['plan_id'])
                if plan.get('mode') != 'reported' or plan['state'] not in ('draft','ordered','partial'):
                    raise ConflictError('請選擇尚未結案的工廠生產單')
                db.execute('INSERT INTO production_purchases VALUES(?,?)',(identifier,plan['id']))
            return outward(record(db,'material_purchases',identifier))
        return command(self.store,'material_purchase_create',body,apply)

    def purchase_action(self, identifier, action, body):
        def apply(db):
            purchase = record(db,'material_purchases',identifier)
            if action == 'confirm':
                if purchase['state'] in ('ordered','partial','received'):
                    return outward(purchase)
                if purchase['state'] != 'draft':
                    raise ConflictError('已取消採購不能重新叫貨')
                state = 'ordered'
            elif action == 'cancel':
                if purchase['state'] == 'received':
                    raise ConflictError('已全部收貨，沒有未收部分可取消')
                state = 'cancelled'
            else:
                raise ValueError('未知採購操作')
            db.execute('UPDATE material_purchases SET state=? WHERE id=?',(state,identifier))
            return outward(record(db,'material_purchases',identifier))
        return command(self.store,f'material_purchase_{action}/{identifier}',body,apply)

    def movement(self, body):
        def apply(db):
            kind, number = body.get('kind'),required_text(body.get('number'),'實物異動單號',100)
            if db.execute('SELECT 1 FROM material_movements WHERE number=?',(number,)).fetchone():
                raise ConflictError('異動單號已存在，請查看原紀錄；未重複加扣物料')
            lines = self._lines(db,body.get('lines'))
            plan, purchase, source = None,None,None
            if kind == 'receipt':
                purchase = record(db,'material_purchases',body.get('purchase_id'))
                if purchase['state'] not in ('ordered','partial'):
                    raise ConflictError('請先確認採購叫貨；已完成或取消的採購不能收貨')
                targets = {x['material_id']:x for x in purchase['lines']}
            elif kind == 'consume':
                plan = record(db,'production_plans',body.get('plan_id'))
                if plan.get('mode') != 'reported' or plan['state'] not in ('ordered','partial','received'):
                    raise ConflictError('請選擇已下單的工廠生產單登記實際用料')
                targets = {x['material_id']:x for x in lines}
            elif kind == 'issue':
                plan = record(db,'production_plans',body.get('plan_id'))
                if plan['state'] != 'confirmed':
                    raise ConflictError('計畫須先確認並預留，取消後不可繼續發料')
                targets = {x['material_id']:x for x in plan['lines']}
            elif kind == 'return':
                source = record(db,'material_movements',body.get('source_id'))
                if source['kind'] != 'issue':
                    raise ValueError('實退料須引用原實際發料紀錄')
                plan = record(db,'production_plans',source['plan_id'])
                targets = {x['material_id']:dict(x,returned=0) for x in source['lines']}
                for previous in db.execute("SELECT lines FROM material_movements WHERE source_id=? AND kind='return'",(source['id'],)):
                    for item in json.loads(previous[0]):
                        targets[item['material_id']]['returned'] += item['quantity']
            else:
                raise ValueError('異動類型須為收貨、發料或實退料')
            for line in lines:
                mid, qty = line['material_id'],line['quantity']
                target = targets.get(mid)
                if target is None:
                    raise ValueError('物料不在來源單據內')
                material = record(db,'materials',mid)
                line['before'] = material['on_hand']
                if kind == 'receipt':
                    if target['received']+qty > target['ordered']:
                        raise ConflictError('本次收貨超過採購剩餘未收數量')
                    target['received'] += qty
                    delta = qty
                elif kind == 'consume':
                    if qty > material['on_hand']-reserved(db).get(mid,0):
                        raise ConflictError('實際用料超過可用物料，不能使用其他計畫已預留數量')
                    delta = -qty
                elif kind == 'issue':
                    if qty > target['reserved'] or qty > material['on_hand']:
                        raise ConflictError('本次發料超過已預留或實際在庫量')
                    target['reserved'] -= qty
                    target['issued'] += qty
                    delta = -qty
                else:
                    if target['returned']+qty > target['quantity']:
                        raise ConflictError('實退量超過原發料尚未退回量')
                    next(x for x in plan['lines'] if x['material_id']==mid)['returned'] += qty
                    delta = qty
                line['after'] = line['before']+delta
                db.execute('UPDATE materials SET on_hand=on_hand+? WHERE id=?',(delta,mid))
            if purchase:
                state = 'received' if all(x['received']==x['ordered'] for x in purchase['lines']) else 'partial'
                db.execute('UPDATE material_purchases SET state=?,lines=? WHERE id=?',(state,encode(purchase['lines']),purchase['id']))
            if plan:
                plan['version'] += 1
                save_plan(db,plan)
            identifier = uuid4().hex
            db.execute('INSERT INTO material_movements VALUES(?,?,?,?,?,?,?,?)',(identifier,number,kind,
                plan['id'] if plan else None,purchase['id'] if purchase else None,source['id'] if source else None,encode(lines),now()))
            return outward(record(db,'material_movements',identifier))
        return command(self.store,'material_movement',body,apply)

    def summary(self, plan_ids=None, db=None):
        if db is None:
            with self.store.connect() as connection:
                connection.execute('BEGIN')
                return self.summary(plan_ids,connection)
        plans = [record(db,'production_plans',r[0]) for r in db.execute('SELECT id FROM production_plans')]
        if plan_ids and not set(plan_ids).issubset({p['id'] for p in plans}):
            raise ValueError('篩選包含不存在的計畫')
        selected = [p for p in plans if p['state']!='cancelled' and (not plan_ids or p['id'] in plan_ids)]
        totals, rows = reserved(db),[]
        for row in db.execute('SELECT * FROM materials ORDER BY rowid'):
            material = dict(row); mid = material['id']
            lines = [line for p in selected for line in p['lines'] if line['material_id']==mid]
            if plan_ids and not lines:
                continue
            demand = sum(line['required']-line['issued'] for line in lines)
            owned = sum(line['reserved'] for line in lines)
            free = material['on_hand']-totals.get(mid,0)
            incoming = sum(line['ordered']-line['received'] for r in db.execute("SELECT lines FROM material_purchases WHERE state IN ('ordered','partial')")
                for line in json.loads(r[0]) if line['material_id']==mid)
            rows.append(dict(material_id=mid,code=material['code'],name=material['name'],unit=material['unit'],
                on_hand=display(material['on_hand']),reserved=display(totals.get(mid,0)),available=display(free),
                demand=display(demand),selected_reserved=display(owned),shortage=display(max(0,demand-owned-free)),incoming=display(incoming)))
        return dict(materials=rows,plan_ids=[p['id'] for p in selected],rule_version='materials-v1',as_of=now())

    def snapshot(self):
        with self.store.connect() as db:
            db.execute('BEGIN')
            result = {key:[outward(record(db,table,row[0])) for row in db.execute(f'SELECT id FROM {table} ORDER BY rowid DESC')]
                for key,table in [('suppliers','suppliers'),('materials','materials'),('plans','production_plans'),('purchases','material_purchases'),('movements','material_movements')]}
            result['boms'] = [outward(dict(row,lines=json.loads(row['lines']))) for row in db.execute('SELECT * FROM boms ORDER BY product_id,version')]
            result['plan_versions'] = [dict(row,snapshot=outward(json.loads(row['snapshot']))) for row in db.execute('SELECT * FROM plan_versions ORDER BY rowid')]
            result['summary'] = self.summary(db=db)
            return result
