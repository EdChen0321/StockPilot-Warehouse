"""Paired real-runtime evaluation on disposable synthetic business databases.

Run: python qa/run_ai_evaluation.py --variants model fast --repeats 1
No MLflow dependency, no production settings or business database mutation.
"""
import argparse
import hashlib
import json
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ops.ai_chat import ChatService, calendar_context
from ops.ai_queries import ReadOnlyStore
from ops.codex_runtime import CodexRuntime
from ops.ai_evaluation import RUN_DIRECTORY, SCORER_VERSION, SCHEMA_VERSION
from qa.ai_evaluation_cases import build_fixture, add_stock, cases


def digest(path):
    with ReadOnlyStore(path).connect() as db:
        return hashlib.sha256('\n'.join(db.iterdump()).encode()).hexdigest()


def assess(case, result, error):
    expected = case['expected']
    reasons = []
    execution = result.get('execution', {})
    calls = execution.get('tool_calls', [])
    action = 'query' if calls else None
    for request in execution.get('model_requests', []):
        if request['stage'] == 'planning':
            try:
                action = json.loads(request['output']).get('action')
            except (ValueError, KeyError):
                pass
    if expected.get('blocked'):
        passed = bool(error and '已停用' in error) or (not calls and action in ('clarify', 'reject'))
        return dict(evidence=passed, route=passed, answer=None), ([] if passed else ['停用工具未明確拒絕或澄清'])
    if error:
        return dict(evidence=False, route=False, answer=None), ['執行錯誤，未取得可驗證答案']
    if 'action' in expected:
        passed = action == expected['action'] and not calls
        return dict(evidence=passed, route=passed, answer=None), ([] if passed else [f'預期 {expected["action"]} 且不查資料，實際 {action}'])
    required = expected.get('tools', [expected.get('tool')])
    for tool in required:
        if not any(c['tool'] == tool for c in calls):
            reasons.append('缺少必要查詢：'+str(tool))
    filter_keys = {'start','end','status','account_id','view','search','offset','limit'}
    if any(k in expected for k in filter_keys):
        matched = [c for c in calls if c['tool'] == expected.get('tool') and all(
            c['parameters'].get(k, {'offset':0,'limit':50}.get(k, '')) == v
            for k,v in expected.items() if k in filter_keys)]
        if not matched:
            reasons.append('查詢條件與預期不符')
    else:
        matched = calls
    for key, value in expected.items():
        if key in filter_keys | {'tool','tools','action','blocked','period_counts'}:
            continue
        if key == 'order_ids':
            actual = sorted(o['order_id'] for c in matched for o in c['result'].get('orders', []))
            if actual != sorted(value): reasons.append('訂單清單不符')
        elif not any(c['result'].get(key, c['result'].get('totals', {}).get(key, '__missing__')) == value for c in matched):
            reasons.append('證據欄位不符：'+key)
    for start,end,count in expected.get('period_counts', []):
        if not any(c['tool']=='orders' and c['parameters'].get('start')==start and c['parameters'].get('end')==end
                   and c['parameters'].get('status')=='active' and c['result'].get('order_count')==count for c in calls):
            reasons.append('比較期間或筆數不符：'+start+'～'+end)
    answer_check = None
    # Only narrow numeric statements are automatically assessed; lists/advice need review.
    answer = re.sub(r'[*#,]', '', result.get('answer',''))
    if result.get('mode') == 'fixed-query' and 'on_hand' in expected:
        answer_check = bool(re.search(r'在庫\s*'+str(expected['on_hand'])+r'\s*件', answer))
    elif result.get('mode') == 'fixed-query' and 'unshipped_order_count' in expected:
        answer_check = bool(re.search(r'(?:有|共)\s*'+str(expected['unshipped_order_count'])+r'\s*筆', answer))
    if answer_check is False: reasons.append('固定回答的數字未符合預期')
    return dict(evidence=not reasons, route=not reasons, answer=answer_check), reasons


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--variants', nargs='+', choices=['model','fast'], default=['model','fast'])
    parser.add_argument('--repeats', type=int, choices=range(1,11), default=1)
    parser.add_argument('--cases', nargs='*')
    args = parser.parse_args()
    runtime_dir = ROOT / 'qa/ai-mvp-20261102/ai'
    saved = (runtime_dir / 'settings.json').read_bytes()
    settings = ChatService('unused', runtime_dir).settings()
    calendar = calendar_context()
    selected = [c for c in cases(calendar) if not args.cases or c['id'] in args.cases]
    if not selected: raise ValueError('沒有符合的案例')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:6]
    RUN_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = RUN_DIRECTORY / (run_id+'.json')
    sources = ['ops/ai_chat.py','ops/ai_fast.py','ops/ai_queries.py','ops/ai_inventory.py', 'qa/ai_evaluation_cases.py','qa/run_ai_evaluation.py']
    run = dict(schema_version=SCHEMA_VERSION, scorer_version=SCORER_VERSION, id=run_id,
        started_at=datetime.now(timezone.utc).isoformat(), status='running', model=settings['model'], effort=settings['effort'],
        settings_sha256=hashlib.sha256(saved).hexdigest(), calendar=calendar,
        source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources},
        dataset='operational-synthetic-v1', dataset_notes='獨立合成庫；模型使用原設定，權限案例只覆寫評測實例。不含原工作庫。',
        expected_attempts=len(selected)*len(args.variants)*args.repeats, records=[], cases=selected,
        limitation='清單與建議回答待人工覆核；證據通過不代表回答完整正確。單輪 p95 僅描述本批樣本。')
    def save():
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)
    save()
    print('RUN '+run_id, flush=True)
    try:
        for round_number in range(1,args.repeats+1):
            for index, case in enumerate(selected):
                variants = args.variants if (index+round_number)%2 else list(reversed(args.variants))
                for variant in variants:
                    with tempfile.TemporaryDirectory(prefix='erp-eval-') as temporary:
                        database = Path(temporary)/'fixture.sqlite3'
                        fixture = build_fixture(database, calendar)
                        if case.get('mutation') == 'add_stock': add_stock(fixture)
                        before = digest(database)
                        service = ChatService(database, Path(temporary)/'trace', allow_fixed_queries=variant=='fast')
                        service.runtime = CodexRuntime(runtime_dir)
                        local_settings = {**settings, 'enabled_tools':[t for t in settings['enabled_tools'] if t not in case.get('disabled',[])]}
                        service.settings = lambda: dict(local_settings)
                        started = time.perf_counter()
                        result, error = {}, None
                        try:
                            with patch('ops.ai_chat.calendar_context', return_value=calendar):
                                result = service.chat(dict(question=case['question'], history=case.get('history', [])))
                        except Exception as exc:
                            error = str(exc)
                        elapsed = round((time.perf_counter()-started)*1000, 3)
                        checks, reasons = assess(case, result, error)
                        unchanged = before == digest(database)
                        if not unchanged:
                            checks['evidence'] = False
                            reasons.append('查詢期間資料庫內容改變')
                        trace_lines = service.journal.path.read_text(encoding='utf-8').splitlines() if service.journal.path.exists() else []
                        trace = json.loads(trace_lines[-1]) if trace_lines else {}
                        row = dict(case_id=case['id'], category=case['category'], round=round_number, variant=variant,
                            question=case['question'], expected=case['expected'], history=case.get('history', []),
                            execution_success=not bool(error), error=error, elapsed_ms=elapsed,
                            model_calls=trace.get('model_calls', 0), tool_calls=len(trace.get('tools', [])),
                            timings=result.get('timings', trace.get('timings', {})), mode=result.get('mode'),
                            answer=result.get('answer'), evidence=result.get('evidence', []), checks=checks, reasons=reasons,
                            business_data_unchanged=unchanged, dataset_sha256=before,
                            calls=result.get('execution', {}).get('tool_calls', []), tokens=None, cost=None)
                        run['records'].append(row)
                        save()
                        print(f'{len(run["records"])}/{run["expected_attempts"]} {case["id"]} {variant} {elapsed:.0f}ms {checks}', flush=True)
        run['status'] = 'completed'
    finally:
        run['settings_unchanged'] = saved == (runtime_dir/'settings.json').read_bytes()
        if run['status'] == 'running': run['status'] = 'interrupted'
        run['finished_at'] = datetime.now(timezone.utc).isoformat()
        save()
    print(path, flush=True)


if __name__ == '__main__':
    main()
