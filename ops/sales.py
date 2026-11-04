"""Read-only deterministic sales. CSV and HTTP use exactly this result."""
import csv
import io
import json
import re
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

from .store import now

TAIPEI = timezone(timedelta(hours=8))


def amount(cents):
    return f'{cents // 100}.{cents % 100:02d}'


def day(value, end=False):
    if not value:
        return None
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        result = date.fromisoformat(value)
        if end:
            result += timedelta(days=1)
        return datetime.combine(result, time.min, TAIPEI)
    except (ValueError, OverflowError):
        raise ValueError('查詢日期須為有效YYYY-MM-DD；結束日須可轉成次日邊界') from None


def sales(store, account_id='', product_id='', start='', end=''):
    beginning, ending = day(start), day(end, True)
    if beginning and ending and beginning >= ending:
        raise ValueError('開始日期不可晚於結束日期')
    with store.connect() as db:
        db.execute('BEGIN')  # One read snapshot for orders, mappings and pending scope.
        products = {r['id']: dict(r) for r in db.execute('SELECT * FROM products')}
        accounts = {r['id'] for r in db.execute('SELECT id FROM accounts')}
        if (account_id and account_id not in accounts) or (product_id and product_id not in products):
            raise ValueError('未知來源帳戶或商品篩選')
        lines, physical, order_keys = [], Counter(), set()
        total, quantity = 0, 0
        for order in store.current_orders(db):
            instant = datetime.fromisoformat(order['ordered_at'])
            if (order['status'] != 'active' or (account_id and order['account_id'] != account_id)
                    or (beginning and instant < beginning) or (ending and instant >= ending)):
                continue
            for line in order['lines']:
                if product_id and line['product_id'] != product_id:
                    continue
                total += line['amount_cents']
                quantity += line['quantity']
                order_keys.add((order['account_id'], order['order_id']))
                if line.get('line_type') == 'non_stock':
                    pass
                elif line['components']:
                    for component in line['components']:
                        physical[component['product_id']] += component['quantity'] * line['quantity']
                else:
                    physical[line['product_id']] += line['quantity']
                lines.append(dict(account_id=order['account_id'], platform=order['platform'], order_id=order['order_id'],
                                  revision=order['revision'], ordered_at=order['ordered_at'], line_id=line['line_id'],
                                  product_id=line['product_id'], product_name=line['item_name'] if line.get('line_type')=='non_stock' else products[line['product_id']]['name'],
                                  line_type=line.get('line_type', 'stock'),
                                  quantity=line['quantity'], amount=amount(line['amount_cents']), kol_ref=order['kol_ref'],
                                  batch_id=order['batch_id'], row_index=line['row_index']))
        return dict(amount=amount(total), order_count=len(order_keys), sales_quantity=quantity,
                    physical_quantities=[{'product_id': key, 'name': products[key]['name'], 'quantity': value}
                                         for key, value in sorted(physical.items())], lines=lines,
                    pending_count=store.pending_count(account_id, db), pending_scope='來源帳戶；不套日期或銷售品項',
                    filters={'account_id': account_id, 'product_id': product_id, 'start': start, 'end': end,
                             'timezone': 'UTC+08:00', 'end_inclusive': True},
                    rule_version='sales-v1', currency='TWD', generated_at=now())


def safe_cell(value):
    text = str(value)
    # Spreadsheet software may ignore leading whitespace before formula markers.
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')) else text


def export_csv(result):
    fields = ['record_type', 'account_id', 'platform', 'order_id', 'revision', 'ordered_at', 'line_id',
              'product_id', 'product_name', 'line_type', 'quantity', 'amount', 'kol_ref', 'batch_id', 'row_index',
              'rule_version', 'currency', 'generated_at', 'filters', 'report_amount', 'order_count',
              'sales_quantity', 'pending_count', 'pending_scope', 'physical_quantities']
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    metadata = {key: result[key] for key in ['rule_version', 'currency', 'generated_at', 'order_count',
                                            'sales_quantity', 'pending_count', 'pending_scope']}
    metadata.update(filters=json.dumps(result['filters'], ensure_ascii=False), report_amount=result['amount'],
                    physical_quantities=json.dumps(result['physical_quantities'], ensure_ascii=False))
    rows = result['lines'] or [{}]
    for line in rows:
        values = dict(line, **metadata, record_type='line' if line else 'empty_summary')
        writer.writerow({key: safe_cell(value) for key, value in values.items()})
    return out.getvalue()
