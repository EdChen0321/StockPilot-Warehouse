import unittest
from qa.run_ai_evaluation import assess


class ScorerTests(unittest.TestCase):
    def result(self, count=2, status='active'):
        return dict(execution=dict(tool_calls=[dict(tool='orders',parameters=dict(status=status),result=dict(order_count=count))]))

    def test_wrong_scope_and_wrong_number_fail(self):
        case=dict(expected=dict(tool='orders',status='active',order_count=2))
        self.assertTrue(assess(case,self.result(),None)[0]['evidence'])
        self.assertFalse(assess(case,self.result(status='all'),None)[0]['evidence'])
        self.assertFalse(assess(case,self.result(count=3),None)[0]['evidence'])

    def test_null_never_matches_missing_or_zero(self):
        case=dict(expected=dict(tool='orders',order_amount=None))
        data=self.result()
        self.assertFalse(assess(case,data,None)[0]['evidence'])
        data['execution']['tool_calls'][0]['result']['order_amount']=None
        self.assertTrue(assess(case,data,None)[0]['evidence'])
        data['execution']['tool_calls'][0]['result']['order_amount']=0
        self.assertFalse(assess(case,data,None)[0]['evidence'])

    def test_runtime_failure_is_not_permission_success(self):
        case=dict(expected=dict(blocked=True))
        self.assertFalse(assess(case,{},'請先登入')[0]['evidence'])
        self.assertTrue(assess(case,{},'此資料工具已停用')[0]['evidence'])

    def test_statement_not_assessed_from_successful_tool(self):
        case=dict(expected=dict(tool='orders',order_count=2))
        result={**self.result(),'answer':'胡亂說一段文字'}
        self.assertIsNone(assess(case,result,None)[0]['answer'])
