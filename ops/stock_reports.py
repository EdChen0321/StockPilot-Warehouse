"""Read stock events at their own grain; current unit state cannot erase history."""
import json
import csv
import io
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .catalogue import product, timestamp
from .store import now
from .transactions import command
from .locations import exportable

TZ = timezone(timedelta(hours=8))


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS stock_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS alert_settings(product_id TEXT PRIMARY KEY REFERENCES products(id),stock_min INTEGER NOT NULL,coverage_days TEXT NOT NULL);
    ''')
    db.execute("INSERT OR IGNORE INTO stock_meta VALUES('coverage_since',?)", (now(),))


def save_alert(store, body):
    def apply(db):
        p = product(db, body.get('product_id'))
        minimum = body.get('stock_min')
        days = body.get('coverage_days')
        if p['kind'] != 'product' or type(minimum) is not int or not 0 <= minimum <= 1000000:
            raise ValueError('庫存門檻須為0至1000000的整數，僅適用單品')
        try:
            valid = isinstance(days, str) and len(days) <= 20 and Decimal(days).is_finite() and 0 <= Decimal(days) <= 3650
        except InvalidOperation:
            valid = False
        if not valid:
            raise ValueError('覆蓋天數門檻須為0至3650的數字')
        db.execute('INSERT INTO alert_settings VALUES(?,?,?) ON CONFLICT(product_id) DO UPDATE SET stock_min=excluded.stock_min,coverage_days=excluded.coverage_days', (p['id'], minimum, days))
        return dict(id=p['id'], stock_min=minimum, coverage_days=days)
    return command(store, 'alert_save', body, apply)


def as_amount(cents):
    return f'{cents // 100}.{cents % 100:02d}'


def export_report(result, kind):
    fields = {
        **{kind: prefix+['shipping_orders','shipment_count','shipped','returned','net_shipped','shipped_cost','returned_cost'] for kind,prefix in (
            ('channels',['channel']),('shipping_products',['product_id','name','sku']),('shipping_daily',['day']),
            ('shipping_monthly',['month']),('shipping_details',['shipment_id','number','channel']))},
        'inventory': ['product_id','name','sku','on_hand','staged','allocated','available','stock_cost','shipped','stock_min','stock_alert','coverage_days','coverage_threshold','coverage_alert','coverage_status'],
        'monthly': ['month','received','shipped','returned','shipping_orders','shipment_count'],
        'daily': ['day','received','shipped','returned','shipping_orders','shipment_count'],
        'totals': ['received','shipped','returned','shipping_orders','shipment_count','received_cost','shipped_cost'],
    }
    if kind not in fields:
        raise ValueError('匯出類別須為inventory、daily、monthly或totals')
    for field in list(fields[kind]):
        if field.endswith('_cost'):
            fields[kind].extend([field+'_pending',field+'_known_amount'])
    meta = {k:result[k] for k in ('start','end','as_of','timezone','rule_version','data_complete')}
    meta.update(cost_basis=result.get('cost_basis','latest_receipt_revision'),cost_calculated_at=result.get('cost_calculated_at',result['as_of']))
    meta['product_id_filter']=result.get('product_id','')
    if 'channel' in result:
        meta['channel_filter']=result['channel']
    rows = [result['totals']] if kind=='totals' else result[kind]
    out=io.StringIO(newline='')
    writer=csv.DictWriter(out,fieldnames=fields[kind]+list(meta))
    writer.writeheader()
    for row in rows:
        values={k:row.get(k) for k in fields[kind]}
        values.update(meta)
        for key,value in values.items():
            if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@','\t','\r','\n')):
                values[key]="'"+value
        writer.writerow(values)
    return out.getvalue()


def report(store, start='', end='', as_of='', product_id=''):
    instant = datetime.fromisoformat(timestamp(as_of)) if as_of else datetime.now(timezone.utc)
    today = instant.astimezone(TZ).date()
    try:
        from_date = date.fromisoformat(start) if start else today - timedelta(days=30)
        end_date = date.fromisoformat(end) if end else today - timedelta(days=1)
        if (start and from_date.isoformat() != start) or (end and end_date.isoformat() != end) or end_date < from_date:
            raise ValueError()
        left = datetime.combine(from_date, time(), TZ)
        right = datetime.combine(end_date + timedelta(days=1), time(), TZ)
    except (ValueError, OverflowError):
        raise ValueError('日期區間不正確，請使用YYYY-MM-DD且結束不早於開始') from None
    days = (end_date - from_date).days + 1
    with store.connect() as db:
        db.execute('BEGIN')
        if product_id:
            product(db,product_id)
        coverage_since = datetime.fromisoformat(db.execute("SELECT value FROM stock_meta WHERE key='coverage_since'").fetchone()[0])
        complete = coverage_since <= left and right <= datetime.combine(today, time(), TZ)
        units = {r['id']: dict(r) for r in db.execute('SELECT u.*,r.cost_status FROM stock_units u JOIN receipts r ON r.id=u.receipt_id')}
        for unit in units.values():
            if unit['cost_status']=='pending': unit['cost_cents']=None
        documents = {r['id']: dict(r) for r in db.execute('SELECT * FROM shipments')} if product_id else {}
        stock_changes, unit_records = {}, []
        states, quantities, monthly, daily = {}, {}, {}, {}
        totals = dict(received=0, shipped=0, returned=0, shipping_orders=0, shipment_count=0)
        orders, shipments = set(), set()
        received_cost = shipped_cost = 0
        received_cost_pending = shipped_cost_pending = 0
        reversed_ships={r['ship_event_id'] for r in db.execute('SELECT * FROM stock_ship_reversals')
                        if datetime.fromisoformat(r['reopened_at'])<=instant}
        events = db.execute('SELECT * FROM stock_events ORDER BY time,rowid')
        for row in events:
            event = dict(row); at = datetime.fromisoformat(event['time'])
            if at > instant:
                continue
            if event['kind'] in ('order_cancel', 'quality_unallocate', 'work_state', 'shipment_lines', 'cancel_reopen'):
                continue  # Document audit only; per-unit release events rebuild stock.
            details = json.loads(event['details']); unit = units[event['unit_id']]; pid = unit['product_id']; kind = event['kind']
            if product_id and pid!=product_id:
                continue
            previous = states.get(unit['id'])
            was_on_hand = bool(previous and previous['location'] in ('warehouse', 'staging'))
            if kind == 'receipt':
                states[unit['id']] = dict(product_id=pid, location='warehouse', quality='good', packaging='good', allocated=False, return_hold=False, can_export=details.get('can_export',True),transferring=False,cost=unit['cost_cents'])
            state = states.get(unit['id'])
            if state:
                if kind == 'allocate': state['allocated'] = True
                elif kind == 'release': state['allocated'] = False
                elif kind == 'deduct': state.update(location='staging',allocated=False)
                elif kind == 'deduct_cancel': state.update(location='warehouse',allocated=False)
                elif kind == 'ship': state.update(location='customer', allocated=False)
                elif kind == 'ship_reopen': state.update(location='warehouse', allocated=True)
                elif kind == 'return': state.update(location='warehouse', quality=details.get('quality','pending'), allocated=False, return_hold=True,can_export=details.get('can_export',True))
                elif kind == 'return_release': state.update(quality=details.get('after',{}).get('quality','good'), packaging=details.get('after',{}).get('packaging','good'), return_hold=False)
                elif kind == 'quality': state.update(quality=details['after']['quality'], packaging=details['after']['packaging'])
                elif kind in ('putaway','place_rule','transfer_complete'): state.update(quality=details['after']['quality'],packaging=details['after']['packaging'],can_export=details['can_export'])
                elif kind == 'storage_enable': state['can_export']=details['can_export']
                elif kind == 'transfer_lock': state['transferring']=True
                elif kind == 'transfer_unlock': state['transferring']=False
                if kind in ('quality','return','return_release','putaway','place_rule','transfer_complete') and 'to_place' in details:
                    state['can_export']=exportable(state,details['to_place'])
            if product_id:
                day = at.astimezone(TZ).date().isoformat()
                is_on_hand = bool(state and state['location'] in ('warehouse', 'staging'))
                stock_changes[day] = stock_changes.get(day, 0) + int(is_on_hand) - int(was_on_hand)
            if (kind=='ship' and event['id'] in reversed_ships) or not left <= at < right:
                continue
            if product_id and kind in ('ship', 'return'):
                document = documents.get(event['shipment_id'], {})
                unit_records.append(dict(day=at.astimezone(TZ).date().isoformat(), time=event['time'],
                    kind=kind, unit_id=unit['id'], shipment_id=event['shipment_id'],
                    number=document.get('number', ''), channel=document.get('channel', '')))
            month = at.astimezone(TZ).strftime('%Y-%m')
            bucket = monthly.setdefault(month, dict(month=month, received=0, shipped=0, returned=0, orders=set(), shipments=set()))
            if kind == 'receipt':
                totals['received'] += 1; bucket['received'] += 1; received_cost += unit['cost_cents'] or 0
                received_cost_pending += unit['cost_cents'] is None
            elif kind == 'ship':
                totals['shipped'] += 1; bucket['shipped'] += 1; shipped_cost += unit['cost_cents'] or 0
                shipped_cost_pending += unit['cost_cents'] is None
                orders.add(details['group_id']); shipments.add(event['shipment_id'])
                bucket['orders'].add(details['group_id']); bucket['shipments'].add(event['shipment_id'])
                quantities[pid] = quantities.get(pid, 0) + 1
            elif kind == 'return':
                totals['returned'] += 1; bucket['returned'] += 1
            if kind in ('receipt','ship','return'):
                day=at.astimezone(TZ).date().isoformat()
                d=daily.setdefault(day,dict(day=day,received=0,shipped=0,returned=0,orders=set(),shipments=set()))
                d[{'receipt':'received','ship':'shipped','return':'returned'}[kind]]+=1
                if kind=='ship':
                    d['orders'].add(details['group_id']);d['shipments'].add(event['shipment_id'])
        inventory = []
        for row in db.execute("SELECT id FROM products WHERE kind='product' ORDER BY rowid"):
            if product_id and row[0]!=product_id:
                continue
            p = product(db, row[0]); own = [u for u in states.values() if u['product_id'] == p['id'] and u['location'] in ('warehouse','staging')]
            available = sum(u['location']=='warehouse' and u['quality'] == 'good' and u['packaging'] == 'good' and not u['allocated'] and not u['return_hold'] and u['can_export'] and not u['transferring'] for u in own)
            setting = db.execute('SELECT * FROM alert_settings WHERE product_id=?', (p['id'],)).fetchone()
            shipped = quantities.get(p['id'], 0)
            coverage = Decimal(available) * days / shipped if complete and shipped else None
            inventory.append(dict(product_id=p['id'], name=p['name'], sku=p['sku'], on_hand=len(own),
                allocated=sum(u['allocated'] for u in own), staged=sum(u['location']=='staging' for u in own), available=available,
                stock_cost=None if any(u['cost'] is None for u in own) else as_amount(sum(u['cost'] for u in own)),
                stock_cost_pending=sum(u['cost'] is None for u in own),stock_cost_known_amount=as_amount(sum(u['cost'] or 0 for u in own)),
                shipped=shipped, daily_shipped=str(Decimal(shipped)/days) if complete else None,
                coverage_days=str(coverage.quantize(Decimal('0.01'))) if coverage is not None else None,
                stock_min=setting['stock_min'] if setting else None, coverage_threshold=setting['coverage_days'] if setting else None,
                stock_alert=available < setting['stock_min'] if setting else None,
                coverage_alert=coverage < Decimal(setting['coverage_days']) if coverage is not None and setting else None,
                coverage_status='資料不足' if not complete else '無近期出貨，無法估計' if not shipped else '可計算'))
        totals.update(shipping_orders=len(orders), shipment_count=len(shipments),
            received_cost=None if received_cost_pending else as_amount(received_cost),shipped_cost=None if shipped_cost_pending else as_amount(shipped_cost),
            received_cost_pending=received_cost_pending,shipped_cost_pending=shipped_cost_pending,
            received_cost_known_amount=as_amount(received_cost),shipped_cost_known_amount=as_amount(shipped_cost))
        months = [dict(month=m['month'], received=m['received'], shipped=m['shipped'], returned=m['returned'], shipping_orders=len(m['orders']), shipment_count=len(m['shipments'])) for m in monthly.values()]
        daily_rows=[dict(day=d['day'],received=d['received'],shipped=d['shipped'],returned=d['returned'],shipping_orders=len(d['orders']),shipment_count=len(d['shipments'])) for d in daily.values()]
        daily_stock = []
        if product_id:
            balance = sum(change for day, change in stock_changes.items() if day < from_date.isoformat())
            day = from_date
            while day <= min(end_date, today):
                change = stock_changes.get(day.isoformat(), 0)
                balance += change
                known = datetime.combine(day, time(), TZ) >= coverage_since
                daily_stock.append(dict(day=day.isoformat(), on_hand=balance if known else None,
                                        change=change if known else None))
                day += timedelta(days=1)
        return dict(totals=totals, inventory=inventory, monthly=months, daily=daily_rows,
                    daily_stock=daily_stock, unit_records=unit_records, product_id=product_id, as_of=instant.isoformat(), start=from_date.isoformat(), end=end_date.isoformat(),
                    cost_basis='latest_receipt_revision',cost_calculated_at=datetime.now(timezone.utc).isoformat(),
                    days=days, data_complete=complete, coverage_since=coverage_since.isoformat(), timezone='UTC+08:00', rule_version='stock-events-v3-latest-cost')
