"""Bounded XLSX ingestion for analysis only; never creates inventory movements."""
import base64
import binascii
import hashlib
import io
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4
from uuid import UUID
from zipfile import BadZipFile, ZipFile
from xml.parsers import expat
from xml.etree.ElementTree import ParseError

from .store import ConflictError, encode, now, required_text
from .sales import amount

RULES_VERSION = 'legacy-full-v1'
SINGLE_FILE_VERSION = 'order-file-v1'
MAX_BYTES = 2_000_000
REQUIRED = {'訂單號碼','商品貨號','商品名稱','訂單狀態','訂單合計','顧客 ID','訂單日期','數量','商品結帳價'}
INTERNAL = ('A1保固','A1回收','A2保固','A2-保固','B1退貨','D1業配','M1保固','P1代客',
            'V1 香港批發','V1香港批發','K1批發','J2換貨','ticket','UTB','補刷')

# Domain correction data, independently represented from the confirmed legacy rules.
EARLY = (
    ('EX-01014','EX-01013',('七澤美亞',)), ('EX-01017','EX-01014',('安齋拉拉',)),
    ('GX-01001','GX-16001',('天王星','清潔慕斯')), ('GX-01002','GX-16002',('海王星','抑菌')),
    ('GX-05010','HM-09001',('GX 潮吹液',)), ('HO-02001','HO-02003',('共鳴按摩棒','Kurve')),
    ('HO-02003','HO-02004',('轟炸機','JETT')), ('IN-06009','IN-06012',('琴通寧',)),
    ('MC-01001','MC-14001',('高校女友',)), ('MC-01001','MC-14002',('純潔小妹','花結子')),
    ('MC-01003','MC-01001',('露天女孩','P妹')), ('MC-01004','MC-14003',('魅惑妖姬',)),
    ('MC-01005','MC-01002',('鍊金祭司',)), ('MG-01014','MG-01011',('蘿莉口交',)),
    ('PJ-05017','PJ-05016',('矽性潤滑液',)), ('TH-01008','MG-01033',('箱詰之娘',)),
    ('TH-01019','TH-01022',('R-20 三代','R20 三代')), ('TH-01020','TH-01021',('R-20 二代',)),
    ('TH-01021','TH-01020',('R-20 一代',)), ('TH-01022','TH-01023',('豪乳傳奇',)),
    ('TH-01023','TH-01024',('地方媽媽',)), ('TH-01027','TH-01018',('天使',)),
    ('TH-01028','TH-01027',('公眾欲情',)), ('TH-01029','TH-01028',('17α',)),
    ('TH-01029','UD-07006',('情萌幼女',)), ('UD-07001','UD-07006',('隱形貓咪',)),
    ('UD-07001','UD-07003',('瘋狂企鵝',)), ('UD-07001','UD-07005',('激凸蝴蝶',)),
    ('UD-07001','UD-07004',('熱情斑馬',)), ('UD-07001','UD-07002',('森林之王',)),
    ('VM-06002','VM-06001',('仙女霜','小藍瓶')), ('VM-06003','VM-06002',('小綠瓶','活力喚醒')),
)


def text(value):
    if value is None:
        return ''
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value).strip()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()).hexdigest()


def read_xlsx(blob, filename):
    """Read values, rejecting formulas and oversized archives before XML parsing."""
    if not isinstance(blob, bytes) or not 0 < len(blob) <= MAX_BYTES:
        raise ValueError('Excel 檔案須為 2 MB 以內')
    if not isinstance(filename, str) or not filename.lower().endswith('.xlsx'):
        raise ValueError('目前只支援 .xlsx 檔案')
    try:
        with ZipFile(io.BytesIO(blob)) as archive:
            members = archive.infolist()
            if len(members) > 1000 or sum(m.file_size for m in members) > 32_000_000:
                raise ValueError('Excel 解壓大小或項目數超過限制')
            if any(m.flag_bits & 1 or 'vbaproject' in m.filename.lower() or 'externallinks/' in m.filename.lower() for m in members):
                raise ValueError('不接受加密、巨集或外部連結')
            # Parse encoding-aware declarations before openpyxl; no DTD/entities allowed.
            def reject_declaration(*_):
                raise ValueError('不接受 XML DTD 或外部實體')
            for member in members:
                if member.filename.lower().endswith(('.xml','.rels')):
                    raw = archive.read(member)
                    parser = expat.ParserCreate()
                    parser.StartDoctypeDeclHandler = reject_declaration
                    parser.EntityDeclHandler = reject_declaration
                    parser.ExternalEntityRefHandler = reject_declaration
                    parser.Parse(raw, True)
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(blob), read_only=True, data_only=False, keep_links=False)
        try:
            sheets = [s for s in workbook.worksheets if s.sheet_state == 'visible']
            if len(sheets) != 1:
                raise ValueError('請提供只有一個可見工作表的 Excel')
            sheet = sheets[0]
            # Some exporters underreport dimensions. Inspect actual cells instead.
            sheet.reset_dimensions()
            iterator = sheet.iter_rows()
            header = next(iterator, ())
            if len(header) > 200:
                raise ValueError('Excel 最多 200 欄')
            if any(c.data_type in ('f','e') for c in header):
                raise ValueError('Excel 欄名不能是公式或錯誤')
            names = [text(c.value) for c in header]
            if not names or '' in names or len(set(names)) != len(names):
                raise ValueError('Excel 欄名不可空白或重複')
            rows = []
            for index, cells in enumerate(iterator, 2):
                if len(cells) > 200 or (len(cells) > len(names) and any(c.value is not None for c in cells[len(names):])):
                    raise ValueError(f'第 {index} 列超出表頭欄位範圍')
                if index > 10001:
                    raise ValueError('Excel 工作表實體列數超過限制')
                if all(c.value is None for c in cells):
                    continue
                if len(rows) >= 5000:
                    raise ValueError('每批最多 5000 筆資料列')
                if any(c.data_type in ('f','e') for c in cells):
                    raise ValueError(f'第 {index} 列含公式或 Excel 錯誤，請先轉為值')
                values = [text(c.value) for c in cells]
                values.extend([''] * (len(names)-len(values)))
                if any(len(v) > 10000 for v in values):
                    raise ValueError(f'第 {index} 列儲存格過長')
                rows.append(dict(row_index=index, data=dict(zip(names, values))))
            if not rows:
                raise ValueError('Excel 沒有資料列')
            return rows
        finally:
            workbook.close()
    except ImportError:
        raise ValueError('請先安裝 requirements.txt 的 Excel 解析依賴') from None
    except (BadZipFile, OSError, KeyError, TypeError, ParseError, expat.ExpatError, IndexError) as exc:
        raise ValueError('Excel 檔案損壞或格式不正確') from exc


def product_code(sku):
    return re.sub(r'(?<=\d)[A-Z]{2,3}$', '', sku)


def load_catalog(path):
    try:
        blob = Path(path).read_bytes()
    except OSError:
        raise ValueError('找不到商品對照表，請以 --kol-catalog 指定產品條碼與分類.xlsx') from None
    raw = read_xlsx(blob, Path(path).name)
    if not {'產品代號','產品名稱','產品類型'}.issubset(raw[0]['data']):
        raise ValueError('商品對照表缺少產品代號、產品名稱或產品類型')
    seen, products = set(), []
    for row in raw:
        d = row['data']; sku = text(d['產品代號'])
        if not sku or sku in seen:
            continue
        seen.add(sku)
        products.append(dict(sku=sku,name=text(d['產品名稱']),category=text(d['產品類型']),product_code=product_code(sku)))
    if not products:
        raise ValueError('商品對照表沒有商品')
    return products, hashlib.sha256(blob).hexdigest()


def normalize(sku):
    multiplier = 1
    match = re.fullmatch(r'(.+)\*(\d+)', sku)
    if match:
        sku, multiplier = match[1], int(match[2])
    sku = sku.removeprefix('#')
    sku = re.sub(r'^((?:[A-Z]{2}-){1,3})X(?=\d)', r'\1', sku)
    return sku, multiplier


def manual_fix(sku):
    shark = re.fullmatch(r'(?:(?:CW|CH|CS|GH|GW|GS)-)?HO-01001(X?)', sku)
    if shark:
        return 'HO-02002' if shark[1] else 'HO-02001'
    bird = re.fullmatch(r'(?:(?:GV|GH)-)?CW-03001(YL|PK|BL)', sku)
    if bird:
        return 'OG-03001' + bird[1]
    plan = re.fullmatch(r'(?:(?:CW|CH|CS|GW|GH)-)?PB-XT0100([12])', sku)
    if plan:
        return 'PB-0200' + plan[1]
    return {'CH-PB-XT0102':'PB-02002','GX-01006':'GX-05002'}.get(sku, sku)


def number(value, label, integer=False):
    try:
        result = Decimal(text(value))
        if not result.is_finite() or result < 0 or result > Decimal('999999999999'):
            raise ValueError()
        if integer and (result != result.to_integral_value() or result < 1 or result > 999999999):
            raise ValueError()
        if not integer and result * 100 != (result * 100).to_integral_value():
            raise ValueError()
        return int(result if integer else result * 100)
    except (InvalidOperation, ValueError):
        raise ValueError(f'{label}須為有效的' + ('正整數' if integer else '非負金額（最多兩位小數）')) from None


def instant(value):
    try:
        dt = datetime.fromisoformat(text(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        raise ValueError('訂單日期格式不正確') from None


def clean_rows(raw_rows, catalog, *, legacy=True):
    if not raw_rows or not REQUIRED.issubset(raw_rows[0]['data']):
        raise ValueError('Excel 缺少必要訂單欄位：' + '、'.join(sorted(REQUIRED-set(raw_rows[0]['data'] if raw_rows else {}))))
    groups, exclusions, errors, warnings = {}, [], [], []
    for row in raw_rows:
        oid = text(row['data'].get('訂單號碼'))
        if not oid:
            errors.append(dict(row_index=row['row_index'],order_id='',message='缺少訂單號碼'))
        else:
            groups.setdefault(oid, []).append(row)
    by_sku, by_code = {}, {}
    for p in catalog:
        by_sku.setdefault(p['sku'], p)
        by_code.setdefault(p['product_code'], p)
    def lookup(sku):
        return by_sku.get(sku) or (by_code.get(product_code(sku)) if legacy else None)
    def exclude(row, reason, component=None):
        exclusions.append(dict(row_index=row['row_index'],order_id=text(row['data'].get('訂單號碼')),
                               original_sku=text(row['data'].get('商品貨號')),component_index=component,reason=reason))
    orders, candidates = [], []
    for oid, rows in groups.items():
        def unique(field, convert=text, required=True):
            values = {convert(r['data'].get(field)) for r in rows if text(r['data'].get(field))}
            if len(values) > 1:
                raise ValueError(f'同單的{field}不一致')
            if not values and required:
                raise ValueError(f'缺少{field}')
            return next(iter(values), '')
        try:
            status = unique('訂單狀態')
            if status == '已取消':
                for row in rows:
                    exclude(row, '已取消')
                total = unique('訂單合計', lambda v:number(v,'訂單合計'), required=False)
                orders.append(dict(order_id=oid,status='cancelled',lines=[],row_indices=[r['row_index'] for r in rows],
                    ordered_at=unique('訂單日期',instant),order_total_cents=total if total!='' else None,
                    total=amount(total) if total!='' else None,utm_medium=unique('UTM 媒介',required=False),
                    internal=False,revision=1))
                continue
            total = unique('訂單合計', lambda v:number(v,'訂單合計'))
            ordered = unique('訂單日期', instant)
            utm = unique('UTM 媒介', required=False)
            customer = unique('顧客', required=False)
            internal = legacy and (not customer or any(k.lower() in customer.lower() for k in INTERNAL))
            if legacy and not customer:
                warnings.append(f'訂單 {oid} 缺顧客名稱，依舊查詢預設排除；可勾選包含內部訂單查看')
            order = dict(order_id=oid,ordered_at=ordered,status='active',order_total_cents=total,total=amount(total),
                         utm_medium=utm,internal=internal,lines=[],row_indices=[r['row_index'] for r in rows],revision=1)
            orders.append(order)
            for row in rows:
                raw = row['data']; sku = text(raw.get('商品貨號'))
                if not sku:
                    exclude(row, '無商品貨號'); continue
                try:
                    qty = number(raw.get('數量'),'數量', True)
                    price = number(raw.get('商品結帳價'),'商品結帳價')
                    pieces = [s.strip() for s in sku.split('+') if s.strip()] if legacy else [sku]
                    if not pieces:
                        raise ValueError('商品貨號拆分後為空')
                    for component, part in enumerate(pieces, 1):
                        normalized, mult = normalize(part) if legacy else (part, 1)
                        if not 0 < qty * mult <= 999999999:
                            raise ValueError('拆分數量超出範圍')
                        normalized = manual_fix(normalized) if legacy else normalized
                        name = text(raw.get('商品名稱'))
                        early = legacy and ordered < '2021-08-31T16:00:00+00:00'
                        if early:
                            for old, new, keywords in EARLY:
                                if normalized == old and any(word in name for word in keywords):
                                    normalized = new
                        candidates.append(dict(order=order,row=row,sku=normalized,name=name,early=early,
                            quantity=qty*mult,original_quantity=qty,original_unit_price_cents=price,
                            original_sku=sku,component_index=component,split_count=len(pieces)))
                except ValueError as exc:
                    errors.append(dict(row_index=row['row_index'],order_id=oid,message=str(exc)))
        except ValueError as exc:
            errors.append(dict(row_index=rows[0]['row_index'],order_id=oid,message=str(exc)))
    name_skus = {}
    for line in sorted(candidates, key=lambda c:c['order']['ordered_at'], reverse=True):
        if lookup(line['sku']):
            name_skus.setdefault(line['name'],line['sku'])
    for c in candidates:
        sku = c['sku']; correction = ''
        if legacy and not lookup(sku):
            replacement = name_skus.get(c['name'])
            if not replacement and c['name']:
                replacement = next((s for n,s in name_skus.items() if c['name'][:15] in n), None)
            if replacement:
                sku = replacement; correction = '依舊規則名稱反查'
        p = lookup(sku)
        if not p or not p['category']:
            exclude(c['row'], '無法匹配商品表', c['component_index']); continue
        if legacy and '刪除' in p['category']:
            exclude(c['row'], '商品品類含刪除', c['component_index']); continue
        name = c['name'] if c['early'] else p['name'] or c['name']
        c['order']['lines'].append(dict(product_id='ka-'+hashlib.sha256(sku.encode()).hexdigest()[:24],
            sku=sku,name=name,category=p['category'],product_code=product_code(sku) if legacy else sku,quantity=c['quantity'],
            original_sku=c['original_sku'],row_index=c['row']['row_index'],component_index=c['component_index'],
            original_quantity=c['original_quantity'],original_unit_price_cents=c['original_unit_price_cents'],
            split_count=c['split_count'],correction=correction,amount=None,amount_cents=None,amount_basis='order_total_only'))
    for order in orders:
        if order['status']=='active' and not order['lines']:
            order['status']='excluded'
    visible = [o for o in orders if o['status']=='active' and o['lines']]
    summary = dict(raw_row_count=len(raw_rows),clean_line_count=sum(len(o['lines']) for o in visible),
                   order_count=len(visible),product_count=len({l['product_id'] for o in visible for l in o['lines']}),
                   excluded_count=len(exclusions),order_total=amount(sum(o['order_total_cents'] for o in visible)))
    return dict(orders=orders,exclusions=exclusions,errors=errors,warnings=warnings,summary=summary)


def clean_order_file(rows):
    """Use exact SKUs and descriptions in the order file, without merchant-specific rules."""
    products, errors = {}, []
    cancelled = {text(r['data'].get('訂單號碼')) for r in rows if text(r['data'].get('訂單狀態')) == '已取消'}
    for row in rows:
        d = row['data']
        if text(d.get('訂單號碼')) in cancelled:
            continue
        sku, name = text(d.get('商品貨號')), text(d.get('商品名稱'))
        category = text(d.get('商品類型')) or '未分類'
        message = ''
        if not sku or not name:
            message = '商品貨號（SKU）與商品名稱不可空白'
        elif sku in products and (products[sku]['name'], products[sku]['category']) != (name, category):
            message = f'同一 SKU {sku} 的商品名稱或商品類型不一致，請確認來源列'
        else:
            products[sku] = dict(sku=sku,name=name,category=category,product_code=sku)
        if message:
            errors.append(dict(row_index=row['row_index'],order_id=text(d.get('訂單號碼')),message=message))
    catalog = sorted(products.values(), key=lambda p:p['sku'])
    result = clean_rows(rows, catalog, legacy=False)
    result['errors'] = errors + result['errors']
    result['product_source'] = 'order_file'
    return result, fingerprint(catalog)


def initialize(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS kol_import_batches(
            id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
            filename TEXT NOT NULL, file_hash TEXT NOT NULL, source_blob BLOB NOT NULL,
            created_at TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('preview','confirmed')),
            rules_version TEXT NOT NULL, catalog_hash TEXT NOT NULL, result TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS kol_import_hash ON kol_import_batches(account_id,file_hash,rules_version,catalog_hash);
        CREATE TABLE IF NOT EXISTS kol_analysis_products(
            id TEXT PRIMARY KEY, sku TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            category TEXT NOT NULL, product_code TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS kol_analysis_orders(
            account_id TEXT NOT NULL REFERENCES accounts(id), order_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, batch_id TEXT NOT NULL REFERENCES kol_import_batches(id),
            normalized TEXT NOT NULL, PRIMARY KEY(account_id,order_id));
        CREATE TABLE IF NOT EXISTS kol_upload_receipts(
            upload_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
            batch_id TEXT NOT NULL REFERENCES kol_import_batches(id));
    ''')


def order_fingerprint(order):
    # File names and row offsets are provenance, not business identity.
    normalized = {k:v for k,v in order.items() if k not in ('row_indices','revision')}
    normalized['lines'] = sorted(
        [{k:v for k,v in line.items() if k not in ('row_index','component_index')} for line in order['lines']],
        key=lambda line:json.dumps(line, sort_keys=True, ensure_ascii=False))
    return fingerprint(normalized)


class KolImport:
    def __init__(self, store, catalog_path=None):
        self.store = store
        self.catalog_path = Path(catalog_path) if catalog_path else None

    def preview(self, body):
        fields = {'account_id','filename','content_base64'}
        if not isinstance(body, dict) or not fields.issubset(body) or set(body)-fields-{'upload_id'}:
            raise ValueError('上傳欄位須為 account_id、filename、content_base64，可另帶 upload_id')
        upload_id = self._upload_id(body['upload_id']) if 'upload_id' in body else None
        account = required_text(body['account_id'], '訂單來源', 100)
        filename = required_text(body['filename'], '檔名', 255)
        encoded = body['content_base64']
        if not isinstance(encoded, str) or len(encoded) > 2_666_668:
            raise ValueError('檔案須為 2 MB 以內')
        try:
            blob = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError('檔案 base64 編碼不正確') from None
        if not 0 < len(blob) <= MAX_BYTES:
            raise ValueError('Excel 檔案須為 2 MB 以內')
        file_hash = hashlib.sha256(blob).hexdigest()
        request_hash = fingerprint(dict(account_id=account,filename=filename,file_hash=file_hash))
        with self.store.connect() as db:
            existing = self._receipt_batch(db, upload_id, request_hash)
            if existing:
                return self._detail(db, existing)
            if not db.execute('SELECT 1 FROM accounts WHERE id=?',(account,)).fetchone():
                raise ValueError('未知訂單來源，請先建立來源')
        rows = read_xlsx(blob, filename)
        rules_version = RULES_VERSION if self.catalog_path else SINGLE_FILE_VERSION
        if self.catalog_path:
            catalog, catalog_hash = load_catalog(self.catalog_path)
            result = clean_rows(rows, catalog)
        else:
            result, catalog_hash = clean_order_file(rows)
        result['raw_rows'] = rows
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Recheck after taking the write lock: concurrent retries must be immutable.
            existing = self._receipt_batch(db, upload_id, request_hash)
            if existing:
                return self._detail(db, existing)
            previous = db.execute('SELECT id FROM kol_import_batches WHERE account_id=? AND file_hash=? AND rules_version=? AND catalog_hash=?',
                                  (account,file_hash,rules_version,catalog_hash)).fetchone()
            result['baseline'] = self._baseline(db, account, result['orders'])
            result['changes'] = self._changes(db, account, result['orders'])
            if previous:
                db.execute("UPDATE kol_import_batches SET result=? WHERE id=? AND state='preview'",(encode(result),previous['id']))
                if upload_id:
                    db.execute('INSERT INTO kol_upload_receipts VALUES(?,?,?)',(upload_id,request_hash,previous['id']))
                return self._detail(db, previous['id'])
            bid = uuid4().hex
            db.execute('INSERT INTO kol_import_batches VALUES(?,?,?,?,?,?,?,?,?,?)',
                (bid,account,filename,file_hash,blob,now(),'preview',rules_version,catalog_hash,encode(result)))
            if upload_id:
                db.execute('INSERT INTO kol_upload_receipts VALUES(?,?,?)',(upload_id,request_hash,bid))
            self.store.event(db,'kol_import_preview',bid,dict(file_hash=file_hash,summary=result['summary']))
            return self._detail(db, bid)

    @staticmethod
    def _upload_id(value):
        if not isinstance(value,str):
            raise ValueError('上傳識別碼須為 UUID')
        try:
            normalized = str(UUID(value))
        except ValueError:
            raise ValueError('上傳識別碼須為 UUID') from None
        if normalized != value:
            raise ValueError('上傳識別碼須為標準小寫 UUID')
        return normalized

    @staticmethod
    def _receipt_batch(db, upload_id, request_hash):
        if upload_id is None:
            return None
        row = db.execute('SELECT * FROM kol_upload_receipts WHERE upload_id=?',(upload_id,)).fetchone()
        if row and row['request_hash'] != request_hash:
            raise ConflictError('這個上傳識別碼已對應其他內容，請查詢原批次或開始新上傳')
        return row['batch_id'] if row else None

    def upload_status(self, upload_id):
        upload_id = self._upload_id(upload_id)
        with self.store.connect() as db:
            row = db.execute('''SELECT r.batch_id,b.state FROM kol_upload_receipts r
                JOIN kol_import_batches b ON b.id=r.batch_id WHERE r.upload_id=?''',(upload_id,)).fetchone()
            return dict(state='saved',batch_id=row['batch_id'],batch_state=row['state']) if row else dict(state='not_found')

    def _detail(self, db, bid):
        row = db.execute('SELECT * FROM kol_import_batches WHERE id=?',(bid,)).fetchone()
        if not row:
            raise KeyError(bid)
        batch = {key:row[key] for key in row.keys() if key not in ('source_blob','result')}
        result = json.loads(row['result'])
        result.pop('raw_rows', None)
        baseline = result.pop('baseline', {})
        batch.update(result)
        if batch['state']=='preview' and baseline != self._baseline(db, batch['account_id'], batch['orders']):
            batch['errors'].append(dict(row_index=None,order_id='',message='訂單在預覽後已更新，請重新上傳預覽'))
        tags = {tag for kol in db.execute('SELECT utms FROM kols') for tag in json.loads(kol['utms'])}
        active = [o for o in batch['orders'] if o['status']=='active' and o['lines'] and not o['internal']]
        unknown = Counter(o['utm_medium'] for o in active if o['utm_medium'] and o['utm_medium'] not in tags)
        batch['unmatched_utms'] = [dict(utm_medium=tag,order_count=count) for tag,count in sorted(unknown.items())]
        batch['no_utm_count'] = sum(not o['utm_medium'] for o in active)
        batch['can_confirm'] = batch['state']=='preview' and not batch['errors']
        return batch

    def detail(self, bid):
        with self.store.connect() as db:
            return self._detail(db, bid)

    def list(self):
        with self.store.connect() as db:
            rows = db.execute('SELECT id,account_id,filename,state,created_at,result FROM kol_import_batches ORDER BY rowid DESC')
            return [dict(id=r['id'],account_id=r['account_id'],filename=r['filename'],state=r['state'],
                         created_at=r['created_at'],summary=json.loads(r['result'])['summary']) for r in rows]

    @staticmethod
    def _baseline(db, account, orders):
        return {o['order_id']: next((r[0] for r in db.execute(
            'SELECT fingerprint FROM kol_analysis_orders WHERE account_id=? AND order_id=?',
            (account,o['order_id']))),None) for o in orders}

    @staticmethod
    def _changes(db, account, orders):
        changes = dict(new=0,updated=0,unchanged=0,excluded=0)
        for order in orders:
            old = db.execute('SELECT fingerprint FROM kol_analysis_orders WHERE account_id=? AND order_id=?',
                             (account,order['order_id'])).fetchone()
            changes['new' if old is None else 'unchanged' if old[0]==order_fingerprint(order) else 'updated'] += 1
            if order['status']!='active':
                changes['excluded'] += 1
        return changes

    def confirm(self, body):
        if not isinstance(body, dict) or set(body)!={'batch_id'}:
            raise ValueError('確認僅接受 batch_id')
        bid = required_text(body['batch_id'],'批次識別',100)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM kol_import_batches WHERE id=?',(bid,)).fetchone()
            if row is None:
                raise KeyError(bid)
            if row['state']=='confirmed':
                return self._detail(db,bid)
            result = json.loads(row['result'])
            if result['errors']:
                raise ValueError('預覽仍有錯誤，請修正檔案後重新上傳')
            if row['rules_version'] == SINGLE_FILE_VERSION:
                _, current_hash = clean_order_file(read_xlsx(row['source_blob'], row['filename']))
            elif row['rules_version'] == RULES_VERSION:
                legacy_path = self.catalog_path or Path(__file__).resolve().parents[1]/'.local'/'kol-products.xlsx'
                _, current_hash = load_catalog(legacy_path)
            else:
                raise ConflictError('清洗規則已變更，請重新上傳預覽')
            if current_hash!=row['catalog_hash']:
                raise ConflictError('商品對照表或清洗規則已變更，請重新上傳預覽')
            account = row['account_id']
            if result['baseline']!=self._baseline(db,account,result['orders']):
                raise ConflictError('訂單在預覽後已更新，請重新上傳預覽')
            for order in result['orders']:
                signature = order_fingerprint(order)
                previous = db.execute('SELECT fingerprint,normalized FROM kol_analysis_orders WHERE account_id=? AND order_id=?',
                                      (account,order['order_id'])).fetchone()
                if previous and previous['fingerprint']==signature:
                    continue
                order['revision'] = json.loads(previous['normalized'])['revision']+1 if previous else 1
                for line in order['lines']:
                    db.execute('''INSERT INTO kol_analysis_products VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,category=excluded.category,product_code=excluded.product_code''',
                        (line['product_id'],line['sku'],line['name'],line['category'],line['product_code']))
                db.execute('''INSERT INTO kol_analysis_orders VALUES(?,?,?,?,?) ON CONFLICT(account_id,order_id) DO UPDATE SET
                    fingerprint=excluded.fingerprint,batch_id=excluded.batch_id,normalized=excluded.normalized''',
                    (account,order['order_id'],signature,bid,encode(order)))
            db.execute("UPDATE kol_import_batches SET state='confirmed' WHERE id=?",(bid,))
            self.store.event(db,'kol_import_confirm',bid,dict(changes=result['changes'],summary=result['summary']))
            return self._detail(db,bid)
