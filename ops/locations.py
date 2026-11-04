"""Location eligibility and physical placement; preserves unit identity and cost."""
import json
import sqlite3
from uuid import uuid4
from .store import ConflictError, encode, now, required_text
from .transactions import command

ENTRY_STATES={'import':('good','good'),'boxDamaged':('good','damaged'),
              'defective':('defective','good'),'destroy':('damaged','good'),
              'defectiveReturn':('vendor_exchange','good'),'other':('other','good')}

def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS storage_config(id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL);
      INSERT OR IGNORE INTO storage_config VALUES(1,1);
      CREATE TABLE IF NOT EXISTS storage_places(id TEXT PRIMARY KEY,address TEXT NOT NULL,position TEXT NOT NULL,
        name TEXT NOT NULL,can_import INTEGER NOT NULL,can_export INTEGER NOT NULL,can_return INTEGER NOT NULL,
        entry_state TEXT NOT NULL,comment TEXT NOT NULL,active INTEGER NOT NULL,version INTEGER NOT NULL);
      CREATE UNIQUE INDEX IF NOT EXISTS storage_place_name ON storage_places(lower(address),lower(position)) WHERE active=1;
      CREATE TABLE IF NOT EXISTS storage_transfers(id TEXT PRIMARY KEY,number TEXT NOT NULL UNIQUE,from_place_id TEXT NOT NULL,
        to_place_id TEXT NOT NULL,unit_ids TEXT NOT NULL,state TEXT NOT NULL,comment TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS storage_transfer_units(unit_id TEXT PRIMARY KEY,transfer_id TEXT NOT NULL);
    ''')
    if 'place_id' not in {r[1] for r in db.execute('PRAGMA table_info(stock_units)')}:
        db.execute('ALTER TABLE stock_units ADD COLUMN place_id TEXT')

def enabled(db):
    # Physical placement is mandatory, including databases with a legacy 0 flag.
    return True

def matches_place(unit, target):
    return bool(target and target['active'] and
                ENTRY_STATES.get(target['entry_state']) == (unit['quality'], unit['packaging']))

def exportable(unit, target):
    return bool(matches_place(unit, target) and target['can_export'] and unit['quality'] == 'good')

# Keep SQL selection and Python final checks based on the same state mapping.
EXPORT_STATE_SQL = ' OR '.join(
    f"(sp.entry_state='{state}' AND u.quality='{quality}' AND u.packaging='{packaging}')"
    for state, (quality, packaging) in ENTRY_STATES.items() if quality == 'good')
LOCATION_SQL = f"""AND EXISTS(SELECT 1 FROM storage_places sp
    WHERE sp.id=u.place_id AND sp.active=1 AND sp.can_export=1 AND ({EXPORT_STATE_SQL}))
    AND NOT EXISTS(SELECT 1 FROM storage_transfer_units st WHERE st.unit_id=u.id)"""

def place(db, pid):
    row=db.execute('SELECT * FROM storage_places WHERE id=? AND active=1',(pid,)).fetchone()
    if row is None: raise ValueError('找不到啟用中的櫃位')
    return dict(row)

def eligible(db, unit):
    p=db.execute('SELECT * FROM storage_places WHERE id=?',(unit['place_id'],)).fetchone()
    return bool(exportable(unit, p) and not db.execute('SELECT 1 FROM storage_transfer_units WHERE unit_id=?',(unit['id'],)).fetchone())

def move_unit(db, body, unit, target, kind):
    from .stock import stock_event
    before=dict(unit)
    quality,packaging=ENTRY_STATES[target['entry_state']]
    db.execute('UPDATE stock_units SET place_id=?,quality=?,packaging=? WHERE id=?',(target['id'],quality,packaging,unit['id']))
    after=dict(db.execute('SELECT * FROM stock_units WHERE id=?',(unit['id'],)).fetchone())
    stock_event(db,body,kind,unit['id'],dict(before=before,after=after,place_id=target['id'],
        transfer_id=body.get('transfer_id'),can_export=bool(target['can_export']),reason=body.get('reason') or '依目標櫃位入櫃規則',product_id=unit['product_id']),receipt_id=unit['receipt_id'])

class Locations:
    def __init__(self,store): self.store=store

    def snapshot(self):
        with self.store.connect() as db:
            places=[]
            for row in db.execute('SELECT * FROM storage_places WHERE active=1 ORDER BY address,position'):
                p=dict(row);p['unit_count']=db.execute("SELECT count(*) FROM stock_units WHERE place_id=? AND location IN ('warehouse','staging')",(p['id'],)).fetchone()[0];places.append(p)
            transfers=[dict(r) for r in db.execute('SELECT * FROM storage_transfers ORDER BY rowid DESC')]
            for t in transfers:t['unit_ids']=json.loads(t['unit_ids'])
            return dict(enabled=enabled(db),places=places,transfers=transfers,entry_states=list(ENTRY_STATES))

    def enable(self,body):
        def apply(db):
            if db.execute('SELECT enabled FROM storage_config WHERE id=1').fetchone()[0]:return dict(id='storage',enabled=True)
            db.execute('UPDATE storage_config SET enabled=1 WHERE id=1')
            from .stock import stock_event
            for u in db.execute("SELECT * FROM stock_units WHERE location IN ('warehouse','staging')").fetchall():
                p=db.execute('SELECT can_export FROM storage_places WHERE id=? AND active=1',(u['place_id'],)).fetchone()
                stock_event(db,body,'storage_enable',u['id'],dict(product_id=u['product_id'],place_id=u['place_id'],can_export=bool(p and p[0])))
            return dict(id='storage',enabled=True)
        return command(self.store,'storage_enable',body,apply)

    def save(self,body):
        def apply(db):
            previous=place(db,body['id']) if body.get('id') else None
            if previous and body.get('expected_version')!=previous['version']:raise ConflictError('櫃位已更新，請重新整理')
            values={k:required_text(body.get(k),label,100) for k,label in [('address','櫃位地址'),('position','櫃位位置')]}
            for k in ('name','comment'):
                v=body.get(k,'')
                if not isinstance(v,str) or len(v)>500:raise ValueError('名稱或備註過長')
                values[k]=v.strip()
            for k in ('can_import','can_export','can_return'):
                if type(body.get(k)) is not bool:raise ValueError('櫃位功能須為勾選值')
                values[k]=int(body[k])
            state=body.get('entry_state','import')
            if state not in ENTRY_STATES:raise ValueError('不支援的入櫃狀態')
            pid=previous['id'] if previous else uuid4().hex
            changed=previous and (state!=previous['entry_state'] or values['can_export']!=previous['can_export'])
            if changed:
                busy=db.execute('''SELECT 1 FROM stock_units u LEFT JOIN allocations a ON a.unit_id=u.id
                    LEFT JOIN storage_transfer_units t ON t.unit_id=u.id WHERE u.place_id=? AND (a.unit_id IS NOT NULL OR t.unit_id IS NOT NULL)''',(pid,)).fetchone()
                if busy:raise ConflictError('櫃內商品已被出貨或調撥占用，不能改入櫃規則／出貨權限')
            try:
                db.execute('INSERT INTO storage_places VALUES(?,?,?,?,?,?,?,?,?,1,?) ON CONFLICT(id) DO UPDATE SET address=excluded.address,position=excluded.position,name=excluded.name,can_import=excluded.can_import,can_export=excluded.can_export,can_return=excluded.can_return,entry_state=excluded.entry_state,comment=excluded.comment,version=excluded.version',
                    (pid,values['address'],values['position'],values['name'],values['can_import'],values['can_export'],values['can_return'],state,values['comment'],previous['version']+1 if previous else 1))
            except sqlite3.IntegrityError:raise ConflictError('相同地址與位置的櫃位已存在') from None
            target=place(db,pid)
            if changed:
                from .stock import stock_event
                for u in db.execute("SELECT * FROM stock_units WHERE place_id=? AND location='warehouse'",(pid,)).fetchall():
                    if state!=previous['entry_state']:move_unit(db,body,u,target,'place_rule')
                    else:stock_event(db,body,'place_rule',u['id'],dict(product_id=u['product_id'],before=dict(u),after=dict(u),place_id=pid,can_export=bool(target['can_export'])))
            return target
        return command(self.store,'place_save',body,apply)

    def delete(self,pid,body):
        def apply(db):
            row=db.execute('SELECT * FROM storage_places WHERE id=?',(pid,)).fetchone()
            if row is None:raise ValueError('找不到櫃位')
            if not row['active']:return dict(id=pid,active=False)
            if db.execute("SELECT 1 FROM stock_units WHERE place_id=? AND location IN ('warehouse','staging')",(pid,)).fetchone():raise ConflictError('櫃位中仍有商品，請先調撥')
            if db.execute("SELECT 1 FROM storage_transfers WHERE (from_place_id=? OR to_place_id=?) AND state IN ('draft','moving')",(pid,pid)).fetchone():raise ConflictError('櫃位仍被未完成調撥單使用')
            db.execute('UPDATE storage_places SET active=0,version=version+1 WHERE id=?',(pid,))
            return dict(id=pid,active=False)
        return command(self.store,'place_delete/'+pid,body,apply)

    def assign(self,body):
        def apply(db):
            target=place(db,body.get('place_id'))
            if not target['can_import']:raise ConflictError('此櫃位不允許進貨配櫃')
            receipt=db.execute('SELECT state,cost_status,cost_version FROM receipts WHERE id=?',(body.get('receipt_id'),)).fetchone()
            if receipt is None or receipt['state']!='confirmed':raise ConflictError('請先確認入庫')
            total=db.execute('SELECT COALESCE(SUM(cost_cents),0) FROM stock_units WHERE receipt_id=?',(body['receipt_id'],)).fetchone()[0]
            if 'expected_cost_version' in body and (type(body['expected_cost_version']) is not int or body['expected_cost_version']!=receipt['cost_version']):
                raise ConflictError('入庫費用已更新，請重新整理後再確認配櫃')
            if receipt['cost_status']=='pending' or total==0:
                if body.get('cost_acknowledged') is not True or type(body.get('expected_cost_version')) is not int or body['expected_cost_version']!=receipt['cost_version']:
                    raise ConflictError('此入庫單尚未填寫費用或費用為0元，請確認費用狀態後再配櫃')
            query="SELECT u.* FROM stock_units u WHERE u.receipt_id=? AND u.location='warehouse' AND u.place_id IS NULL"
            args=[body['receipt_id']]
            if body.get('product_id'):query+=' AND u.product_id=?';args.append(body['product_id'])
            units=db.execute(query+' ORDER BY fifo_at,id',args).fetchall()
            if 'unit_ids' in body:
                ids=body['unit_ids']
                if not isinstance(ids,list) or not ids or any(not isinstance(x,str) for x in ids) or len(ids)!=len(set(ids)):raise ValueError('請選擇不重複的商品件')
                units=[u for u in units if u['id'] in set(ids)]
                if len(units)!=len(ids):raise ConflictError('選取的商品已配櫃或不屬於此進貨單')
            quantity=body.get('quantity',len(units))
            if type(quantity) is not int or quantity<1 or quantity>len(units):raise ConflictError('未配櫃數量不足或數量不合法')
            for u in units[:quantity]:
                if db.execute('SELECT 1 FROM allocations WHERE unit_id=?',(u['id'],)).fetchone() and enabled(db):raise ConflictError('已占用商品不能重新配櫃')
                move_unit(db,body,u,target,'putaway')
            if receipt['cost_status']=='pending' or total==0:
                self.store.event(db,'putaway_cost_acknowledged',body['receipt_id'],dict(cost_version=receipt['cost_version'],cost_status=receipt['cost_status'],operation_id=body['operation_id']))
            return dict(id=body['receipt_id'],placed=quantity,place_id=target['id'])
        return command(self.store,'putaway',body,apply)

    def transfer(self,body):
        def apply(db):
            src=place(db,body.get('from_place_id'));dst=place(db,body.get('to_place_id'))
            if src['id']==dst['id']:raise ValueError('來源與目標櫃位不能相同')
            ids=body.get('unit_ids')
            if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)):raise ValueError('請選擇不重複的商品件')
            self._check_units(db,ids,src['id'])
            tid=uuid4().hex;number=required_text(body.get('number'),'調撥單號',100)
            comment=body.get('comment','')
            if not isinstance(comment,str) or len(comment)>1000:raise ValueError('備註過長')
            try:db.execute('INSERT INTO storage_transfers VALUES(?,?,?,?,?,?,?,?,?)',(tid,number,src['id'],dst['id'],encode(ids),'draft',comment,now(),now()))
            except sqlite3.IntegrityError:raise ConflictError('調撥單號已存在') from None
            return dict(id=tid,number=number,state='draft')
        return command(self.store,'transfer_create',body,apply)

    @staticmethod
    def _check_units(db,ids,src,owner=None):
        from .stock import return_hold
        for uid in ids:
            u=db.execute('SELECT * FROM stock_units WHERE id=?',(uid,)).fetchone()
            reserved=db.execute('SELECT transfer_id FROM storage_transfer_units WHERE unit_id=?',(uid,)).fetchone()
            if not u or u['location']!='warehouse' or u['place_id']!=src or return_hold(db,uid) or db.execute('SELECT 1 FROM allocations WHERE unit_id=?',(uid,)).fetchone() or (reserved and reserved[0]!=owner):raise ConflictError('商品位置／占用已變動，不能調撥')

    def transition(self,tid,action,body):
        def apply(db):
            from .stock import stock_event
            row=db.execute('SELECT * FROM storage_transfers WHERE id=?',(tid,)).fetchone()
            if not row:raise ValueError('找不到調撥單')
            t=dict(row);ids=json.loads(t['unit_ids']);target_state={'start':'moving','complete':'complete','cancel':'cancelled'}.get(action)
            if not target_state:raise ValueError('未知調撥動作')
            if t['state']==target_state:return dict(id=tid,state=target_state)
            if t['state'] in ('complete','cancelled') or (action=='start' and t['state']!='draft') or (action=='complete' and t['state']!='moving'):raise ConflictError('調撥單狀態已變動')
            if action!='cancel':self._check_units(db,ids,t['from_place_id'],tid)
            dst=place(db,t['to_place_id']) if action=='complete' else None
            for uid in ids:
                u=db.execute('SELECT * FROM stock_units WHERE id=?',(uid,)).fetchone()
                if action=='start':db.execute('INSERT INTO storage_transfer_units VALUES(?,?)',(uid,tid))
                elif action=='complete':move_unit(db,{**body,'transfer_id':tid},u,dst,'transfer_complete')
                if action!='start':db.execute('DELETE FROM storage_transfer_units WHERE unit_id=? AND transfer_id=?',(uid,tid))
                if action!='cancel' or t['state']=='moving':
                    stock_event(db,body,'transfer_lock' if action=='start' else 'transfer_unlock',uid,dict(product_id=u['product_id'],transfer_id=tid,reason=action))
            db.execute('UPDATE storage_transfers SET state=?,updated_at=? WHERE id=?',(target_state,now(),tid))
            return dict(id=tid,state=target_state)
        return command(self.store,'transfer_'+action+'/'+tid,body,apply)
