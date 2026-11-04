import tempfile
import unittest
from pathlib import Path
from ops.ai_evaluation import summary, report, read_run, assess_record_identity


class EvaluationTests(unittest.TestCase):
    def row(self, **extra):
        return dict(case_id='one', round=1, variant='fast', category='庫存', elapsed_ms=100,
                    execution_success=True, checks=dict(evidence=True, route=True, answer=None), **extra)

    def test_unknown_is_not_zero_and_execution_is_not_answer_correctness(self):
        actual = summary([self.row()])
        self.assertEqual(actual['execution_success'], 1)
        self.assertIsNone(actual['answer']['rate'])
        self.assertEqual(actual['answer']['assessed'], 0)
        self.assertIsNone(actual['cost'])
        self.assertIsNone(summary([])['p95_ms'])

    def test_pairing_does_not_compare_different_cases_or_failed_runs(self):
        fast = self.row()
        model = {**fast, 'variant':'model', 'elapsed_ms':500}
        run = report(dict(records=[fast, model, {**model, 'case_id':'other'}]))
        self.assertEqual(run['comparison']['paired_samples'], 1)
        self.assertEqual(run['comparison']['mean_delta_ms'], -400)
        self.assertEqual(run['comparison']['excluded_pairs'], 1)
        run = report(dict(records=[fast, {**model, 'execution_success':False}]))
        self.assertIsNone(run['comparison']['mean_delta_ms'])

    def test_nearest_rank_and_safe_paths(self):
        self.assertEqual(summary([{**self.row(), 'elapsed_ms':v} for v in range(1,21)])['p95_ms'], 19)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                read_run('../settings', Path(folder))

    def test_correct_query_with_anonymous_order_answer_is_not_pass(self):
        row={**self.row(),'expected':{'order_ids':['ORD-101']},'answer':'訂單項目 1','reasons':[]}
        checked=assess_record_identity(row)
        self.assertFalse(checked['checks']['answer'])
        self.assertIsNone(row['checks']['answer'])
        checked=assess_record_identity({**row,'answer':'ORD-101 尚未出貨'})
        self.assertTrue(checked['checks']['answer'])
        self.assertIsNone(checked['full_answer_review'])

    def test_barcode_cannot_substitute_for_unit_id(self):
        row={**self.row(),'expected':{'view':'units'},'answer':'SKU CUP，品號 88001',
             'calls':[{'tool':'inventory','parameters':{'view':'units'},'result':{'rows':[{'id':'unit-123','barcode':'88001'}]}}]}
        self.assertFalse(assess_record_identity(row)['checks']['answer'])
