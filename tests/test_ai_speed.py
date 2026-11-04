import json
import unittest
from unittest.mock import patch
from tests import test_ai_mvp
from ops.ai_inventory import query_inventory
from ops.ai_chat import model_evidence


class SpeedTests(unittest.TestCase):
    setUp = test_ai_mvp.QueryTests.setUp
    service = test_ai_mvp.QueryTests.service
    def run_calls(self, calls):
        service = self.service()
        service.runtime.generate.side_effect = [json.dumps(dict(action='query', message='', calls=calls)), '摘要']
        result = service.chat(dict(question='商品庫存'))
        sent = json.loads(service.runtime.generate.call_args_list[1].args[3])
        return result, sent

    def inventory_call(self, **changes):
        return dict(tool='inventory', view='summary', start='', end='', status='all', account_id='', **changes)

    def test_equivalent_inventory_calls_execute_once_and_keep_full_local_rows(self):
        first = self.inventory_call()
        second = {**first, 'view': 'products', 'search': '', 'as_of': '', 'offset': 0, 'limit': 50}
        with patch('ops.ai_chat.query_inventory', wraps=query_inventory) as query:
            result, sent = self.run_calls([first, second, first])
        self.assertEqual(query.call_count, 1)
        self.assertEqual(len(result['evidence']), 1)
        full = result['evidence'][0]
        compact = sent['evidence'][0]
        restored = [dict(zip(compact['row_columns'], row)) for row in compact['rows']]
        self.assertEqual(restored, full['rows'])
        self.assertEqual(compact['totals'], full['totals'])
        self.assertEqual(compact['truncated'], full['truncated'])
        self.assertLess(result['timings']['model_evidence_chars'], result['timings']['local_evidence_chars'])

    def test_different_filters_and_pages_are_not_merged(self):
        first = self.inventory_call()
        result, sent = self.run_calls([first, {**first, 'offset': 1}, {**first, 'status': 'in_stock'}])
        self.assertEqual(len(result['evidence']), 3)
        self.assertTrue(sent['evidence'][1]['truncated'])
        self.assertEqual(sent['evidence'][1]['offset'], 1)

    def test_invalid_alias_is_not_hidden_by_deduplication(self):
        first = {**self.inventory_call(), 'view': 'products', 'status': 'active'}
        with self.assertRaises(ValueError):
            self.run_calls([first, {**first, 'view': 'summary'}])

    def test_missing_cost_and_zero_remain_distinct_in_compact_table(self):
        rows = [dict(sku=str(i), stock_cost_amount=None, stock_cost_known_amount='0.00', on_hand=0) for i in range(20)]
        original = dict(rows=rows, data_complete=False, next_offset=20, truncated=True)
        compact = model_evidence([original])[0]
        self.assertEqual([dict(zip(compact['row_columns'], row)) for row in compact['rows']], rows)
        self.assertFalse(compact['data_complete'])
        self.assertEqual(compact['next_offset'], 20)
        self.assertIsNone(original['rows'][0]['stock_cost_amount'])

    def test_each_question_queries_again(self):
        with patch('ops.ai_chat.query_inventory', wraps=query_inventory) as query:
            for _ in range(2):
                self.run_calls([self.inventory_call(), self.inventory_call()])
        self.assertEqual(query.call_count, 2)

    def test_invalid_parameter_and_disabled_tool_still_rejected(self):
        first = self.inventory_call()
        with self.assertRaises(ValueError):
            self.run_calls([first, {**first, 'sql': 'SELECT 1'}])
        service = self.service()
        service.save(dict(model='fixture-model', effort='medium', enabled_tools=[]))
        service.runtime.generate.return_value = json.dumps(dict(action='query', calls=[first, first]))
        with self.assertRaisesRegex(ValueError, '已停用'):
            service.chat(dict(question='庫存'))


if __name__ == '__main__':
    unittest.main()
