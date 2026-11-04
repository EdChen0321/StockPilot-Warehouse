"""Single-workbook adapter into operational orders-v1 and its confirmation transaction."""
import base64
import binascii
import copy
import csv
import hashlib
import io
import json
from uuid import uuid4

from .importing import FIELDS, parse_orders, digest
from .kol_import import read_xlsx, text, number, instant
from .store import ConflictError, encode, now, required_text
from .catalogue import Catalogue

VERSION = 'operational-xlsx-v1'
REQUIRED = {'訂單號碼','訂單日期','訂單狀態','貨幣','訂單合計','商品貨號','商品名稱','商品結帳價','數量'}
STATUSES = {'已取消':'cancelled','已確認':'active','已完成':'active','處理中':'active','待處理':'active'}


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS operational_excel_files(
        batch_id TEXT PRIMARY KEY REFERENCES batches(id),source_blob BLOB NOT NULL,
        adapter_version TEXT NOT NULL,metadata TEXT NOT NULL)''')


def money(cents):
    return f'{cents//100}.{cents%100:02d}'


def content(order):
    """Source values only; product bindings and local revisions are not source evidence."""
    keys=('order_id','ordered_at','status','currency','amount_cents','source_updated_at','customer_ref','kol_ref','utm_medium','order_total_cents')
    result={k:order[k] for k in keys if k in order}
    result['lines']=[{k:l[k] for k in ('line_id','external_sku','item_name','quantity','amount_cents','line_type') if k in l} for l in order['lines']]
    return result


def parse_workbook(blob, filename):
    raw=read_xlsx(blob,filename)
    missing=REQUIRED-set(raw[0]['data'])
    if missing:raise ValueError('Excel 缺少必要欄位：'+'、'.join(sorted(missing)))
    groups, products={},{}
    for r in raw:
        oid=required_text(r['data'].get('訂單號碼'),'訂單號碼',100)
        groups.setdefault(oid,[]).append(r)
    normalized=[];indices=[]
    for oid,rows in groups.items():
        def unique(field,required=True):
            values={text(r['data'].get(field)) for r in rows if text(r['data'].get(field))}
            if len(values)>1:raise ValueError(f'訂單 {oid} 的{field}不一致')
            if not values and required:raise ValueError(f'訂單 {oid} 缺少{field}')
            return next(iter(values),'')
        status=unique('訂單狀態')
        if status not in STATUSES:raise ValueError(f'訂單 {oid} 不支援此訂單狀態：{status}')
        if unique('貨幣')!='TWD':raise ValueError(f'訂單 {oid} 目前只支援 TWD，不自動換匯')
        head=dict(order_id=oid,source_revision='1',ordered_at=instant(unique('訂單日期')),
            raw_status=STATUSES[status],currency='TWD',expected_line_count=str(len(rows)),
            customer_ref=unique('顧客 ID',False),utm_medium=unique('UTM 媒介',False),
            order_total=money(number(unique('訂單合計'),'訂單合計')))
        lines=[]
        for ordinal,r in enumerate(rows,1):
            d=r['data'];prefix=f"第 {r['row_index']} 列"
            sku=required_text(d.get('商品貨號'),prefix+'商品貨號（SKU）',200)
            name=required_text(d.get('商品名稱'),prefix+'商品名稱',200)
            category=text(d.get('商品類型'))
            if len(category)>200:raise ValueError(prefix+'商品類型最多200字元')
            p=dict(sku=sku,name=name,group_name=category)
            if sku in products and products[sku]!=p:raise ValueError(prefix+f'同一 SKU {sku} 的名稱／類型衝突')
            products[sku]=p
            qty=number(d.get('數量'),prefix+'數量',True)
            price=number(d.get('商品結帳價'),prefix+'商品結帳價')
            lines.append(dict(line_id=str(ordinal),external_sku=sku,item_name=name,quantity=str(qty),line_item_amount=money(qty*price)))
            indices.append(r['row_index'])
        head['order_item_amount']=money(sum(number(l['line_item_amount'],'明細金額') for l in lines))
        normalized.extend(dict(head,**l) for l in lines)
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=FIELDS)
    writer.writeheader();writer.writerows(normalized)
    _,orders=parse_orders(stream.getvalue())
    original_indices={i+2:index for i,index in enumerate(indices)}
    for order in orders:
        if order['errors']:raise ValueError(f"訂單 {order['order_id']}："+'；'.join(order['errors']))
        order['row_indices']=[original_indices[i] for i in order['row_indices']]
        for l in order['lines']:l['row_index']=original_indices[l['row_index']]
    return raw,orders,products


def order_baseline(db,account,orders):
    return {o['order_id']:next((r[0] for r in db.execute(
        'SELECT v.fingerprint FROM current_orders c JOIN order_versions v USING(account_id,order_id,revision) WHERE account_id=? AND order_id=?',
        (account,o['order_id']))),None) for o in orders}


def product_baseline(db,products):
    return {sku:next((list(r) for r in db.execute('SELECT product_id,version,active FROM product_details WHERE sku=?',(sku,))),None) for sku in products}


def preview(store,account,filename,encoded,complete_snapshot,parent_id=None):
    filename=required_text(filename,'檔名',255)
    if complete_snapshot is not True:raise ValueError('必須聲明每張訂單為完整快照')
    if not isinstance(encoded,str) or len(encoded)>2_666_668:raise ValueError('Excel 須為2MB以內')
    try:blob=base64.b64decode(encoded,validate=True)
    except (binascii.Error,ValueError):raise ValueError('Excel base64 編碼不正確') from None
    raw,orders,products=parse_workbook(blob,filename)
    file_hash=hashlib.sha256(blob).hexdigest()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        account=store.import_account(db,account)
        if parent_id is None:
            existing=db.execute('SELECT b.id FROM batches b JOIN operational_excel_files f ON f.batch_id=b.id WHERE b.account_id=? AND b.file_hash=? AND f.adapter_version=? ORDER BY b.rowid LIMIT 1',(account,file_hash,VERSION)).fetchone()
            if existing:return store._batch(db,existing[0])
        baseline=order_baseline(db,account,orders)
        product_state=product_baseline(db,products)
        new_products=[p for sku,p in products.items() if product_state[sku] is None]
        for order in orders:
            current=db.execute('SELECT normalized FROM order_versions v JOIN current_orders c USING(account_id,order_id,revision) WHERE account_id=? AND order_id=?',(account,order['order_id'])).fetchone()
            if current:
                old=json.loads(current[0])
                order['revision']=old['revision']+(content(old)!=content(order))
            canonical={k:v for k,v in order.items() if k not in ('row_indices','warnings','errors','lines','fingerprint')}
            canonical['lines']=sorted([{k:v for k,v in l.items() if k!='row_index'} for l in order['lines']],key=lambda l:l['line_id'])
            order['fingerprint']=digest(canonical)
        metadata=dict(orders=orders,products=products,new_products=new_products,baseline=baseline,product_baseline=product_state)
        results=[store._evaluate(db,account,o,new_products=products) for o in orders]
        bid=uuid4().hex
        db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?,?,?,?,?)',(bid,account,filename,file_hash,'','draft',parent_id,now(),encode(results),encode(raw)))
        db.execute('INSERT INTO operational_excel_files VALUES(?,?,?,?)',(bid,blob,VERSION,encode(metadata)))
        store.event(db,'preview',bid,dict(format_version=VERSION,new_product_count=len(new_products),complete_snapshot=True))
        return store._batch(db,bid)


def prepare_confirmation(store,db,batch,attachment):
    if attachment['adapter_version']!=VERSION:raise ConflictError('匯入規則已更新，請重新預檢')
    metadata=json.loads(attachment['metadata'])
    orders=metadata['orders'];products=metadata['products'];account=batch['account_id']
    if order_baseline(db,account,orders)!=metadata['baseline']:
        raise ConflictError('訂單在預覽後已更新，請重新預檢')
    if product_baseline(db,products)!=metadata['product_baseline']:
        raise ConflictError('商品主檔在預覽後已變動，請重新預檢')
    checks=[store._evaluate(db,account,o,new_products=products) for o in orders]
    if any(r['errors'] for r in checks):raise ValueError('Excel 預覽仍有錯誤，請修正後重新預檢')
    needed={l['external_sku'] for r in checks if r['outcome']=='ready' for l in r['lines']}
    catalogue=Catalogue(store)
    for candidate in metadata['new_products']:
        if candidate['sku'] not in needed:continue
        p=catalogue._validate(db,dict(candidate,id=uuid4().hex,kind='product',active=True,attributes={},version=1))
        db.execute('INSERT INTO products VALUES(?,?,?)',(p['id'],p['name'],p['kind']))
        catalogue._save(db,p,'訂單 Excel 確認建檔')
        store.event(db,'product_create',p['id'],dict(batch_id=batch['id'],sku=p['sku']))
    return copy.deepcopy(orders)
