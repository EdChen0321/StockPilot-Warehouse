"""Local evaluation artifacts. Execution, evidence and answer reviews stay separate."""
import json
import math
import re
from pathlib import Path

SCHEMA_VERSION = 1
SCORER_VERSION = 'evidence-v1'
ANSWER_SCORER_VERSION = 'record-identity-v1'
RUN_DIRECTORY = Path(__file__).resolve().parents[1] / 'qa/ai-evaluation/runs'


def summary(records):
    durations = sorted(r['elapsed_ms'] for r in records if r.get('elapsed_ms') is not None)
    def score(key):
        known = [r['checks'][key] for r in records if r.get('checks', {}).get(key) is not None]
        return dict(passed=sum(v is True for v in known), assessed=len(known),
                    rate=100*sum(v is True for v in known)/len(known) if known else None)
    return dict(attempts=len(records), execution_success=sum(r['execution_success'] for r in records),
                evidence=score('evidence'), route=score('route'), answer=score('answer'),
                mean_ms=sum(durations)/len(durations) if durations else None,
                p50_ms=durations[math.ceil(len(durations)*.5)-1] if durations else None,
                p95_ms=durations[math.ceil(len(durations)*.95)-1] if durations else None,
                latency_samples=len(durations), errors=sum(not r['execution_success'] for r in records),
                model_calls=sum(r.get('model_calls', 0) for r in records),
                tool_calls=sum(r.get('tool_calls', 0) for r in records), tokens=None, cost=None)


def assess_record_identity(row):
    """Recomputable answer check: requested records must be findable by their ID.

    This does not evaluate prose quality or infer correctness from tool success.
    Raw execution artifacts stay unchanged.
    """
    row = {**row, 'checks':dict(row.get('checks', {})), 'reasons':list(row.get('reasons', []))}
    expected = row.get('expected', {})
    required_ids = expected.get('order_ids')
    if expected.get('view') == 'units' and row['checks'].get('evidence') is True:
        required_ids = [unit['id'] for call in row.get('calls', [])
                        if call['tool'] == 'inventory' and call['parameters'].get('view') == 'units'
                        for unit in call['result'].get('rows', [])]
    if required_ids and row['checks'].get('evidence') is True:
        missing = [identifier for identifier in required_ids if identifier not in (row.get('answer') or '')]
        row['checks']['answer'] = not missing and row['checks'].get('answer') is not False
        row['answer_check_scope'] = '清單的實際訂單編號／逐件品號；不是完整文字品質評分'
        if missing: row['reasons'].append('回答缺少可尋找的實際編號：'+'、'.join(missing))
    row['full_answer_review'] = None
    return row


def report(run):
    records = [assess_record_identity(r) for r in run.get('records', [])]
    run = dict(run)
    run['records'] = records
    run['answer_scorer_version'] = ANSWER_SCORER_VERSION
    run['summaries'] = {variant: summary([r for r in records if r['variant'] == variant])
                        for variant in sorted({r['variant'] for r in records})}
    run['by_category'] = {category: {variant: summary([r for r in records
        if r['variant'] == variant and r['category'] == category]) for variant in run['summaries']}
        for category in ('營運', '庫存', '訂單')}
    pairs = {}
    for row in records:
        pairs.setdefault((row['case_id'], row['round']), {})[row['variant']] = row
    deltas = [p['fast']['elapsed_ms']-p['model']['elapsed_ms'] for p in pairs.values()
              if set(p) >= {'model', 'fast'} and all(p[v]['execution_success'] for v in ('model', 'fast'))]
    run['comparison'] = dict(paired_samples=len(deltas),
        mean_delta_ms=sum(deltas)/len(deltas) if deltas else None,
        direction='fast - model', excluded_pairs=len(pairs)-len(deltas))
    return run


def list_runs(directory=RUN_DIRECTORY):
    result = []
    if not directory.exists():
        return result
    for path in sorted(directory.glob('*.json'), reverse=True):
        try:
            run = json.loads(path.read_text(encoding='utf-8'))
            result.append({k:run.get(k) for k in ('id', 'started_at', 'status', 'model', 'dataset', 'expected_attempts')}
                          | {'completed_attempts':len(run.get('records', []))})
        except (ValueError, OSError):
            continue
    return result


def read_run(run_id, directory=RUN_DIRECTORY):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', run_id):
        raise ValueError('評測編號不正確')
    path = directory / (run_id + '.json')
    if not path.is_file():
        raise KeyError(run_id)
    return report(json.loads(path.read_text(encoding='utf-8')))
