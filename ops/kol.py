"""UTM-only KOL attribution, computed from a single read snapshot."""
import csv
import io
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from .importing import timestamp
from .sales import amount, day, safe_cell
from .store import ConflictError, encode, now, required_text
from .transactions import command

SYSTEM_GROUPS = {'general':'一般訂單', 'unattributed':'待確認歸屬'}


def system_groups():
    return [dict(id=key,name=name,system_group=True,platforms=[],utms=[],started_at='',
                 version=None,campaign_name='',comment='系統自動分類',created_at='',updated_at='',keep_case=False)
            for key,name in SYSTEM_GROUPS.items()]


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS kols(
        id TEXT PRIMARY KEY, name TEXT NOT NULL, platforms TEXT NOT NULL,
        started_at TEXT NOT NULL, utms TEXT NOT NULL, keep_case INTEGER NOT NULL,
        comment TEXT NOT NULL, version INTEGER NOT NULL)''')
    columns={r['name'] for r in db.execute('PRAGMA table_info(kols)')}
    for name in ('campaign_name','created_at','updated_at'):
        if name not in columns:
            db.execute(f"ALTER TABLE kols ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")


def unpack(row):
    value = dict(row)
    for key in ('platforms', 'utms'):
        value[key] = json.loads(value[key])
    value['keep_case'] = bool(value['keep_case'])
    return value


def split_values(value, label, lower=False):
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError(f'{label}須為最多2000字元的逗號分隔文字')
    values = [v.strip() for v in value.split(',') if v.strip()]
    if lower:
        values = [v.lower() for v in values]
    return list(dict.fromkeys(values))


class Kol:
    def __init__(self, store):
        self.store = store

    def list(self):
        with self.store.connect() as db:
            return [unpack(r) for r in db.execute('SELECT * FROM kols ORDER BY started_at DESC,id')]

    def save(self, body):
        def apply(db):
            allowed = {'operation_id','id','expected_version','name','platforms','started_at','utms','keep_case','comment','campaign_name'}
            if set(body) - allowed:
                raise ValueError('未知KOL設定欄位')
            keep = body.get('keep_case', False)
            if not isinstance(keep, bool):
                raise ValueError('保留大小寫須為布林值')
            name = required_text(body.get('name'), 'KOL名稱', 200)
            started = timestamp(required_text(body.get('started_at'), '合作開始時間', 100))
            utms = split_values(body.get('utms'), 'UTM', not keep)
            if not utms:
                raise ValueError('至少填入一個UTM medium代碼')
            platforms = split_values(body.get('platforms', ''), '平台')
            comment = body.get('comment', '')
            if not isinstance(comment, str) or len(comment) > 2000:
                raise ValueError('備註最多2000字元')
            kid = required_text(body['id'], 'KOL識別', 100) if 'id' in body else None
            if kid in SYSTEM_GROUPS:
                raise ValueError('系統分類不能修改')
            if kid and (type(body.get('expected_version')) is not int or body['expected_version'] < 1):
                raise ValueError('KOL版本須為正整數')
            previous = db.execute('SELECT * FROM kols WHERE id=?',(kid,)).fetchone() if kid else None
            if kid and (previous is None or body.get('expected_version') != previous['version']):
                raise ConflictError('KOL已更新或刪除，請重新讀取')
            campaign=body.get('campaign_name',previous['campaign_name'] if previous else '')
            if not isinstance(campaign,str) or len(campaign)>200:
                raise ValueError('活動名稱最多200字元')
            for other in db.execute('SELECT id,utms FROM kols'):
                if other['id'] != kid and set(utms).intersection(json.loads(other['utms'])):
                    raise ConflictError('UTM代碼已由其他KOL使用')
            result = dict(id=kid or uuid4().hex, name=name, platforms=platforms, started_at=started,
                          utms=utms, keep_case=keep, comment=comment,
                          version=previous['version']+1 if previous else 1,
                          campaign_name=campaign.strip(),created_at=previous['created_at'] if previous else now(),updated_at=now())
            db.execute('''INSERT INTO kols(id,name,platforms,started_at,utms,keep_case,comment,version,campaign_name,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,platforms=excluded.platforms,started_at=excluded.started_at,
                utms=excluded.utms,keep_case=excluded.keep_case,comment=excluded.comment,version=excluded.version,
                campaign_name=excluded.campaign_name,updated_at=excluded.updated_at''',
                (result['id'],name,encode(platforms),started,encode(utms),int(keep),comment,result['version'],
                 result['campaign_name'],result['created_at'],result['updated_at']))
            return result
        return command(self.store,'kol_save',body,apply)

    def delete(self, body):
        def apply(db):
            if set(body)-{'operation_id','id','expected_version'}:
                raise ValueError('未知刪除欄位')
            kid=required_text(body.get('id'),'KOL識別',100)
            if kid in SYSTEM_GROUPS:
                raise ValueError('系統分類不能刪除')
            if type(body.get('expected_version')) is not int or body['expected_version'] < 1:
                raise ValueError('KOL版本須為正整數')
            previous=db.execute('SELECT * FROM kols WHERE id=?',(kid,)).fetchone()
            if previous is None or body.get('expected_version') != previous['version']:
                raise ConflictError('KOL已更新或刪除，請重新讀取')
            db.execute('DELETE FROM kols WHERE id=?',(kid,))
            return dict(id=kid,deleted=True)
        return command(self.store,'kol_delete',body,apply)


def summary(orders):
    known = [o['total_cents'] for o in orders if o['total_cents'] is not None]
    missing = len(orders)-len(known)
    total = sum(known)
    average = (Decimal(total)/100/len(orders)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP) if orders else Decimal('0.00')
    return dict(order_count=len(orders), total=amount(total) if not missing else None,
                known_total=amount(total), missing_total_count=missing,
                average=str(average) if not missing else None)


def report(store, account_id='', start='', end='', kol_id='', platform='', status='active', utm='', product_id='', include_internal=False):
    if isinstance(include_internal, str):
        if include_internal not in ('true','false'):
            raise ValueError('包含內部訂單須為 true 或 false')
        include_internal = include_internal == 'true'
    if not isinstance(include_internal, bool):
        raise ValueError('包含內部訂單須為布林值')
    if status not in ('active','cancelled','excluded','all'):
        raise ValueError('訂單狀態須為有效、取消、清洗排除或全部')
    beginning, ending = day(start), day(end, True)
    if beginning and ending and beginning >= ending:
        raise ValueError('開始日期不可晚於結束日期')
    with store.connect() as db:
        db.execute('BEGIN')
        if account_id and not db.execute('SELECT 1 FROM accounts WHERE id=?',(account_id,)).fetchone():
            raise ValueError('未知來源帳戶')
        kols = [dict(unpack(r),system_group=False) for r in db.execute('SELECT * FROM kols ORDER BY name,id')]
        destinations = [*system_groups(),*kols]
        by_id = {k['id']:k for k in destinations}
        if kol_id and kol_id not in by_id:
            raise ValueError('找不到KOL')
        tags = {tag:k for k in kols for tag in k['utms']}
        products = {r['id']:dict(r) for r in db.execute('SELECT p.*,d.sku FROM products p JOIN product_details d ON d.product_id=p.id')}
        products.update({r['id']:dict(r,kind='analysis') for r in db.execute('SELECT * FROM kol_analysis_products')})
        if product_id and product_id not in products:
            raise ValueError('未知商品')
        orders = []
        imported = [dict(json.loads(r['normalized']),account_id=r['account_id'],batch_id=r['batch_id'],platform=r['platform'])
                    for r in db.execute('SELECT o.*,a.platform FROM kol_analysis_orders o JOIN accounts a ON a.id=o.account_id')]
        imported_keys = {(o['account_id'],o['order_id']) for o in imported}
        operational = store.current_orders(db)
        duplicate_count = sum((o['account_id'],o['order_id']) in imported_keys for o in operational)
        originals = [o for o in operational if (o['account_id'],o['order_id']) not in imported_keys] + imported
        for original in originals:
            if not include_internal and original.get('internal',False):
                continue
            instant=datetime.fromisoformat(original['ordered_at'])
            if ((account_id and original['account_id'] != account_id)
                    or (beginning and instant < beginning) or (ending and instant >= ending)):
                continue
            source_utm=original.get('utm_medium','').strip()
            match=tags.get(source_utm)
            reason='沒有UTM' if not source_utm else 'UTM未對應'
            if match and instant < datetime.fromisoformat(match['started_at']):
                match=None
                reason='早於合作開始時間'
            identity=match['id'] if match else 'unattributed' if source_utm else 'general'
            if platform and (not match or platform not in match['platforms']):
                continue
            if kol_id and identity != kol_id:
                continue
            lines=[dict(line, name=line.get('name') or (line['item_name'] if line.get('line_type')=='non_stock' else products[line['product_id']]['name']),
                        sku='' if line.get('line_type')=='non_stock' else products[line['product_id']]['sku'],
                        amount=amount(line['amount_cents']) if line.get('amount_cents') is not None else None) for line in original['lines']]
            orders.append(dict(account_id=original['account_id'],order_id=original['order_id'],
                ordered_at=original['ordered_at'],revision=original['revision'],batch_id=original['batch_id'],
                total_cents=original.get('order_total_cents'),total=amount(original['order_total_cents']) if original.get('order_total_cents') is not None else None,
                status=original['status'],source_updated_at=original.get('source_updated_at',''),
                platform=original['platform'],utm_medium=source_utm,kol_id=identity,kol_name=by_id[identity]['name'],
                matched_utm=source_utm if match else '',reason='UTM精確匹配' if match else reason,
                rule_version=match['version'] if match else None,lines=lines))
        active_orders=[o for o in orders if o['status']=='active']
        groups=[]
        for k in destinations:
            if kol_id and k['id'] != kol_id:
                continue
            if platform and platform not in k['platforms']:
                continue
            selected=[o for o in active_orders if o['kol_id']==k['id']]
            groups.append(dict(k,**summary(selected)))
        product_groups={}
        for o in active_orders:
            for line in o['lines']:
                if line.get('line_type') == 'non_stock':
                    continue
                key=(o['account_id'],line['product_id'])
                p=product_groups.setdefault(key,dict(id=line['product_id'],account_id=o['account_id'],platform=o['platform'],kind=products[line['product_id']]['kind'],name=line['name'],sku=line['sku'],cents=0,quantity=0,orders=set(),quantity_only=False))
                p['cents']+=line.get('amount_cents') or 0;p['quantity']+=line['quantity']
                p['quantity_only'] = p['quantity_only'] or line.get('amount_basis')=='order_total_only'
                p['orders'].add((o['account_id'],o['order_id']))
        product_rows=[dict(id=p['id'],account_id=p['account_id'],platform=p['platform'],kind=p['kind'],name=p['name'],sku=p['sku'],amount=None if p['quantity_only'] else amount(p['cents']),amount_basis='order_total_only' if p['quantity_only'] else 'line_amount',quantity=p['quantity'],order_count=len(p['orders'])) for p in product_groups.values()]
        status_counts={s:sum(o['status']==s for o in orders) for s in ('active','cancelled','excluded')}
        status_counts['all']=len(orders)
        utm_counts={tag:sum(o['utm_medium']==tag for o in orders) for tag in sorted({o['utm_medium'] for o in orders})}
        visible=[]
        for o in orders:
            if (status!='all' and o['status']!=status) or (utm and o['utm_medium']!=utm):
                continue
            quantity=sum(l['quantity'] for l in o['lines'] if l['product_id']==product_id)
            if product_id and not quantity:
                continue
            visible.append(dict(o,product_quantity=quantity if product_id else None))
        return dict(summary=summary(active_orders),filtered_summary=summary(visible),groups=groups,orders=visible,products=product_rows,
                    status_counts=status_counts,utm_counts=utm_counts,product=products.get(product_id),
                    kol=by_id.get(kol_id),filters=dict(account_id=account_id,start=start,end=end,kol_id=kol_id,platform=platform,status=status,utm=utm,product_id=product_id,include_internal=include_internal),
                    duplicate_source_count=duplicate_count,
                    rule_version='utm-medium-exact-v2',generated_at=now(),
                    definition='無UTM歸一般訂單；有UTM尚未匹配或早於合作時間歸待確認歸屬。依目前KOL設定重算；有效訂單總金額按原始完整訂單計一次，取消及清洗排除不計業績。Excel 清洗商品僅統計數量，無商品營收分攤。',
                    settings_versions={k['id']:k['version'] for k in kols})


def export_csv(result, kind='statistics'):
    out=io.StringIO(newline='')
    metadata=['filters','rule_version','generated_at','settings_versions']
    if kind=='statistics':
        fields=['id','name','order_count','total','average','known_total','missing_total_count'];rows=result['groups']
    elif kind=='list':
        fields=['id','name','campaign_name','order_count','platforms','started_at','utms','comment','created_at','updated_at'];rows=result['groups']
    elif kind=='products':
        fields=['account_id','platform','kind','id','sku','name','quantity','order_count','amount'];rows=result['products']
    elif kind=='orders':
        fields=['account_id','platform','order_id','status','utm_medium','total','product_quantity','ordered_at','source_updated_at','batch_id'];rows=result['orders']
    else:
        raise ValueError('未知KOL匯出種類')
    fields+=metadata
    writer=csv.DictWriter(out,fieldnames=fields)
    writer.writeheader()
    for group in rows:
        values={key:group.get(key,'') for key in fields}
        values.update(filters=encode(result['filters']),rule_version=result['rule_version'],generated_at=result['generated_at'],settings_versions=encode(result['settings_versions']))
        writer.writerow({k:safe_cell('' if v is None else encode(v) if isinstance(v,list) else v) for k,v in values.items()})
    return out.getvalue()
