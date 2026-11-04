"""orders-v1 parsing. Pure validation: no database, network or stock mutation."""
import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from decimal import Decimal

REQUIRED = ['order_id', 'source_revision', 'ordered_at', 'raw_status', 'currency',
            'expected_line_count', 'order_item_amount', 'line_id', 'external_sku',
            'item_name', 'quantity', 'line_item_amount']
FIELDS = REQUIRED + ['source_updated_at', 'customer_ref', 'kol_ref', 'utm_medium', 'order_total', 'line_type']
HEADERS = REQUIRED[:7] + ['source_updated_at', 'customer_ref', 'kol_ref', 'utm_medium', 'order_total']
MAX_BYTES = 2_000_000
MAX_ROWS = 5000


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def integer(value, label, minimum=0):
    if not re.fullmatch(r'[0-9]{1,9}', value) or int(value) < minimum:
        raise ValueError(f'{label} 須為 {minimum} 以上整數（最多9位）')
    return int(value)


def money(value):
    if not re.fullmatch(r'[0-9]{1,12}(\.[0-9]{1,2})?', value):
        raise ValueError('金額須為非負十進位，最多兩位小數與12位整數')
    return int(Decimal(value) * 100)


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or 'T' not in value:
            raise ValueError()
        return result.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        raise ValueError('日期須為含時區的ISO時間，例如2026-01-02T09:00:00+08:00') from None


def parse_orders(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('檔案須為UTF-8文字且不超過2MB')
    if '\x00' in text:
        raise ValueError('檔案含無效字元')
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff'), newline=''), strict=True)
    if (not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames)
            or not set(REQUIRED).issubset(reader.fieldnames)
            or not set(reader.fieldnames).issubset(FIELDS)):
        raise ValueError('CSV欄位缺少、重複或不屬於orders-v1，請使用標準範本')
    raw_rows, groups = [], {}
    try:
        for index, raw in enumerate(reader, 2):
            if index > MAX_ROWS + 1:
                raise ValueError('每批最多5000列')
            if None in raw or any(v is None for v in raw.values()):
                raise ValueError(f'第{index}列欄位數不一致')
            if any(len(v) > 2000 for v in raw.values()):
                raise ValueError(f'第{index}列欄位過長')
            raw_rows.append({'row_index': index, 'data': raw})
            clean = {field: raw.get(field, '').strip() for field in FIELDS}
            groups.setdefault(clean['order_id'], []).append((index, clean))
    except csv.Error as exc:
        raise ValueError(f'CSV格式錯誤：{exc}') from None
    if not raw_rows:
        raise ValueError('檔案沒有訂單明細')
    return raw_rows, [_validate_order(rows) for rows in groups.values()]


def _validate_order(rows):
    first = rows[0][1]
    result = {'order_id': first['order_id'], 'revision': None, 'lines': [],
              'errors': [], 'warnings': [], 'row_indices': [r[0] for r in rows]}
    seen = {}
    try:
        if not first['order_id']:
            raise ValueError('缺訂單ID')
        result.update(revision=integer(first['source_revision'], '來源版本'),
                      ordered_at=timestamp(first['ordered_at']), status=first['raw_status'],
                      currency=first['currency'], amount_cents=money(first['order_item_amount']),
                      source_updated_at=timestamp(first['source_updated_at']) if first['source_updated_at'] else '',
                      customer_ref=first['customer_ref'], kol_ref=first['kol_ref'])
        # Omit absent extensions from the fingerprint: old same-version files stay identical.
        if first['utm_medium']:
            result['utm_medium'] = first['utm_medium']
        if first['order_total']:
            result['order_total_cents'] = money(first['order_total'])
        if result['status'] not in ('active', 'cancelled'):
            raise ValueError('未知或尚未支援的訂單狀態')
        if result['currency'] != 'TWD':
            raise ValueError('本切片只支援TWD；不自動換匯')
        expected = integer(first['expected_line_count'], '預期明細數', 1)
        for index, row in rows:
            if any(row[key] != first[key] for key in HEADERS):
                raise ValueError(f'第{index}列訂單頭不一致／同批同單混合版本')
            line_type = row['line_type'] or 'stock'
            if line_type not in ('stock', 'non_stock'):
                raise ValueError(f'第{index}列明細類型須為 stock 或 non_stock')
            if line_type == 'non_stock' and row['external_sku']:
                raise ValueError(f'第{index}列非庫存明細不可填商品貨號，避免誤略實體出貨')
            if any(not row[key] for key in REQUIRED if not (key == 'external_sku' and line_type == 'non_stock')):
                raise ValueError(f'第{index}列有必填值空白')
            if row['line_id'] in seen:
                if seen[row['line_id']] != row:
                    raise ValueError(f'第{index}列同明細ID內容衝突')
                result['warnings'].append(f'第{index}列為相同明細重複，只計一次')
                continue
            seen[row['line_id']] = row
            result['lines'].append(dict(line_id=row['line_id'], external_sku=row['external_sku'],
                                        item_name=row['item_name'], quantity=integer(row['quantity'], '數量', 1),
                                        amount_cents=money(row['line_item_amount']), row_index=index))
            if line_type == 'non_stock':
                result['lines'][-1]['line_type'] = 'non_stock'
        if len(seen) != expected:
            raise ValueError(f'完整性不符：聲明{expected}筆明細，實際{len(seen)}筆')
        if sum(line['amount_cents'] for line in result['lines']) != result['amount_cents']:
            raise ValueError('明細金額合計與訂單商品總額不符')
        canonical = {key: value for key, value in result.items()
                     if key not in ('row_indices', 'warnings', 'errors', 'lines')}
        canonical['lines'] = sorted([{k: v for k, v in line.items() if k != 'row_index'}
                                      for line in result['lines']], key=lambda x: x['line_id'])
        result['fingerprint'] = digest(canonical)
    except ValueError as exc:
        result['errors'].append(str(exc))
    return result
