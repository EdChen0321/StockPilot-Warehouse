import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from ops.ai_chat import ChatService, calendar_context
from qa.ai_evaluation_cases import build_fixture, add_stock


class FreshnessTests(unittest.TestCase):
    def test_same_service_requeries_after_new_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'fixture.db'
            fixture=build_fixture(path, calendar_context())
            service=ChatService(path,Path(temporary)/'ai')
            service.save(dict(model='fixture',effort='low'))
            service.runtime=Mock()
            service.runtime.rpc.return_value={'account':{'type':'chatgpt'}}
            before=path.read_bytes()
            first=service.chat({'question':'目前庫存總量是多少？'})
            self.assertEqual(first['evidence'][0]['totals']['on_hand'],6)
            self.assertEqual(path.read_bytes(),before)
            add_stock(fixture)
            updated=path.read_bytes()
            second=service.chat({'question':'目前庫存總量是多少？'})
            self.assertEqual(second['evidence'][0]['totals']['on_hand'],8)
            self.assertEqual(path.read_bytes(),updated)
            service.runtime.generate.assert_not_called()
