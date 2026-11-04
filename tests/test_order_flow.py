import tempfile
import unittest
from pathlib import Path
from ops.ai_chat import calendar_context, order_list_answer
from ops.ai_queries import query_orders
from ops.server import create_server
from qa.verify_order_flow import fixture
from qa.run_ai_evaluation import digest


class OrderFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'orders.db';self.calendar=calendar_context()
        self.data=fixture(self.path,self.calendar)
        self.filters=dict(view='unshipped',status='active',**self.calendar['yesterday'])

    def test_known_answer_partial_complete_cancelled_and_dates(self):
        before=digest(self.path)
        result=query_orders(self.path,self.filters)
        self.assertEqual([r['order_id'] for r in result['orders']],['ORD-101','PARTIAL'])
        partial=result['orders'][1]
        self.assertEqual((partial['fulfillment_status'],partial['remaining_quantity']),('部分出貨',1))
        self.assertEqual(partial['items'][0]['shipped_quantity'],1)
        self.assertIn('PARTIAL',order_list_answer(result))
        self.assertEqual(digest(self.path),before)

    def test_page_boundaries_and_validation(self):
        pages=[query_orders(self.path,dict(self.filters,offset=n,limit=1)) for n in range(3)]
        self.assertEqual([[r['order_id'] for r in p['orders']] for p in pages],[['ORD-101'],['PARTIAL'],[]])
        self.assertTrue(pages[0]['truncated']);self.assertFalse(pages[1]['truncated'])
        self.assertIn('本頁沒有',order_list_answer(pages[2]))
        for fields in [dict(offset=True),dict(offset=-1),dict(limit=51),dict(limit=0),dict(limit='2')]:
            with self.assertRaises(ValueError):query_orders(self.path,dict(self.filters,**fields))

    def test_existing_source_no_migration_and_wrong_source_no_links(self):
        before=digest(self.path)
        server=create_server(self.path,0,initialize_database=False)
        self.addCleanup(server.server_close)
        self.assertTrue(server.chat.order_links_enabled)
        with self.assertRaises(OSError):create_server(self.path,server.server_port,initialize_database=False)
        self.assertEqual(digest(self.path),before)
        other=Path(self.temp.name)/'other.db'
        different=create_server(other,0,ai_database=self.path)
        self.addCleanup(different.server_close)
        self.assertFalse(different.chat.order_links_enabled)
        with self.assertRaises(ValueError):create_server(Path(self.temp.name)/'missing.db',0,initialize_database=False)

if __name__=='__main__':unittest.main()
