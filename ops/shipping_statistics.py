"""Read-only shipment statistics, grouped at physical-event and order grain."""
import json
from datetime import date, datetime, time, timedelta, timezone

from .catalogue import product, timestamp
from .stock_reports import TZ


def amount(cents):
    return ('-' if cents < 0 else '') + f'{abs(cents)//100}.{abs(cents)%100:02d}'


def bucket():
    return dict(shipped=0, returned=0, shipped_cents=0, returned_cents=0, shipped_cost_pending=0, returned_cost_pending=0, orders=set(), shipments=set())


def record(target, kind, cost, group_id, shipment_id):
    target['shipped' if kind == 'ship' else 'returned'] += 1
    target['shipped_cents' if kind == 'ship' else 'returned_cents'] += cost or 0
    target['shipped_cost_pending' if kind == 'ship' else 'returned_cost_pending'] += cost is None
    if kind == 'ship':
        target['orders'].add(group_id)
        target['shipments'].add(shipment_id)


def finish(value):
    return dict(shipped=value['shipped'], returned=value['returned'],
                net_shipped=value['shipped']-value['returned'],
                shipping_orders=len(value['orders']), shipment_count=len(value['shipments']),
                shipped_cost=None if value['shipped_cost_pending'] else amount(value['shipped_cents']),
                returned_cost=None if value['returned_cost_pending'] else amount(value['returned_cents']),
                shipped_cost_pending=value['shipped_cost_pending'], returned_cost_pending=value['returned_cost_pending'],
                shipped_cost_known_amount=amount(value['shipped_cents']),returned_cost_known_amount=amount(value['returned_cents']),
                net_cost=None if value['shipped_cost_pending'] or value['returned_cost_pending'] else amount(value['shipped_cents']-value['returned_cents']))


def shipping_statistics(store, start='', end='', as_of='', product_id='', channel=None):
    instant = datetime.fromisoformat(timestamp(as_of)) if as_of else datetime.now(timezone.utc)
    today = instant.astimezone(TZ).date()
    try:
        first = date.fromisoformat(start) if start else today.replace(day=1)
        last = date.fromisoformat(end) if end else today
        if last < first or (start and first.isoformat()!=start) or (end and last.isoformat()!=end):
            raise ValueError()
        left = datetime.combine(first,time(),TZ)
        right = datetime.combine(last+timedelta(days=1),time(),TZ)
    except (ValueError, OverflowError):
        raise ValueError('日期區間不正確，請使用YYYY-MM-DD且結束不早於開始') from None
    with store.connect() as db:
        db.execute('BEGIN')
        if product_id:
            product(db,product_id)
        products = {}
        reversed_ships={r['ship_event_id'] for r in db.execute('SELECT * FROM stock_ship_reversals')
                        if datetime.fromisoformat(r['reopened_at'])<=instant}
        total = bucket()
        channels, goods, daily, monthly, documents = {}, {}, {}, {}, {}
        available_channels = [r[0] for r in db.execute('SELECT DISTINCT channel FROM shipments ORDER BY channel')]
        rows = db.execute('''SELECT e.*,u.product_id,u.cost_cents,r.cost_status,s.channel,s.number,s.group_id
            FROM stock_events e JOIN stock_units u ON u.id=e.unit_id
            JOIN receipts r ON r.id=u.receipt_id
            JOIN shipments s ON s.id=e.shipment_id
            WHERE e.kind IN ('ship','return') ORDER BY e.time,e.rowid''')
        for row in rows:
            if row['kind']=='ship' and row['id'] in reversed_ships:
                continue
            at=datetime.fromisoformat(row['time'])
            if at>instant or not left<=at<right or (product_id and row['product_id']!=product_id):
                continue
            if channel is not None and row['channel']!=channel:
                continue
            pid=row['product_id']
            if pid not in products:
                products[pid]=product(db,pid)
            day=at.astimezone(TZ).date().isoformat()
            targets=[total, channels.setdefault(row['channel'],bucket()),goods.setdefault(pid,bucket()),
                     daily.setdefault(day,bucket()),monthly.setdefault(day[:7],bucket())]
            document=documents.setdefault(row['shipment_id'],dict(number=row['number'],channel=row['channel'],**bucket()))
            targets.append(document)
            cost=row['cost_cents'] if row['cost_status']=='complete' else None
            for target in targets:
                record(target,row['kind'],cost,row['group_id'],row['shipment_id'])
        coverage = db.execute("SELECT value FROM stock_meta WHERE key='coverage_since'").fetchone()[0]
        return dict(totals=finish(total),
            channels=[dict(channel=key,**finish(value)) for key,value in sorted(channels.items())],
            shipping_products=[dict(product_id=key,name=products[key]['name'],sku=products[key]['sku'],**finish(value)) for key,value in goods.items()],
            shipping_daily=[dict(day=key,**finish(value)) for key,value in sorted(daily.items())],
            shipping_monthly=[dict(month=key,**finish(value)) for key,value in sorted(monthly.items())],
            shipping_details=[dict(shipment_id=key,number=value['number'],channel=value['channel'],**finish(value)) for key,value in documents.items()],
            available_channels=available_channels,channel=channel,product_id=product_id,
            start=first.isoformat(),end=last.isoformat(),as_of=instant.isoformat(),timezone='UTC+08:00',
            cost_basis='latest_receipt_revision',cost_calculated_at=datetime.now(timezone.utc).isoformat(),
            rule_version='shipment-events-v3-latest-cost',data_complete=datetime.fromisoformat(coverage)<=left and right<=instant)
