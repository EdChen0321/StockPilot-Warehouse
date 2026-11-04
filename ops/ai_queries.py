"""Small, deterministic ERP tools; the model never receives a database handle."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from .store import Store, now
from .sales import day, amount
from .shipping_statistics import shipping_statistics
from .sales import TAIPEI


class ReadOnlyStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        try:
            yield db
        finally:
            db.close()

    current_orders = Store.current_orders


def query_orders(path, arguments):
    allowed = {'start', 'end', 'status', 'account_id', 'view', 'offset', 'limit'}
    if not isinstance(arguments, dict) or set(arguments) - allowed:
        raise ValueError('只接受日期、狀態、來源與查詢種類，不接受 SQL 或命令')
    if any(not isinstance(v, str) or len(v) > 200 for k,v in arguments.items() if k not in ('offset','limit')):
        raise ValueError('查詢參數須為 200 字內文字')
    offset, limit = arguments.get('offset', 0), arguments.get('limit', 50)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('分頁須為非負整數位移，每頁 1–50 筆')
    start, end = arguments.get('start', ''), arguments.get('end', '')
    beginning, ending = day(start), day(end, True)
    if beginning and ending and beginning >= ending:
        raise ValueError('開始日期不可晚於結束日期')
    status, view = arguments.get('status', 'active'), arguments.get('view', 'summary')
    if status not in ('active', 'cancelled', 'all') or view not in ('summary', 'utm', 'unshipped'):
        raise ValueError('不支援的訂單狀態或查詢種類')
    store = ReadOnlyStore(path)
    with store.connect() as db:
        db.execute('BEGIN')
        account = arguments.get('account_id', '')
        if account and not db.execute('SELECT 1 FROM accounts WHERE id=?', (account,)).fetchone():
            raise ValueError('未知訂單來源')
        orders = [o for o in store.current_orders(db)
                  if (status == 'all' or o['status'] == status)
                  and (not account or o['account_id'] == account)
                  and (not beginning or datetime.fromisoformat(o['ordered_at']) >= beginning)
                  and (not ending or datetime.fromisoformat(o['ordered_at']) < ending)]
        def totals(rows):
            known = [o['order_total_cents'] for o in rows if o.get('order_total_cents') is not None]
            return dict(order_count=len(rows), order_amount=amount(sum(known)) if len(known) == len(rows) else None,
                        known_amount=amount(sum(known)), missing_amount_count=len(rows)-len(known))
        result = dict(query_id=uuid4().hex, generated_at=now(), scope='目前工作庫的營運訂單現行版本；不含獨立 KOL 匯入批次',
                      filters=dict(start=start, end=end, status=status, account_id=account, view=view),
                      rules='台北下單日期、含結束日；每單總額只計一次；預設排除取消；缺金額不當零',
                      currency='TWD', **totals(orders))
        if view == 'utm':
            groups = {}
            for order in orders:
                groups.setdefault(order.get('utm_medium') or '', []).append(order)
            result['groups'] = [dict(utm_medium=key, **totals(rows)) for key, rows in sorted(groups.items())][:50]
            result.update(group_count=len(groups),groups_truncated=len(groups)>50)
        if view == 'unshipped':
            result.update(unshipped(db, orders, offset, limit))
        return result


def unshipped(db, orders, offset=0, limit=50):
    """Outstanding physical demand, not merely a list of existing shipment drafts."""
    groups = {(r['account_id'], r['order_id']): r['id'] for r in db.execute('SELECT * FROM fulfillment_groups')}
    shipments = {}
    for row in db.execute('SELECT group_id,state,lines FROM shipments'):
        shipments.setdefault(row['group_id'], []).append(dict(row))
    additions = {}
    for row in db.execute('SELECT account_id,order_id,requirements FROM order_additions'):
        additions.setdefault((row['account_id'], row['order_id']), []).append(json.loads(row['requirements']))
    products = {r['id']:dict(r) for r in db.execute('SELECT p.id,p.name,d.sku FROM products p LEFT JOIN product_details d ON d.product_id=p.id')}
    rows = []
    for order in orders:
        if order['status'] != 'active':
            continue
        key = (order['account_id'], order['order_id'])
        required = {}
        for line in order['lines']:
            if line.get('line_type') == 'non_stock':
                continue
            for part in line.get('components') or [dict(product_id=line['product_id'], quantity=1)]:
                pid = part['product_id']
                required[pid] = required.get(pid, 0) + line['quantity'] * part['quantity']
        for addition in additions.get(key, []):
            for pid, count in addition.items():
                required[pid] = required.get(pid, 0) + count
        completed = {}
        related = shipments.get(groups.get(key), [])
        for shipment in related:
            if shipment['state'] == 'shipped':
                for line in json.loads(shipment['lines']):
                    pid = line['product_id']
                    completed[pid] = completed.get(pid, 0) + line['quantity']
        remaining = sum(max(0, count - completed.get(pid, 0)) for pid, count in required.items())
        if remaining:
            states = sorted({s['state'] for s in related if s['state'] != 'cancelled'})
            rows.append(dict(account_id=key[0], order_id=key[1], ordered_at=order['ordered_at'],
                             status='active', fulfillment_status='部分出貨' if any(completed.values()) else '尚未出貨',
                             items=[dict(product_id=pid, name=products.get(pid, {}).get('name', pid),
                                         sku=products.get(pid, {}).get('sku'), required_quantity=count,
                                         shipped_quantity=completed.get(pid, 0),
                                         remaining_quantity=max(0, count-completed.get(pid, 0)))
                                    for pid,count in sorted(required.items())],
                             remaining_quantity=remaining, shipment_states=states,
                             next_step='查看相關出貨單並核對配貨／揀貨進度' if states else '確認商品可用庫存並建立出貨單'))
    rows.sort(key=lambda o: (o['ordered_at'], o['account_id'], o['order_id']))
    return dict(unshipped_order_count=len(rows), orders=rows[offset:offset+limit], offset=offset, limit=limit,
                total_rows=len(rows), truncated=offset+limit<len(rows),
                priority_rule='依下單時間由早到晚供人工檢查；沒有交期／承諾日期，不能判定逾期或業務優先級',
                limitation='未出貨為有效訂單實體需求減目前完成出貨；實退不自動產生補寄需求。未判定缺貨原因。')


def query_shipping(path, arguments):
    if not isinstance(arguments,dict) or set(arguments)-{'start','end'}:
        raise ValueError('出貨統計只接受實際出退貨開始／結束日期，不支援套用下單來源或訂單狀態')
    if any(not isinstance(v,str) or len(v)>10 for v in arguments.values()):
        raise ValueError('日期須為 YYYY-MM-DD')
    store=ReadOnlyStore(path)
    with store.connect() as db:
        dates=[datetime.fromisoformat(r[0]).astimezone(TAIPEI).date() for r in db.execute("SELECT time FROM stock_events WHERE kind IN ('ship','return')")]
    today=datetime.now(TAIPEI).date().isoformat()
    start=arguments.get('start') or (min(dates).isoformat() if dates else today)
    end=arguments.get('end') or today
    data=shipping_statistics(store,start=start,end=end)
    return dict(query_id=uuid4().hex,generated_at=now(),tool='query_shipping',
        scope='目前工作庫已記錄的實際出退貨事件；含未連結營運訂單的獨立出貨，不代表全部商業歷史',
        requested_filters=dict(start=arguments.get('start',''),end=arguments.get('end','')),
        period_scope='指定日期範圍' if arguments.get('start') or arguments.get('end') else '目前工作庫全部已記錄事件，截至今日',
        recorded_event_count=len(dates),
        coverage_note='未指定日期即查全部已記錄事件；若全庫事件數為零，內部日期僅為計算預設，並非只查今日。data_complete 表示業務資料完整性，不代表查詢漏查歷史。',
        filters=dict(start=start,end=end,date_basis='實際出貨／實退日期',timezone='UTC+08:00'),
        totals=data['totals'],currency='TWD',data_complete=data['data_complete'],
        cost_basis=data['cost_basis'],rule_version=data['rule_version'],
        rules='出貨訂單數依履約群組去重，出貨單數依單據去重；部分出貨亦計入。撤銷完成不算，退貨另列。成本使用最新入庫成本更正，不是售價或含所有營運費用的總成本；待填成本不當零。',
        reconciliation='出貨總覽使用相同 shipping_statistics 計算；銷售訂單按下單日，不能直接把兩者當同一批訂單。')
