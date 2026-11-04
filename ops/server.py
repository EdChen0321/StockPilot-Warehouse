"""Loopback-only demonstration adapter. Not a production server or identity system."""
import argparse
import json
import re
import sqlite3
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .sales import sales, export_csv
from .store import Store, ConflictError
from .catalogue import Catalogue
from .stock import Stock
from .locations import Locations
from .orders import Orders
from .materials import Materials
from .goods_purchases import GoodsPurchases
from .insights import sales_insights, crossbuy, product_overview, export_insights
from .assistant import ReadScope, preview as assistant_preview
from .ai_chat import ChatService
from .ai_evaluation import list_runs, read_run
from .stock_reports import report as stock_report, save_alert, export_report
from .shipping_statistics import shipping_statistics
from .kol import Kol, report as kol_report, export_csv as export_kol
from .kol_import import KolImport

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = [
    ('01-shopline.csv', '01 · SHOPLINE 初始訂單', 'demo-sl'),
    ('02-shopee.csv', '02 · 蝦皮套組訂單', 'demo-sp'),
    ('03-shopline-revision.csv', '03 · SHOPLINE 修訂版本2', 'demo-sl'),
    ('04-shopline-cancel.csv', '04 · SHOPLINE 取消版本3', 'demo-sl'),
    ('05-missing-sku.csv', '05 · 缺少SL-NEW對照', 'demo-sl'),
    ('06-incomplete.csv', '06 · 缺少明細阻擋案例', 'demo-sl'),
    ('07-conflict.csv', '07 · 版本2內容衝突（請在取消前測）', 'demo-sl'),
]


class LocalServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR can leave two servers sharing the same local port.
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class HttpError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        pass  # Do not log request bodies, imported customer fields or query values.

    def send(self, value, status=200, mime='application/json; charset=utf-8', filename=None):
        payload = json.dumps(value, ensure_ascii=False).encode('utf-8') if mime.startswith('application/json') else value
        if isinstance(payload, str):
            payload = payload.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(payload)
        if status >= 400 and self.command == 'POST' and not getattr(self, 'body_read', False):
            # Send FIN after the error response before discarding an already-arriving
            # rejected body. Bound the discard; never wait for an advertised huge body.
            self.wfile.flush()
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_WR)
                self.connection.settimeout(0.05)
                self.rfile.read1(65536)
            except OSError:
                pass

    def guard(self, write=False):
        hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        host = self.headers.get('Host', '')
        public_navigation = (not write and urlsplit(self.path).path == '/'
                             and self.headers.get('Sec-Fetch-Mode') == 'navigate'
                             and self.headers.get('Sec-Fetch-Dest') == 'document')
        if host not in hosts or (self.headers.get('Sec-Fetch-Site') == 'cross-site' and not public_navigation):
            raise HttpError(403, '只接受本機同源操作')
        origin = self.headers.get('Origin')
        if origin and origin != f'http://{host}':
            raise HttpError(403, '拒絕跨來源操作')
        if write and self.headers.get('X-Requested-With') != 'operations-ui':
            raise HttpError(403, '缺少本機操作標記')

    def read_json(self):
        if self.headers.get_content_type() != 'application/json':
            raise HttpError(415, '請使用application/json')
        if self.headers.get('Transfer-Encoding'):
            raise HttpError(400, '不接受分塊請求')
        length = self.headers.get('Content-Length', '')
        if not re.fullmatch(r'[0-9]{1,10}', length):
            raise HttpError(400, '無效Content-Length')
        if int(length) > 4_000_000:
            raise HttpError(413, '請求超過4MB')
        raw = self.rfile.read(int(length))
        self.body_read = True
        if len(raw) != int(length):
            raise HttpError(400, '請求內容不完整')
        try:
            body = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError):
            raise HttpError(400, '無效UTF-8或JSON') from None
        if not isinstance(body, dict):
            raise HttpError(400, 'JSON必須為物件')
        return body

    def dispatch(self, write=False):
        try:
            self.guard(write)
            parsed = urlsplit(self.path)
            if write:
                if parsed.query:
                    raise ValueError('寫入路徑不接受查詢參數')
                value = self.post(parsed.path, self.read_json())
                self.send(value)
            else:
                self.get(parsed.path, parsed.query)
        except HttpError as exc:
            self.send({'error': exc.message}, exc.status)
        except ConflictError as exc:
            self.send({'error': str(exc)}, 409)
        except KeyError:
            self.send({'error': '找不到指定資料'}, 404)
        except PermissionError as exc:
            self.send({'error': str(exc)}, 403)
        except (ValueError, TypeError, UnicodeError) as exc:
            self.send({'error': str(exc)}, 400)
        except sqlite3.Error:
            self.send({'error': '資料儲存未完成，請稍後以相同操作重試'}, 503)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except OSError:
            self.send({'error': '本機服務無法完成操作，請檢查連線與設定目錄。'}, 503)

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch(True)

    def get(self, path, query):
        if path == '/api/assistant/evaluations':
            if query: raise ValueError('評測列表不接受額外條件')
            return self.send({'runs':list_runs()})
        if path.startswith('/api/assistant/evaluations/'):
            if query: raise ValueError('評測查詢不接受額外條件')
            return self.send(read_run(path.removeprefix('/api/assistant/evaluations/')))
        if path == '/api/assistant/capabilities':
            return self.send(self.server.chat.capabilities())
        if path == '/api/assistant/settings':
            return self.send(self.server.chat.settings())
        upload_match = re.fullmatch(r'/api/kol-import/uploads/([^/]+)', path)
        if upload_match:
            if query:
                raise ValueError('上傳狀態查詢不接受額外條件')
            return self.send(KolImport(self.server.store,self.server.kol_catalog).upload_status(upload_match[1]))
        import_match = re.fullmatch(r'/api/kol-import/batches/([a-f0-9]{32})', path)
        if path == '/api/kol-import/batches' or import_match:
            if query:
                raise ValueError('批次查詢不接受額外條件')
            importer = KolImport(self.server.store, self.server.kol_catalog)
            return self.send(importer.detail(import_match[1]) if import_match else {'batches':importer.list()})
        if path in ('/api/kols','/api/kols/report','/api/kols/export'):
            params = parse_qs(query, keep_blank_values=True, max_num_fields=10)
            allowed = () if path == '/api/kols' else ('account_id','start','end','kol_id','platform','status','utm','product_id','include_internal')
            if path == '/api/kols/export':
                allowed += ('kind',)
            if any(k not in allowed or len(v) != 1 for k,v in params.items()):
                raise ValueError('KOL查詢條件不正確')
            if path == '/api/kols':
                return self.send({'kols':Kol(self.server.store).list()})
            kind=params.pop('kind',['statistics'])[0]
            result = kol_report(self.server.store, **{k:v[0] for k,v in params.items()})
            if path.endswith('/export'):
                return self.send(export_kol(result,kind).encode('utf-8-sig'),mime='text/csv; charset=utf-8',filename='kol-'+kind+'.csv')
            return self.send(result)
        overview = re.fullmatch(r'/api/products/([A-Za-z0-9_-]+)/overview', path)
        if overview or path in ('/api/insights','/api/insights/export','/api/crossbuy'):
            params = parse_qs(query, keep_blank_values=True, max_num_fields=4)
            allowed = ('account_id','start','end') if overview or path == '/api/crossbuy' else ('account_id','product_id','start','end')
            if any(k not in allowed or len(v)!=1 for k,v in params.items()):
                raise ValueError('分析查詢條件不正確')
            filters = {k:v[0] for k,v in params.items()}
            if overview:
                return self.send(product_overview(self.server.store,overview[1],**filters))
            if path == '/api/crossbuy':
                return self.send(crossbuy(self.server.store,**filters))
            result = sales_insights(self.server.store,**filters)
            if path.endswith('/export'):
                return self.send(export_insights(result).encode('utf-8-sig'),mime='text/csv; charset=utf-8',filename='sales-insights.csv')
            return self.send(result)
        if path == '/api/goods-purchases':
            return self.send(GoodsPurchases(self.server.store).snapshot())
        if path == '/api/materials':
            return self.send(Materials(self.server.store).snapshot())
        if path == '/api/materials/summary':
            params = parse_qs(query, keep_blank_values=True, max_num_fields=500)
            if any(k != 'plan_id' for k in params):
                raise ValueError('只接受生產計畫篩選')
            return self.send(Materials(self.server.store).summary(params.get('plan_id')))
        if path in ('/api/stock/shipping-statistics','/api/stock/shipping-statistics/export'):
            params = parse_qs(query, keep_blank_values=True, max_num_fields=6)
            if any(k not in ('start','end','as_of','product_id','channel','kind') or len(v)!=1 for k,v in params.items()):
                raise ValueError('統計查詢條件不正確')
            kind=params.pop('kind',['channels'])[0]
            if kind not in ('channels','shipping_products','shipping_daily','shipping_monthly','shipping_details'):
                raise ValueError('統計匯出類別不正確')
            result=shipping_statistics(self.server.store,**{k:v[0] for k,v in params.items()})
            if path.endswith('/export'):
                return self.send(export_report(result,kind).encode('utf-8-sig'),mime='text/csv; charset=utf-8',filename='shipping-'+kind+'.csv')
            return self.send(result)
        if path in ('/api/stock/reports','/api/stock/reports/export'):
            exporting=path.endswith('/export')
            params = parse_qs(query, keep_blank_values=True, max_num_fields=5 if exporting else 4)
            if any(k not in (('start','end','as_of','product_id','kind') if exporting else ('start','end','as_of','product_id')) or len(v) != 1 for k, v in params.items()):
                raise ValueError('報表查詢條件不正確')
            kind=params.pop('kind',['inventory'])[0]
            result=stock_report(self.server.store, **{k:v[0] for k,v in params.items()})
            if exporting:
                return self.send(export_report(result,kind).encode('utf-8-sig'),mime='text/csv; charset=utf-8',filename='stock-'+kind+'.csv')
            return self.send(result)
        if path == '/api/stock/shipments':
            params=parse_qs(query,keep_blank_values=True,max_num_fields=4)
            if any(k not in ('page','q','state','source') or len(v)!=1 for k,v in params.items()):
                raise ValueError('出貨分頁查詢條件不正確')
            values={k:v[0] for k,v in params.items()}
            values['page']=int(values.get('page','1'))
            return self.send(Stock(self.server.store).shipment_page(**values))
        if path == '/api/stock/products':
            if query:
                raise ValueError('庫存商品摘要不接受額外條件')
            return self.send({'products': Stock(self.server.store).snapshot(summary=True)['products']})
        if path == '/api/stock':
            if query not in ('', 'summary=1'):
                raise ValueError('庫存查詢只接受 summary=1')
            return self.send(Stock(self.server.store).snapshot(summary=query == 'summary=1'))
        history = re.fullmatch(r'/api/stock/history/(unit|shipment|receipt)/([A-Za-z0-9_-]+)', path)
        if history:
            if query:
                raise ValueError('歷史查詢不接受額外條件')
            return self.send(Stock(self.server.store).history(*history.groups()))
        if path == '/api/locations':
            return self.send(Locations(self.server.store).snapshot())
        if path == '/api/orders':
            params = parse_qs(query, keep_blank_values=True, max_num_fields=4)
            if any(k not in ('q','status','account_id','page') or len(v)!=1 for k,v in params.items()):
                raise ValueError('訂單查詢只接受單一搜尋、來源與狀態')
            if 'page' in params:
                values={k:v[0] for k,v in params.items()};values['page']=int(values['page'])
                return self.send(Orders(self.server.store).page(**values))
            return self.send({'orders': Orders(self.server.store).list(**{k:v[0] for k,v in params.items()})})
        if path == '/api/catalogue':
            params = parse_qs(query, keep_blank_values=True, max_num_fields=1)
            if any(k != 'q' or len(v) != 1 for k, v in params.items()):
                raise ValueError('只接受單一搜尋條件')
            return self.send(Catalogue(self.server.store).list(params.get('q', [''])[0]))
        match = re.fullmatch(r'/api/catalogue/([A-Za-z0-9_-]+)', path)
        if match:
            return self.send(Catalogue(self.server.store).detail(match[1]))
        if path == '/api/bootstrap':
            if query not in ('', 'summary=1'):
                raise ValueError('工作區查詢只接受 summary=1')
            boot = self.server.store.bootstrap(summary=query == 'summary=1')
            boot['mode'] = 'demo' if self.server.allow_demo else 'local'
            boot['samples'] = [dict(filename=name, label=label, account_id=account) for name, label, account in SAMPLES] if self.server.allow_demo else []
            return self.send(boot)
        match = re.fullmatch(r'/api/imports/([a-f0-9]{32})', path)
        if match:
            return self.send(self.server.store.batch(match[1]))
        if path in ('/api/sales', '/api/sales/export'):
            params = parse_qs(query, keep_blank_values=True, max_num_fields=10)
            if any(key not in ('account_id', 'product_id', 'start', 'end') or len(value) != 1 for key, value in params.items()):
                raise ValueError('查詢參數未知或重複')
            result = sales(self.server.store, **{key: value[0] for key, value in params.items()})
            if path.endswith('/export'):
                return self.send('\ufeff' + export_csv(result), mime='text/csv; charset=utf-8', filename='sales.csv')
            return self.send(result)
        static = {'/': ('web/index.html', 'text/html'), '/app.js': ('web/app.js', 'text/javascript'),
                  '/style.css': ('web/style.css', 'text/css'), '/format.js': ('web/format.js', 'text/javascript'),
                  '/catalogue.js': ('web/catalogue.js', 'text/javascript')}
        static['/stock.js'] = ('web/stock.js', 'text/javascript')
        static['/workspace.js'] = ('web/workspace.js', 'text/javascript')
        static['/locations.js'] = ('web/locations.js', 'text/javascript')
        static['/layout-refinements.css'] = ('web/layout-refinements.css', 'text/css')
        static['/status.js'] = ('web/status.js', 'text/javascript')
        static['/stock-extras.js'] = ('web/stock-extras.js', 'text/javascript')
        static['/materials.js'] = ('web/materials.js', 'text/javascript')
        static['/goods-purchases.js'] = ('web/goods-purchases.js', 'text/javascript')
        static['/insights.js'] = ('web/insights.js', 'text/javascript')
        static['/sales-charts.js'] = ('web/sales-charts.js', 'text/javascript')
        static['/vendor/echarts-6.1.0/echarts.min.js'] = ('web/vendor/echarts-6.1.0/echarts.min.js', 'text/javascript')
        static['/shipping-stats.js'] = ('web/shipping-stats.js', 'text/javascript')
        static['/product-analytics.js'] = ('web/product-analytics.js', 'text/javascript')
        static['/kol.js'] = ('web/kol.js', 'text/javascript')
        static['/kol-import.js'] = ('web/kol-import.js', 'text/javascript')
        static['/order-upload.js'] = ('web/order-upload.js', 'text/javascript')
        static['/operations-ui.js'] = ('web/operations-ui.js', 'text/javascript')
        static['/operations.css'] = ('web/operations.css', 'text/css')
        static['/assistant-chat.js'] = ('web/assistant-chat.js', 'text/javascript')
        static['/assistant-chat.css'] = ('web/assistant-chat.css', 'text/css')
        static['/ai-evaluation.js'] = ('web/ai-evaluation.js', 'text/javascript')
        static['/ai-evaluation.css'] = ('web/ai-evaluation.css', 'text/css')
        if path in static:
            file, mime = static[path]
            return self.send((ROOT / file).read_bytes(), mime=mime + '; charset=utf-8')
        for name, _, _ in SAMPLES:
            if path == f'/samples/{name}':
                return self.send((ROOT / 'demo' / name).read_bytes(), mime='text/csv; charset=utf-8', filename=name)
        if path == '/favicon.ico':
            return self.send(b'', status=204, mime='image/x-icon')
        raise HttpError(404, '找不到指定路徑')

    def post(self, path, body):
        if path == '/api/assistant/chat':
            return self.server.chat.chat(body)
        if path == '/api/assistant/settings':
            return self.server.chat.save(body)
        if path in ('/api/assistant/login','/api/assistant/logout','/api/assistant/status','/api/assistant/models'):
            if body: raise ValueError('此操作不接受額外參數')
            return self.server.chat.connection(path.rsplit('/',1)[1])
        if path in ('/api/kol-import/preview','/api/kol-import/confirm'):
            importer = KolImport(self.server.store, self.server.kol_catalog)
            return importer.preview(body) if path.endswith('/preview') else importer.confirm(body)
        store = self.server.store
        if path == '/api/kols':
            return Kol(store).save(body)
        if path == '/api/kols/delete':
            return Kol(store).delete(body)
        catalogue = Catalogue(store)
        stock = Stock(store)
        locations=Locations(store)
        location_routes={'/api/locations':locations.save,'/api/locations/enable':locations.enable,
            '/api/locations/assign':locations.assign,'/api/transfers':locations.transfer}
        if path in location_routes:return location_routes[path](body)
        match=re.fullmatch(r'/api/locations/([a-f0-9]{32})/delete',path)
        if match:return locations.delete(match[1],body)
        match=re.fullmatch(r'/api/transfers/([a-f0-9]{32})/(start|complete|cancel)',path)
        if match:return locations.transition(match[1],match[2],body)
        if path == '/api/assistant/preview':
            return assistant_preview(store, body, ReadScope())
        if path == '/api/goods-purchases':
            return GoodsPurchases(store).save(body)
        match = re.fullmatch(r'/api/goods-purchases/([a-f0-9]{32})/(confirm|schedule|cancel|receive)', path)
        if match:
            return GoodsPurchases(store).action(match[1], match[2], body)
        materials = Materials(store)
        material_routes = {'/api/materials/suppliers': materials.supplier, '/api/materials/items': materials.material,
            '/api/materials/boms': materials.bom, '/api/materials/plans': materials.plan,
            '/api/materials/purchases': materials.purchase, '/api/materials/movements': materials.movement}
        if path in material_routes:
            return material_routes[path](body)
        match = re.fullmatch(r'/api/materials/(plans|purchases)/([a-f0-9]{32})/(confirm|cancel|revise|receive|schedule)', path)
        if match:
            handler = materials.plan_action if match[1] == 'plans' else materials.purchase_action
            return handler(match[2], match[3], body)
        if path == '/api/stock/alerts':
            return save_alert(store, body)
        match = re.fullmatch(r'/api/stock/units/([a-f0-9]{32})/(quality|return|release-return|restock-return)', path)
        if match:
            if match[2] == 'restock-return':
                return stock.restock_return(match[1], body)
            if match[2] == 'release-return':
                return stock.release_return(match[1], body)
            return stock.quality(match[1], body) if match[2] == 'quality' else stock.receive_return(match[1], body)
        if path == '/api/receipts':
            return stock.receipt(body)
        match = re.fullmatch(r'/api/receipts/([a-f0-9]{32})/cost', path)
        if match:
            return stock.revise_receipt_cost(match[1], body)
        if path == '/api/orders':
            return Orders(store).receive(body)
        if path == '/api/orders/cancel':
            return Orders(store).cancel(body)
        if path == '/api/orders/reopen':
            return Orders(store).reopen(body)
        if path == '/api/orders/append':
            return Orders(store).append(body)
        if path == '/api/shipments':
            return stock.shipment(body)
        match = re.fullmatch(r'/api/shipments/([a-f0-9]{32})/scan', path)
        if match:
            return stock.scan(match[1], body)
        match = re.fullmatch(r'/api/shipments/([a-f0-9]{32})/items/([a-f0-9]{32})/cancel', path)
        if match:
            return stock.cancel_item(match[1], match[2], body)
        match = re.fullmatch(r'/api/shipments/([a-f0-9]{32})/lines', path)
        if match:
            return stock.update_shipment_lines(match[1], body)
        match = re.fullmatch(r'/api/receipts/([a-f0-9]{32})/(confirm|order|cancel)', path)
        if match:
            if match[2]!='confirm':
                return stock.receipt_stage(match[1],match[2],body)
            return stock.confirm_receipt(match[1], body)
        match = re.fullmatch(r'/api/shipments/([a-f0-9]{32})/(allocate|pick|deduct|ship|complete|cancel|reopen|work|cancel-staged)', path)
        if match:
            if match[2]=='cancel-staged':
                return stock.cancel_staged(match[1],body)
            if match[2]=='work':
                return stock.set_work_state(match[1],body)
            return stock.transition(match[1], match[2], body)
        if path == '/api/catalogue/products':
            return catalogue.create(body)
        if path == '/api/catalogue/attributes':
            return catalogue.define_attribute(body)
        if path == '/api/catalogue/mappings':
            return catalogue.mapping(body)
        if path == '/api/accounts':
            return catalogue.account(body)
        match = re.fullmatch(r'/api/catalogue/(products|bundles)/([A-Za-z0-9_-]+)', path)
        if match:
            return catalogue.update(match[2], body) if match[1] == 'products' else catalogue.bundle(match[2], body)
        if path == '/api/demo/seed':
            if not self.server.allow_demo:
                raise HttpError(403, '此資料庫為正常工作區，不載入示範資料')
            return store.seed()
        if path == '/api/imports':
            if 'content_base64' in body:
                if 'csv_text' in body:
                    raise ValueError('請只提供一份 Excel 或 CSV，不可同時提供兩種內容')
                return store.preview_excel(body.get('account_id'),body.get('filename'),body.get('content_base64'),body.get('complete_snapshot'))
            return store.preview(body.get('account_id'), body.get('filename'), body.get('csv_text'), body.get('complete_snapshot'))
        if path == '/api/mappings':
            return store.add_mapping(body.get('account_id'), body.get('external_sku'), body.get('product_id'))
        match = re.fullmatch(r'/api/imports/([a-f0-9]{32})/(confirm|reprocess)', path)
        if match:
            return store.confirm(match[1]) if match[2] == 'confirm' else store.reprocess(match[1])
        raise HttpError(404, '找不到指定操作')


def create_server(database, port=8816, allow_demo=False, kol_catalog=None, ai_directory=None, ai_database=None, initialize_database=True):
    if ai_database is not None and not Path(ai_database).is_file():
        raise ValueError('AI 唯讀資料庫不存在，不會建立或回退到示範資料')
    store = Store(database, initialize=initialize_database)
    server = LocalServer(('127.0.0.1', port), Handler)
    server.store = store
    server.allow_demo = allow_demo
    server.kol_catalog = kol_catalog
    server.chat = ChatService(ai_database if ai_database is not None else database, ai_directory or Path(database).parent / 'ai-settings')
    server.chat.order_links_enabled = server.chat.database.resolve() == store.path.resolve()
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description='Synthetic local operations demo; no production access')
    parser.add_argument('--database', type=Path, default=ROOT / '.local' / 'workspace.sqlite3')
    parser.add_argument('--demo', action='store_true', help='Only use with a separate demonstration database')
    parser.add_argument('--port', type=int, default=8816)
    parser.add_argument('--kol-catalog', type=Path, help='KOL Excel 商品對照表（唯讀）')
    args = parser.parse_args()
    if args.demo and args.database.resolve() == (ROOT / '.local' / 'workspace.sqlite3').resolve():
        parser.error('--demo 必須指定獨立示範資料庫，不可使用正常 workspace.sqlite3')
    server = create_server(args.database, args.port, allow_demo=args.demo, kol_catalog=args.kol_catalog)
    print(f'Local workspace: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
