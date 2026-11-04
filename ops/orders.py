"""Manual receipt of order facts into the same immutable order store as CSV imports."""
import csv
import hashlib
import io
import json
from uuid import uuid4

from .catalogue import Catalogue, product, timestamp
from .importing import REQUIRED, parse_orders
from .stock import count, money, cancel_order_shipments
from .store import ConflictError, encode, now, required_text
from .transactions import command
from .numbering import document_number


def amount(cents):
    return f'{cents // 100}.{cents % 100:02d}'


class Orders:
    def __init__(self, store):
        self.store = store

    def page(self, page=1, q='', status='', account_id=''):
        if type(page) is not int or page < 1 or page > 1000000:
            raise ValueError('頁碼須為正整數')
        if not all(isinstance(v,str) and len(v)<=200 for v in (q,status,account_id)) or status not in ('','active','cancelled'):
            raise ValueError('訂單篩選條件不正確')
        with self.store.connect() as db:
            db.execute('BEGIN')
            if account_id and not db.execute('SELECT 1 FROM accounts WHERE id=?',(account_id,)).fetchone():
                raise ValueError('未知訂單來源')
            db.create_function('casefold',1,lambda value: str(value or '').casefold(),deterministic=True)
            where=[]; args=[]
            if status:
                where.append("json_extract(v.normalized,'$.status')=?");args.append(status)
            if account_id:
                where.append('v.account_id=?');args.append(account_id)
            if q.strip():
                where.append('''(instr(casefold(v.order_id),?)>0 OR EXISTS(
                    SELECT 1 FROM json_each(v.normalized,'$.lines') l
                    LEFT JOIN products p ON p.id=json_extract(l.value,'$.product_id')
                    LEFT JOIN product_details d ON d.product_id=p.id WHERE
                    instr(casefold(json_extract(l.value,'$.external_sku')),?)>0 OR
                    instr(casefold(json_extract(l.value,'$.item_name')),?)>0 OR
                    instr(casefold(p.name),?)>0 OR instr(casefold(d.sku),?)>0))''')
                args.extend([q.strip().casefold()]*5)
            source='FROM current_orders c JOIN order_versions v USING(account_id,order_id,revision) JOIN accounts a ON a.id=v.account_id'
            condition=' WHERE '+' AND '.join(where) if where else ''
            total=db.execute('SELECT count(*) '+source+condition,args).fetchone()[0]
            pages=max(1,(total+49)//50);page=min(page,pages)
            rows=db.execute('SELECT v.normalized,v.account_id,v.batch_id,a.platform '+source+condition+
                ' ORDER BY v.rowid LIMIT 50 OFFSET ?',[*args,(page-1)*50])
            orders=[dict(json.loads(r['normalized']),account_id=r['account_id'],platform=r['platform'],batch_id=r['batch_id']) for r in rows]
            return dict(orders=orders,total=total,page=page,pages=pages,page_size=50)

    def list(self, q='', status='', account_id=''):
        if not all(isinstance(value,str) and len(value)<=200 for value in (q,status,account_id)):
            raise ValueError('訂單查詢條件須為200字內文字')
        if status not in ('','active','cancelled'):
            raise ValueError('訂單狀態只接受有效或取消；出貨狀態請至出貨管理查詢')
        with self.store.connect() as db:
            db.execute('BEGIN')
            if account_id and not db.execute('SELECT 1 FROM accounts WHERE id=?',(account_id,)).fetchone():
                raise ValueError('未知訂單來源')
            products={r['id']:dict(r) for r in db.execute('SELECT p.id,p.name,d.sku FROM products p JOIN product_details d ON d.product_id=p.id')}
            needle=q.strip().casefold()
            results=[]
            for order in self.store.current_orders(db):
                if (status and order['status']!=status) or (account_id and order['account_id']!=account_id):
                    continue
                search=[order['order_id']]
                for line in order['lines']:
                    p=products.get(line['product_id'],{})
                    search.extend([line.get('external_sku',''),line.get('item_name',''),p.get('name',''),p.get('sku','')])
                if not needle or any(needle in text.casefold() for text in search):
                    results.append(order)
            return results

    def cancel(self, body):
        def apply(db):
            account = required_text(body.get('account_id'), '訂單來源', 200)
            order_id = required_text(body.get('order_id'), '訂單編號', 100)
            current = next((o for o in self.store.current_orders(db) if o['account_id'] == account and o['order_id'] == order_id), None)
            if current is None:
                raise ValueError('原訂單不存在')
            if current['status'] == 'cancelled':
                return dict(order_id=order_id, status='cancelled', revision=current['revision'])
            if body.get('expected_revision') != current['revision']:
                raise ConflictError('訂單已更新，請重新讀取後再取消')
            source = db.execute('SELECT normalized FROM order_versions WHERE account_id=? AND order_id=? AND revision=?',
                (account, order_id, current['revision'])).fetchone()
            order = json.loads(source[0])
            order.update(status='cancelled', revision=current['revision'] + 1, outcome='applied')
            order['fingerprint'] = hashlib.sha256(encode(order).encode()).hexdigest()
            batch_id = uuid4().hex
            # Cancellation is a local command, not an imported replacement CSV.
            db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?,?,?,?,?)', (batch_id, account, f'cancel-{order_id}',
                order['fingerprint'], '', 'confirmed', current['batch_id'], now(), encode([order]), '[]'))
            db.execute('INSERT INTO order_versions VALUES(?,?,?,?,?,?)',
                (account, order_id, order['revision'], order['fingerprint'], encode(order), batch_id))
            db.execute('UPDATE current_orders SET revision=? WHERE account_id=? AND order_id=?', (order['revision'], account, order_id))
            cancel_order_shipments(db, account, order_id, body)
            return dict(order_id=order_id, status='cancelled', revision=order['revision'])
        return command(self.store, 'order_cancel', body, apply)

    def reopen(self, body):
        def apply(db):
            account=required_text(body.get('account_id'),'來源',200)
            order_id=required_text(body.get('order_id'),'訂單',100)
            reason=required_text(body.get('reason'),'重開原因',200)
            order=next((o for o in self.store.current_orders(db) if o['account_id']==account and o['order_id']==order_id),None)
            if order is None or order['status']!='active':
                raise ConflictError('只能重開有效訂單；取消訂單不能藉重開恢復')
            before=db.execute('SELECT * FROM order_work WHERE account_id=? AND order_id=?',(account,order_id)).fetchone()
            version=before['version'] if before else 0
            if body.get('expected_version')!=version:
                raise ConflictError('訂單工作版本已變動，請重新讀取')
            db.execute("INSERT INTO order_work VALUES(?,?,'open',?,?) ON CONFLICT(account_id,order_id) DO UPDATE SET state='open',reason=excluded.reason,version=excluded.version",(account,order_id,reason,version+1))
            return dict(account_id=account,order_id=order_id,state='open',version=version+1,before=dict(before) if before else None,reason=reason)
        return command(self.store,'order_reopen',body,apply)

    def append(self, body):
        def apply(db):
            account=required_text(body.get('account_id'),'來源',200)
            order_id=required_text(body.get('order_id'),'訂單',100)
            number=required_text(body.get('number'),'追加編號',100)
            reason=required_text(body.get('reason'),'追加原因',200)
            if db.execute('SELECT 1 FROM order_additions WHERE account_id=? AND order_id=? AND number=?',(account,order_id,number)).fetchone():
                raise ConflictError('此追加編號已保存，請查看原追加紀錄，不重複提交')
            order=next((o for o in self.store.current_orders(db) if o['account_id']==account and o['order_id']==order_id),None)
            work=db.execute('SELECT * FROM order_work WHERE account_id=? AND order_id=?',(account,order_id)).fetchone()
            if order is None or order['status']!='active' or work is None or work['state']!='open':
                raise ConflictError('請先在有效訂單詳情重新開啟並記錄原因')
            if body.get('expected_version')!=work['version']:
                raise ConflictError('訂單工作版本已更新，請重新讀取')
            items=body.get('lines')
            if not isinstance(items,list) or not items:
                raise ValueError('請加入本次新增商品及數量')
            lines=[];requirements={}
            for item in items:
                if not isinstance(item,dict):
                    raise ValueError('追加商品格式錯誤')
                qty=count(item.get('quantity'))
                ref=Catalogue.resolve_product(db,item.get('product_id'),now())
                lines.append(dict(product_id=item['product_id'],quantity=qty,**{k:v for k,v in ref.items() if k!='product_id'}))
                for part in ref.get('components') or [dict(product_id=item['product_id'],quantity=1)]:
                    pid=part['product_id'];requirements[pid]=requirements.get(pid,0)+qty*part['quantity']
            addition_id=uuid4().hex
            db.execute('INSERT INTO order_additions VALUES(?,?,?,?,?,?,?,?)',(addition_id,account,order_id,number,reason,encode(lines),encode(requirements),now()))
            group=db.execute('SELECT * FROM fulfillment_groups WHERE account_id=? AND order_id=?',(account,order_id)).fetchone()
            if group:
                if group['revision']!=order['revision']:
                    raise ConflictError('原訂單版本與履約需求不一致，請先核對')
                merged=json.loads(group['requirements'])
                for pid,q in requirements.items():merged[pid]=merged.get(pid,0)+q
                db.execute('UPDATE fulfillment_groups SET requirements=? WHERE id=?',(encode(merged),group['id']))
            db.execute('UPDATE order_work SET version=version+1 WHERE account_id=? AND order_id=?',(account,order_id))
            return dict(id=addition_id,number=number,lines=lines,requirements=requirements,version=work['version']+1,revenue_changed=False)
        return command(self.store,'order_append',body,apply)

    def receive(self, body):
        def apply(db):
            account = required_text(body.get('account_id'), '訂單來源', 200)
            order_id = required_text(body.get('order_id'), '訂單編號', 100) if body.get('order_id') else document_number(db)
            if not db.execute('SELECT 1 FROM accounts WHERE id=?', (account,)).fetchone():
                raise ValueError('請先建立本機訂單來源')
            if db.execute('SELECT 1 FROM current_orders WHERE account_id=? AND order_id=?', (account, order_id)).fetchone():
                raise ConflictError('同來源訂單已存在，不能重複建立')
            ordered_at = timestamp(body.get('ordered_at'))
            for optional in ('customer_ref', 'kol_ref'):
                if not isinstance(body.get(optional, ''), str) or len(body.get(optional, '')) > 200:
                    raise ValueError('顧客識別碼與 KOL 標記須為最多200字元的文字')
            utm = body.get('utm_medium', '')
            order_total = body.get('order_total', '')
            if not isinstance(utm, str) or len(utm) > 2000:
                raise ValueError('UTM medium 須為最多2000字元的文字')
            if not isinstance(order_total, str):
                raise ValueError('訂單總金額須為金額文字或留白')
            if order_total:
                money(order_total)
            items = body.get('lines')
            if not isinstance(items, list) or not 1 <= len(items) <= 100:
                raise ValueError('訂單須有1至100行商品')
            lines, resolved = [], []
            for i, item in enumerate(items):
                if not isinstance(item, dict):
                    raise ValueError('訂單商品格式錯誤')
                quantity, price = count(item.get('quantity')), money(item.get('unit_price'))
                kind = item.get('line_type', 'stock')
                if kind == 'non_stock':
                    if item.get('product_id') or item.get('external_sku'):
                        raise ValueError('非庫存明細不可指定商品或貨號')
                    name = required_text(item.get('item_name'), '非庫存明細名稱', 200)
                    resolved.append(dict(product_id=None, product_version=None, mapping_version=None, components=[]))
                    lines.append(dict(line_id=str(i + 1), external_sku='', item_name=name, quantity=quantity,
                                      line_item_amount=amount(quantity * price), line_type='non_stock'))
                elif kind == 'stock':
                    p = product(db, item.get('product_id'))
                    resolved.append(Catalogue.resolve_product(db, p['id'], ordered_at))
                    lines.append(dict(line_id=str(i + 1), external_sku=p['sku'], item_name=p['name'], quantity=quantity, line_item_amount=amount(quantity * price)))
                else:
                    raise ValueError('明細類型須為 stock 或 non_stock')
            total = sum(money(line['line_item_amount']) for line in lines)
            output = io.StringIO(newline='')
            writer = csv.DictWriter(output, fieldnames=[*REQUIRED, 'customer_ref', 'kol_ref', 'utm_medium', 'order_total', 'line_type'])
            writer.writeheader()
            for line in lines:
                writer.writerow(dict(order_id=order_id, source_revision=1, ordered_at=ordered_at, raw_status='active', currency='TWD',
                    expected_line_count=len(lines), order_item_amount=amount(total),
                    customer_ref=body.get('customer_ref', ''), kol_ref=body.get('kol_ref', ''),
                    utm_medium=utm, order_total=order_total, **line))
            text = output.getvalue()
            raw_rows, orders = parse_orders(text)
            order = orders[0]
            if order['errors']:
                raise ValueError('；'.join(order['errors']))
            for line, reference in zip(order['lines'], resolved):
                line.update(reference)
            order['outcome'] = 'applied'
            batch_id = uuid4().hex
            db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?,?,?,?,?)', (batch_id, account, f'manual-{order_id}.csv',
                hashlib.sha256(text.encode()).hexdigest(), text, 'confirmed', None, now(), encode([order]), encode(raw_rows)))
            db.execute('INSERT INTO order_versions VALUES(?,?,?,?,?,?)', (account, order_id, 1, order['fingerprint'], encode(order), batch_id))
            db.execute('INSERT INTO current_orders VALUES(?,?,1)', (account, order_id))
            return dict(self.store._batch(db, batch_id), order_id=order_id, input_method='manual')
        return command(self.store, 'order_receive', body, apply)
