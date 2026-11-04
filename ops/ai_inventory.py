"""Fixed inventory views over existing read models; never expose SQL or commands."""
from datetime import datetime
from decimal import Decimal
from .ai_queries import ReadOnlyStore
from .stock import Stock
from .locations import Locations
from .stock_reports import report
from .sales import day
from .store import now
from uuid import uuid4

VIEWS = ('summary', 'products', 'units', 'receipts', 'shipments', 'events',
         'cost_history', 'locations', 'transfers', 'report', 'report_daily', 'report_monthly')
COUNTS = ('on_hand', 'warehouse', 'staged', 'allocated', 'available', 'box_available',
          'unassigned', 'transferring', 'pending', 'defective', 'damaged',
          'packaging_damaged', 'return_held', 'stock_cost_pending')
PRODUCT_FIELDS = ('id', 'name', 'sku', 'barcode', 'group_name', 'specification', 'active',
                  'stock_cost_amount', 'stock_cost_known_amount') + COUNTS
UNIT_FIELDS = ('id', 'product_id', 'receipt_id', 'shipment_id', 'first_received_at', 'fifo_at',
               'location', 'quality', 'packaging', 'cost_cents', 'cost_status', 'cost_version',
               'place_id', 'place_label', 'place_eligible', 'transfer_id', 'return_hold')
DOCUMENT_FIELDS = ('id', 'number', 'state', 'parent_id', 'created_at', 'confirmed_at',
                   'shipped_at', 'first_completed_at', 'cancelled_at', 'cost_status',
                   'cost_version', 'freight_cents', 'tax_cents', 'allocation',
                   'purchase_id', 'production_plan_id', 'channel', 'reopen_count', 'source')


def select(row, fields):
    return {key: row[key] for key in fields if key in row}


def query_inventory(path, arguments):
    allowed = {'view', 'search', 'status', 'start', 'end', 'as_of', 'offset', 'limit'}
    if not isinstance(arguments, dict) or set(arguments) - allowed:
        raise ValueError('庫存只接受固定查詢條件，不接受 SQL 或寫入操作')
    for key, value in arguments.items():
        if key in ('offset', 'limit'):
            if type(value) is not int:
                raise ValueError('分頁須為整數')
        elif not isinstance(value, str) or len(value) > 200:
            raise ValueError('查詢條件須為 200 字內文字')
    view = arguments.get('view', 'summary')
    status = arguments.get('status', 'all') or 'all'
    search = arguments.get('search', '').strip().casefold()
    offset, limit = arguments.get('offset', 0), arguments.get('limit', 50)
    if view not in VIEWS or offset < 0 or not 1 <= limit <= 50:
        raise ValueError('庫存查詢種類或分頁範圍不正確')
    statuses = {
        'summary': ('all', 'in_stock', 'available', 'unassigned', 'return_held', 'defective', 'damaged', 'packaging_damaged', 'allocated', 'transferring'),
        'products': ('all', 'active', 'inactive', 'in_stock', 'available', 'unassigned', 'return_held', 'defective', 'damaged', 'packaging_damaged', 'allocated', 'transferring'),
        'units': ('all', 'warehouse', 'staging', 'customer', 'available', 'unassigned', 'return_held', 'allocated', 'transferring', 'good', 'pending', 'defective', 'damaged', 'vendor_exchange', 'other', 'packaging_damaged'),
        'receipts': ('all', 'draft', 'ordered', 'confirmed', 'cancelled'),
        'shipments': ('all', 'draft', 'allocated', 'picked', 'staged', 'shipped', 'cancelled'),
        'events': ('all',), 'cost_history': ('all',), 'locations': ('all',),
        'transfers': ('all', 'draft', 'moving', 'complete', 'cancelled'), 'report': ('all',),
        'report_daily': ('all',), 'report_monthly': ('all',),
    }
    if status not in statuses[view]:
        raise ValueError('此庫存查詢不支援指定狀態')
    start, end = arguments.get('start', ''), arguments.get('end', '')
    as_of = arguments.get('as_of', '')
    if as_of and not view.startswith('report'):
        raise ValueError('歷史時點只適用庫存報表')
    left, right = day(start), day(end, True)
    if left and right and left >= right:
        raise ValueError('開始日期不可晚於結束日期')
    if view in ('summary', 'products', 'units', 'locations') and (start or end):
        raise ValueError('目前庫存不套用期間；歷史與期間統計請用 report')
    store = ReadOnlyStore(path)
    snapshot = Stock(store).snapshot()
    products = {p['id']: p for p in snapshot['products']}
    units = {u['id']: u for u in snapshot['units']}

    def contains(row, fields):
        return any(search in str(row.get(k) or '').casefold() for k in fields)

    exact = {pid for pid, p in products.items() if search and any(search == str(p.get(k) or '').casefold() for k in ('id', 'sku', 'barcode'))}
    pids = exact or {pid for pid, p in products.items() if not search or contains(p, ('id', 'name', 'sku', 'barcode', 'group_name', 'specification'))}
    receipt_ids = {r['id'] for r in snapshot['receipts'] if search and contains(r, ('id', 'number'))}
    shipment_ids = {s['id'] for s in snapshot['shipments'] if search and contains(s, ('id', 'number'))}

    def product_info(pid):
        p = products.get(pid, {})
        return dict(product_name=p.get('name'), sku=p.get('sku'), barcode=p.get('barcode'))

    def in_period(value):
        if not (left or right):
            return True
        if not value:
            return False
        at = datetime.fromisoformat(value)
        return (not left or at >= left) and (not right or at < right)

    result = dict(tool='query_inventory', query_id=uuid4().hex, generated_at=now(),
        scope='目前服務連接的庫存資料庫；唯讀，非外部庫存表', filters=dict(arguments),
        currency='TWD', cost_basis='latest_receipt_revision',
        rules='在庫含 warehouse 與 staging；可出貨依既有品質、包裝、占用、退貨限制、櫃位及調撥規則。各狀態可能重疊，不可相加當總庫存。成本缺漏不當零；成本分欄為 cents，amount 欄為元。',
        date_basis='目前狀態，不受歷史出貨期間限制')
    rows = []
    if view in ('summary', 'products'):
        for p in products.values():
            if p['id'] not in pids:
                continue
            match = status == 'all' or (status == 'active' and p['active']) or (status == 'inactive' and not p['active'])
            match = match or (status == 'in_stock' and p['on_hand'] > 0) or (status in COUNTS and p[status] > 0)
            if match:
                rows.append(select(p, PRODUCT_FIELDS))
        result['totals'] = {key: sum(p[key] for p in rows) for key in COUNTS}
        result['totals'].update(product_count=len(rows), in_stock_sku_count=sum(p['on_hand'] > 0 for p in rows))
        known_cost = format(sum((Decimal(p['stock_cost_known_amount']) for p in rows), Decimal(0)), '.2f')
        result['totals'].update(stock_cost_known_amount=known_cost,
            stock_cost_amount=None if result['totals']['stock_cost_pending'] else known_cost)
        result['count_basis'] = '商品種類按單品 SKU 計；不含套組主檔。product_count 含零庫存商品，in_stock_sku_count 僅在庫大於零。總數涵蓋全部符合條件資料，不只本頁。'
    elif view == 'units':
        for u in units.values():
            if search and u['product_id'] not in pids and u['receipt_id'] not in receipt_ids and u['shipment_id'] not in shipment_ids and not contains(u, ('id', 'receipt_id', 'shipment_id', 'place_id', 'place_label')):
                continue
            physical = u['location'] in ('warehouse', 'staging')
            flags = dict(available=u['location'] == 'warehouse' and u['quality'] == 'good' and u['packaging'] == 'good' and not u['shipment_id'] and not u['return_hold'] and u['place_eligible'],
                unassigned=physical and not u['place_id'], return_held=physical and u['return_hold'],
                allocated=physical and bool(u['shipment_id']), transferring=physical and bool(u['transfer_id']),
                packaging_damaged=physical and u['packaging'] == 'damaged')
            if status != 'all' and not flags.get(status, False) and status != u['location'] and not (physical and status == u['quality']):
                continue
            rows.append({**select(u, UNIT_FIELDS), **product_info(u['product_id'])})
        result['count_basis'] = '每列一件；all 包含已出庫件，不可把總列數當現有庫存。'
    elif view in ('receipts', 'shipments'):
        result['date_basis'] = '單據建立日（台北）；實際入出退貨期間請查 report'
        for doc in snapshot[view]:
            if status != 'all' and doc['state'] != status:
                continue
            if not in_period(doc.get('created_at')):
                continue
            if search and not contains(doc, ('id', 'number')) and not any(line['product_id'] in pids for line in doc['lines']):
                continue
            row = select(doc, DOCUMENT_FIELDS)
            if view == 'receipts':
                if not doc.get('freight_known', 1): row['freight_cents'] = None
                if not doc.get('tax_known', 1): row['tax_cents'] = None
            row['line_count'] = len(doc['lines'])
            row['lines_truncated'] = len(doc['lines']) > 50
            row['lines'] = [{**select(line, ('product_id', 'quantity', 'unit_price_cents')), **product_info(line['product_id'])} for line in doc['lines'][:50]]
            rows.append(row)
    elif view in ('events', 'cost_history'):
        result['date_basis'] = '實際異動／成本修訂時間（台北）；原始事件含撤銷紀錄，不直接加總為有效出貨'
        for event in snapshot['events' if view == 'events' else 'unit_cost_revisions']:
            unit = units.get(event.get('unit_id'), {})
            if not in_period(event['time']):
                continue
            if search and unit.get('product_id') not in pids and event.get('receipt_id') not in receipt_ids and event.get('shipment_id') not in shipment_ids and not contains(event, ('id', 'unit_id', 'receipt_id', 'shipment_id', 'kind')):
                continue
            details = event['details']
            row = select(event, ('id', 'kind', 'unit_id', 'receipt_id', 'shipment_id', 'time'))
            row.update(product_info(unit.get('product_id')))
            row['details'] = select(details, ('before_cost_cents', 'after_cost_cents', 'cost_version', 'place_id', 'transfer_id', 'flow', 'can_export', 'reason'))
            if isinstance(details.get('item_snapshot'), dict):
                row['details']['item_snapshot'] = select(details['item_snapshot'], ('name', 'sku', 'barcode', 'receipt_number', 'source', 'cost_cents', 'cost_status', 'cost_version'))
            for key in ('before', 'after'):
                if isinstance(details.get(key), dict):
                    row['details'][key] = select(details[key], UNIT_FIELDS)
            rows.append(row)
    elif view in ('locations', 'transfers'):
        data = Locations(store).snapshot()
        result['storage_enabled'] = data['enabled']
        result['date_basis'] = '目前櫃位狀態' if view == 'locations' else '調撥單建立日（台北）'
        for entry in data['places' if view == 'locations' else 'transfers']:
            if view == 'locations':
                fields = ('id', 'address', 'position', 'name', 'unit_count', 'can_import', 'can_export', 'can_return', 'entry_state')
                match = not search or contains(entry, fields[:4])
            else:
                fields = ('id', 'number', 'from_place_id', 'to_place_id', 'unit_ids', 'state', 'created_at', 'updated_at')
                match = (status == 'all' or entry['state'] == status) and in_period(entry['created_at'])
                match = match and (not search or contains(entry, fields[:4]) or any(units.get(uid, {}).get('product_id') in pids or search == uid for uid in entry['unit_ids']))
            if match:
                row = select(entry, fields)
                if view == 'transfers':
                    row.update(unit_count=len(entry['unit_ids']), unit_ids=entry['unit_ids'][:50], unit_ids_truncated=len(entry['unit_ids']) > 50)
                rows.append(row)
    else:
        if search and len(pids) != 1:
            raise ValueError('期間庫存報表請提供可唯一識別的 SKU；未指定則查全部商品')
        data = report(store, start=start, end=end, as_of=as_of, product_id=next(iter(pids)) if search else '')
        result.update(select(data, ('totals', 'as_of', 'start', 'end', 'days', 'data_complete', 'coverage_since', 'timezone', 'rule_version')))
        result['date_basis'] = '入出退貨按事件日期；庫存為 as_of 時點。未指定期間沿既有報表：截至昨日的近30天。'
        result['rules'] += ' 未設定低庫存門檻、期間資料不足或無近期出貨時，不推估補貨或支應天數。'
        rows = data[{'report': 'inventory', 'report_daily': 'daily', 'report_monthly': 'monthly'}[view]]
    result.update(total_rows=len(rows), offset=offset, limit=limit, rows=rows[offset:offset + limit],
                  next_offset=offset + limit if offset + limit < len(rows) else None,
                  truncated=offset > 0 or offset + limit < len(rows))
    return result
