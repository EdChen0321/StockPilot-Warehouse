import json
import tempfile
import unittest
from pathlib import Path
from ops.store import Store
from ops.orders import Orders
from ops.stock import Stock, shipment_time_snapshot


class ListPageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'lists.sqlite3');self.store.seed()
        with self.store.connect() as db:
            db.execute("INSERT INTO batches VALUES('b','demo-sl','orders','','','confirmed',NULL,'2026-11-02','[]','[]')")
            for i in range(125):
                number=f'ORDER-{i:04d}'
                order=dict(order_id=number,revision=1,status='cancelled' if i%3==0 else 'active',lines=[dict(product_id='P-BOX',external_sku='Cup',quantity=1)])
                db.execute('INSERT INTO order_versions VALUES(?,?,?,?,?,?)',('demo-sl',number,1,number,json.dumps(order),'b'))
                db.execute('INSERT INTO current_orders VALUES(?,?,?)',('demo-sl',number,1))
                db.execute('INSERT INTO fulfillment_groups VALUES(?,?,?,?,?)',(str(i),'demo-sl',number,1,'{}'))
                db.execute('INSERT INTO shipments(id,group_id,number,state,lines,created_at,channel) VALUES(?,?,?,?,?,?,?)',
                    (str(i),str(i),f'OUT-{i:04d}','shipped' if i%2 else 'draft',json.dumps([dict(product_id='P-BOX',quantity=1)]),'2026-11-02','門市' if i%2 else '網店'))

    def test_order_paging_matches_all_legacy_filters(self):
        service=Orders(self.store)
        for filters in ({},{'q':'cup'},{'q':'ORDER-0124'},{'status':'cancelled'},{'q':'沒有'},{'q':'小收納盒'}):
            first=service.page(**filters)
            rows=[o for page in range(1,first['pages']+1) for o in service.page(page=page,**filters)['orders']]
            self.assertEqual(rows,service.list(**filters))
            self.assertEqual(first['total'],len(rows))
            self.assertLessEqual(len(first['orders']),50)
        with self.assertRaises(ValueError):service.page(page=0)
        with self.assertRaises(ValueError):service.page(account_id='unknown')

    def test_shipments_paged_before_details_and_filters(self):
        service=Stock(self.store)
        all_rows=[s for page in (1,2,3) for s in service.shipment_page(page=page)['shipments']]
        with self.store.connect() as db:
            legacy=shipment_time_snapshot(db)
        for row in legacy:row.pop('lines')
        self.assertEqual(all_rows,legacy)
        self.assertTrue(all('lines' not in s for s in all_rows))
        filtered=service.shipment_page(state='complete',source='門市')
        self.assertEqual(filtered['total'],62)
        self.assertEqual(service.shipment_page(q='ORDER-0124')['total'],1)
        self.assertEqual(service.shipment_page(q='小收納盒')['total'],125)
        self.assertEqual(service.shipment_page(q='沒有')['shipments'],[])
        with self.assertRaises(ValueError):service.shipment_page(state='anything')


if __name__=='__main__':unittest.main()
