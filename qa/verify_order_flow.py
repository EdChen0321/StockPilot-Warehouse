"""Isolated known-answer acceptance; never seed the user's working database."""
import argparse
import hashlib
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ops.ai_chat import ChatService, calendar_context
from ops.ai_queries import query_orders
from ops.codex_runtime import CodexRuntime
from ops.orders import Orders
from ops.server import create_server
from qa.ai_evaluation_cases import build_fixture
from qa.run_ai_evaluation import assess, digest


def fixture(path, calendar):
    data=build_fixture(path,calendar)
    store=data['store']
    with store.connect() as db:
        original=json.loads(db.execute("SELECT normalized FROM order_versions WHERE order_id='ORD-101'").fetchone()[0])
        for identifier,state,shipped in [('PARTIAL','active',1),('COMPLETE','active',2),('CANCELLED','cancelled',0)]:
            order={**original,'order_id':identifier,'status':state,'lines':[{**original['lines'][0],'quantity':2}]}
            db.execute('INSERT INTO order_versions VALUES(?,?,?,?,?,?)',('demo-sl',identifier,1,identifier,json.dumps(order),'demo-sl'))
            db.execute('INSERT INTO current_orders VALUES(?,?,?)',('demo-sl',identifier,1))
            if shipped:
                db.execute('INSERT INTO fulfillment_groups VALUES(?,?,?,?,?)',(identifier,'demo-sl',identifier,1,'{}'))
                db.execute('INSERT INTO shipments(id,group_id,number,state,lines,created_at) VALUES(?,?,?,?,?,?)',
                           (identifier,identifier,identifier,'shipped',json.dumps([dict(product_id=data['cup']['id'],quantity=shipped)]),calendar['today']))
    return data


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
    calendar=calendar_context();checks=[];records=[]
    def check(name,passed,detail):
        checks.append(dict(name=name,passed=passed,detail=detail))
    runtime_dir=ROOT/'qa/ai-mvp-20261102/ai'
    settings=ChatService('unused',runtime_dir).settings()
    settings_before=(runtime_dir/'settings.json').read_bytes()
    with tempfile.TemporaryDirectory(prefix='order-acceptance-') as temporary:
        path=Path(temporary)/'orders.sqlite3';data=fixture(path,calendar)
        before=digest(path)
        server=create_server(path,0,ai_directory=Path(temporary)/'ai',initialize_database=False)
        check('聊天室與營運頁使用同一資料庫',server.chat.database.resolve()==server.store.path.resolve() and server.chat.order_links_enabled,'以同一個 server 實例核對來源與單據入口；不執行遷移。')
        server.server_close()
        filters=dict(view='unshipped',status='active',**calendar['yesterday'])
        result=query_orders(path,filters);rows=result['orders'];ids=[r['order_id'] for r in rows]
        check('昨天下單日期與取消／全部出貨排除',ids==['ORD-101','PARTIAL'],f'預期 ORD-101、PARTIAL；實際 {ids}。今天與上月訂單亦排除。')
        partial=next(r for r in rows if r['order_id']=='PARTIAL')
        check('部分出貨狀態與未出件數',partial['remaining_quantity']==1 and partial['fulfillment_status']=='部分出貨' and partial['items'][0]['shipped_quantity']==1,'PARTIAL 需求 2、已出 1、未出 1。')
        documents={(o['account_id'],o['order_id']) for o in Orders(data['store']).list()}
        check('真實編號能找到原單據',all((r['account_id'],r['order_id']) in documents for r in rows),'同來源代碼＋訂單編號核對營運 Orders.list；畫面點擊另列待驗。')
        pages=[query_orders(path,dict(filters,offset=i,limit=1)) for i in range(3)]
        check('分頁與空頁',[[r['order_id'] for r in p['orders']] for p in pages]==[['ORD-101'],['PARTIAL'],[]] and pages[0]['truncated'] and not pages[1]['truncated'],'每頁 1 筆，第二頁無重複，第三頁為空。')
        check('查詢與服務啟動零業務寫入',digest(path)==before,'對照 SQLite 邏輯內容雜湊。')
        if args.live:
            questions=[('L01','昨天有哪些訂單還沒出貨？',{},['ORD-101','PARTIAL']),
                       ('L02','幫我找昨天成立、到現在還有商品沒寄出去的訂單，每頁只看一筆。',{'limit':1,'offset':0},['ORD-101']),
                       ('L03','下一頁',{'limit':1,'offset':1},['PARTIAL'])]
            history=[]
            for identifier,question,paging,expected_ids in questions:
                service=ChatService(path,Path(temporary)/identifier);service.runtime=CodexRuntime(runtime_dir)
                service.settings=lambda:dict(settings);service.order_links_enabled=True
                expected=dict(tool='orders',unshipped_order_count=2,order_ids=expected_ids,**filters,**paging)
                started=time.perf_counter();response={};error=None
                try:response=service.chat(dict(question=question,history=history if identifier=='L03' else []))
                except Exception as exc:error=str(exc)
                elapsed=round((time.perf_counter()-started)*1000,3)
                assessed,reasons=assess(dict(expected=expected),response,error)
                answer=response.get('answer','')
                assessed['answer']=all(i in answer for i in expected_ids) and assessed['evidence']
                if not assessed['answer']:reasons.append('缺少預期編號或查詢結果不符')
                records.append(dict(case_id=identifier,category='訂單',round=1,variant='fast',question=question,
                    expected=expected,checks=assessed,reasons=reasons,execution_success=error is None,error=error,
                    elapsed_ms=elapsed,answer=answer,calls=response.get('execution',{}).get('tool_calls',[]),
                    timings=response.get('timings',{}),model_calls=response.get('execution',{}).get('model_call_count',0),
                    tool_calls=response.get('execution',{}).get('tool_call_count',0),business_data_unchanged=digest(path)==before))
                check('實際提問 '+identifier,assessed['answer'] and error is None,f'{question} 耗時 {elapsed/1000:.2f} 秒；'+('符合預期清單' if assessed['answer'] else '；'.join(reasons)))
                history=[dict(role='user',text=question),dict(role='assistant',text=answer)]
                print(identifier,assessed,elapsed,flush=True)
        # More than a page, using only this isolated fixture.
        with data['store'].connect() as db:
            order=json.loads(db.execute("SELECT normalized FROM order_versions WHERE order_id='ORD-101'").fetchone()[0])
            for n in range(51):
                identifier=f'PAGE-{n:03}';cloned={**order,'order_id':identifier}
                db.execute('INSERT INTO order_versions VALUES(?,?,?,?,?,?)',('demo-sl',identifier,1,identifier,json.dumps(cloned),'demo-sl'))
                db.execute('INSERT INTO current_orders VALUES(?,?,?)',('demo-sl',identifier,1))
        first=query_orders(path,filters);second=query_orders(path,dict(filters,offset=50))
        check('超過 50 筆不漏列不重複',len(first['orders'])==50 and len(second['orders'])==3 and len({r['order_id'] for r in first['orders']+second['orders']})==53,'53 筆分成 50＋3；每次查詢固定按下單時間、來源、編號排序。資料異動時重新查詢，不保證跨頁快照。')
    check('既有 AI 設定未變更',(runtime_dir/'settings.json').read_bytes()==settings_before,'模型、指示與登入設定不覆寫。')
    check('真實瀏覽器查看單據',None,'Browser access host unavailable；目前只有 API／DOM 驗證，尚未完成實際點擊。')
    check('使用者以自己的問法驗收',None,'待 Ed 回來提問；系統問法通過不代替使用者驗收。')
    run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-order-flow'
    run=dict(id=run_id,schema_version=1,scorer_version='order-flow-v1',started_at=datetime.now(timezone.utc).isoformat(),
             status='completed',model=settings['model'],effort=settings['effort'],calendar=calendar,
             dataset='隔離訂單流程：取消／部分出貨／全部出貨／53筆分頁',records=records,acceptance_checks=checks,expected_attempts=len(records))
    target=ROOT/'qa/ai-evaluation/runs'/f'{run_id}.json';target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(run,ensure_ascii=False,indent=2),encoding='utf-8')
    print(target,flush=True)
    print(json.dumps(checks,ensure_ascii=True),flush=True)
    if any(c['passed'] is False for c in checks):sys.exit(1)

if __name__=='__main__':main()
