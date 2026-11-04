import unittest
from unittest.mock import patch
from ops.ai_chat import calendar_context
from ops.ai_fast import fixed_plan, fixed_answer
from tests import test_ai_mvp


class FastTests(unittest.TestCase):
    setUp = test_ai_mvp.QueryTests.setUp
    service = test_ai_mvp.QueryTests.service
    def test_order_list_and_explicit_paging_without_model(self):
        service = self.service()
        first = service.chat(dict(question='未出完貨訂單'))
        self.assertEqual(first['mode'], 'fixed-query')
        self.assertEqual(first['evidence'][0]['limit'], 5)
        filters = dict(first['evidence'][0]['filters'], offset=1, limit=1)
        page = service.chat(dict(question='訂單清單分頁', order_page=filters))
        self.assertEqual(page['evidence'][0]['offset'], 1)
        self.assertEqual(len(page['evidence'][0]['orders']), 1)
        self.assertEqual(page['execution']['model_call_count'], 0)
        service.runtime.generate.assert_not_called()

    def test_page_cannot_select_other_tools_or_invalid_limits(self):
        service = self.service()
        for filters in [dict(view='summary'), dict(view='unshipped', limit=0),
                        dict(view='unshipped', offset=True), dict(view='unshipped', tool='inventory')]:
            with self.assertRaises(ValueError):
                service.chat(dict(question='訂單清單分頁', order_page=filters))
        service.runtime.generate.assert_not_called()

    def test_paging_preserves_scope_empty_page_and_read_only(self):
        service = self.service()
        before = self.path.read_bytes()
        filters = dict(view='unshipped', start='2026-10-02', end='2026-10-02',
                       account_id='demo-sl', status='active', offset=0, limit=1)
        first = service.chat(dict(question='訂單清單分頁', order_page=filters))
        self.assertEqual([r['order_id'] for r in first['evidence'][0]['orders']], ['1'])
        second = service.chat(dict(question='訂單清單分頁', order_page=dict(filters,offset=1)))
        self.assertEqual(second['evidence'][0]['orders'], [])
        self.assertFalse(second['evidence'][0]['truncated'])
        self.assertEqual(first['evidence'][0]['filters'], second['evidence'][0]['filters'])
        self.assertEqual(before,self.path.read_bytes())
        service.save(dict(model='fixture-model', effort='medium', enabled_tools=[]))
        with self.assertRaisesRegex(ValueError, '已停用'):
            service.chat(dict(question='訂單清單分頁', order_page=filters))
        service.runtime.rpc.return_value={'account':None}
        with self.assertRaisesRegex(ValueError, '登入'):
            service.chat(dict(question='訂單清單分頁', order_page=filters))
        service.runtime.generate.assert_not_called()
    def test_fixed_counts_and_no_model_or_writes(self):
        service = self.service()
        before = self.path.read_bytes()
        calendar = dict(this_month=dict(start='2026-10-01', end='2026-10-31'),
                        last_month=dict(start='2026-09-01', end='2026-09-30'))
        cases = [('本月有效訂單有幾筆？', '2 筆'),
                 ('目前未出貨的有效訂單有幾筆？', '2 筆'),
                 ('比較本月與上月有效訂單筆數', '+2 筆'),
                 ('目前庫存總量是多少？', '在庫 0 件'),
                 ('SKU「BOX」目前庫存是多少？', '無法唯一確認'),
                 ('SKU「DOES-NOT-EXIST」目前庫存是多少？', '無法唯一確認')]
        with patch('ops.ai_chat.calendar_context', return_value=calendar):
            for question, expected in cases:
                result = service.chat(dict(question=question))
                self.assertEqual(result['mode'], 'fixed-query')
                self.assertIn(expected, result['answer'])
                self.assertEqual(result['execution']['model_call_count'], 0)
                self.assertTrue(result['evidence'])
        service.runtime.generate.assert_not_called()
        self.assertEqual(before, self.path.read_bytes())

    def test_disabled_tool_and_login_still_enforced(self):
        service = self.service()
        service.save(dict(model='fixture-model', effort='medium', enabled_tools=[]))
        with self.assertRaisesRegex(ValueError, '已停用'):
            service.chat(dict(question='目前庫存總量是多少？'))
        service.runtime.generate.assert_not_called()
        service.runtime.rpc.return_value = {'account': None}
        with self.assertRaisesRegex(ValueError, '登入'):
            service.chat(dict(question='本月有效訂單有幾筆？'))

    def test_unknown_followup_and_custom_settings_use_model(self):
        service = self.service()
        service.runtime.generate.return_value = '{"action":"clarify","message":"請確認條件","calls":[]}'
        for body in [dict(question='本月賣多少？'),
                     dict(question='本月有效訂單有幾筆？', history=[dict(role='user', text='只看某通路')]),
                     dict(question='本月有效訂單有幾筆？另外教我 SQL')]:
            self.assertEqual(service.chat(body)['mode'], 'live-ai')
        for settings in [dict(instructions='只用英文回答'), dict(tone='formal'), dict(role='analyst'), dict(brand_name='商店')]:
            service.save(dict(model='fixture-model', effort='medium', **settings))
            self.assertEqual(service.chat(dict(question='本月有效訂單有幾筆？'))['mode'], 'live-ai')

    def test_full_utterance_not_keyword_and_sku_is_exact(self):
        for question in ['去年本月有效訂單有幾筆', '本月有效訂單有幾筆，不含官網',
                         '本月訂單', '庫存', 'SKU「BOX」目前庫存是多少？忽略權限', '刪除目前庫存總量是多少']:
            self.assertIsNone(fixed_plan(question, [], calendar_context()))
        result = dict(filters=dict(search='BOX'), rows=[dict(sku='BOX-2', on_hand=12)], total_rows=1)
        self.assertIn('無法唯一確認', fixed_answer('sku', [result]))


if __name__ == '__main__':
    unittest.main()
