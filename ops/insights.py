"""Deterministic views; no model, external account, or writable query interface."""
import csv
import io
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from itertools import permutations
from uuid import uuid4
from .catalogue import Catalogue
from .materials import Materials
from .sales import sales, amount, day, TAIPEI, safe_cell
from .stock import Stock
from .store import now


def sales_insights(store, **filters):
    result = sales(store, **filters)
    groups = {name: {} for name in ('daily','channels','products','attribution')}
    for line in result['lines']:
        keys = dict(daily=datetime.fromisoformat(line['ordered_at']).astimezone(TAIPEI).date().isoformat(),
            channels=line['account_id'], products=line['product_id'], attribution=line['kol_ref'] or '')
        labels = dict(keys, products=line['product_name'], attribution=line['kol_ref'] or '未歸因')
        for name, key in keys.items():
            if name == 'products' and line.get('line_type') == 'non_stock':
                continue
            row = groups[name].setdefault(key, dict(key=key,label=labels[name],cents=0,quantity=0,orders=set(),sources=set()))
            row['cents'] += int(Decimal(line['amount'])*100)
            row['quantity'] += line['quantity']
            row['orders'].add((line['account_id'],line['order_id']))
            row['sources'].add(line['batch_id'])
    for name, rows in groups.items():
        result[name] = [dict(key=row['key'],label=row['label'],amount=amount(row['cents']),quantity=row['quantity'],
            order_count=len(row['orders']),batch_ids=sorted(row['sources'])) for _,row in sorted(rows.items())]
    result['attribution_rule'] = 'source-kol-ref-v1'
    result['attribution_note'] = '僅按來源提供的 kol_ref 分組，不推測缺值或衝突，不代表因果增量。'
    return result


def crossbuy(store, account_id='', start='', end=''):
    beginning, ending = day(start), day(end, True)
    if beginning and ending and beginning >= ending:
        raise ValueError('開始日期不可晚於結束日期')
    with store.connect() as db:
        db.execute('BEGIN')
        if account_id and not db.execute('SELECT 1 FROM accounts WHERE id=?',(account_id,)).fetchone():
            raise ValueError('未知來源帳戶')
        products={r['id']:dict(r) for r in db.execute('SELECT * FROM products')}
        buyers=defaultdict(set); histories=defaultdict(lambda:defaultdict(list)); same=defaultdict(set)
        eligible_orders=missing=bundle_only=non_stock_only=0
        for order in store.current_orders(db):
            instant=datetime.fromisoformat(order['ordered_at'])
            if order['status']!='active' or (account_id and order['account_id']!=account_id) or (beginning and instant<beginning) or (ending and instant>=ending):
                continue
            singles={line['product_id'] for line in order['lines'] if line.get('line_type')!='non_stock' and products[line['product_id']]['kind']=='product'}
            if not singles:
                if any(line.get('line_type')!='non_stock' for line in order['lines']):
                    bundle_only+=1
                else:
                    non_stock_only+=1
                continue
            eligible_orders+=1
            if not order['customer_ref']:
                missing+=1
                continue
            customer=(order['account_id'],order['customer_ref'])
            for pid in singles:
                buyers[pid].add(customer); histories[customer][pid].append(instant)
            for pair in permutations(sorted(singles),2):
                same[pair].add(customer)
        later=defaultdict(set)
        for customer, history in histories.items():
            for first,second in permutations(sorted(history),2):
                if min(history[first])<max(history[second]):
                    later[(first,second)].add(customer)
        pairs=[]
        for a,b in sorted(set(same)|set(later)):
            denominator=len(buyers[a])
            pairs.append(dict(source_product_id=a,target_product_id=b,source_name=products[a]['name'],target_name=products[b]['name'],
                eligible_buyers=denominator,same_order_buyers=len(same[(a,b)]),later_buyers=len(later[(a,b)]),
                same_order_rate=str((Decimal(len(same[(a,b)]))*100/denominator).quantize(Decimal('0.01'))),
                later_rate=str((Decimal(len(later[(a,b)]))*100/denominator).quantize(Decimal('0.01')))))
        return dict(pairs=pairs,eligible_orders=eligible_orders,missing_customer_orders=missing,excluded_bundle_only_orders=bundle_only,
            excluded_non_stock_only_orders=non_stock_only,
            known_customer_orders=eligible_orders-missing,filters=dict(account_id=account_id,start=start,end=end,timezone='UTC+08:00'),
            generated_at=now(),rule_version='natural-crossbuy-v1',
            definition='分母為有顧客識別的來源商品買家；後購限目前觀察期內嚴格較晚的訂單。同帳戶內認人，套組不拆件，缺ID不猜。')


def product_overview(store, product_id, account_id='', start='', end=''):
    p=Catalogue(store).detail(product_id)
    sales_result=sales(store,product_id=product_id,account_id=account_id,start=start,end=end)
    stock_data=Stock(store).snapshot()
    stock_time=now()
    material_service=Materials(store)
    material_data=material_service.snapshot()
    bom=next((b for b in reversed(material_data['boms']) if b['product_id']==product_id),None)
    plans=[plan for plan in material_data['plans'] if plan['product_id']==product_id and plan['state']!='cancelled']
    relevant={line['material_id'] for line in (bom['lines'] if bom else [])}
    relevant.update(line['material_id'] for plan in plans for line in plan['lines'])
    summary=material_service.summary([p['id'] for p in plans]) if plans else material_data['summary']
    selected={row['material_id']:dict(row) for row in summary['materials']} if plans else {}
    materials=[]
    for row in material_data['summary']['materials']:
        if row['material_id'] not in relevant:
            continue
        # A new BOM can contain materials absent from older plan snapshots.
        # Show their current availability without attributing other plans' demand.
        materials.append(selected.get(row['material_id'],dict(row,demand='0',selected_reserved='0',shortage='0')))
    return dict(query_id=uuid4().hex,product=p,sales=sales_result,
        stock=next((item for item in stock_data['products'] if item['id']==product_id),None),
        bom=bom,materials=materials,production_plan_ids=[p['id'] for p in plans],
        sources=dict(sales=dict(at=sales_result['generated_at'],rule=sales_result['rule_version'],batch_ids=sorted({l['batch_id'] for l in sales_result['lines']})),
            stock=dict(at=stock_time,rule='physical-units-v1'),materials=dict(at=summary['as_of'],rule=summary['rule_version'])),
        filters=dict(account_id=account_id,product_id=product_id,start=start,end=end),
        note='各模組依所列查詢時間讀取，不聲稱多次查詢為同一原子快照。')


def export_insights(result):
    out=io.StringIO(newline='')
    writer=csv.writer(out)
    writer.writerow(['group','key','label','amount','order_count','quantity','batch_ids','filters','generated_at','rule_version'])
    for group in ('daily','channels','products','attribution'):
        for row in result[group]:
            values=[group,row['key'],row['label'],row['amount'],row['order_count'],row['quantity'],json.dumps(row['batch_ids']),
                json.dumps(result['filters'],ensure_ascii=False),result['generated_at'],result['rule_version']]
            writer.writerow([safe_cell(value) for value in values])
    return out.getvalue()
