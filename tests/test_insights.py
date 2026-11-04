import tempfile
import unittest
from pathlib import Path
from test_orders import row, as_csv
from ops.store import Store
from ops.materials import Materials
from ops.insights import sales_insights, crossbuy, product_overview


class InsightTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store=Store(Path(temp.name)/'insights.db')
        self.store.seed()
        def upload(account, **changes):
            lines=changes.pop('lines', [('P-BOX','100')])
            total=str(sum(int(value) for _,value in lines))
            rows=[row(order_id=changes.get('order_id','O1'), line_id=str(i), external_sku=sku,
                quantity='1', line_item_amount=value, order_item_amount=total, expected_line_count=str(len(lines)),
                customer_ref=changes.get('customer_ref','c1'), kol_ref=changes.get('kol_ref',''),
                ordered_at=changes.get('ordered_at','2026-01-02T09:00:00+08:00'), raw_status=changes.get('raw_status','active'))
                for i,(sku,value) in enumerate(lines)]
            batch=self.store.preview(account,'test.csv',as_csv(rows),True)
            self.store.confirm(batch['id'])
        upload('demo-sl',lines=[('P-BOX','100'),('P-POUCH','50')],kol_ref='K1')
        upload('demo-sl',order_id='O2',lines=[('P-POUCH','50')],ordered_at='2026-01-03T09:00:00+08:00')
        upload('demo-sl',order_id='O3',lines=[('SET-01','200')],customer_ref='c2')
        upload('demo-sp',order_id='O4')
        upload('demo-sl',order_id='O5',customer_ref='')
        upload('demo-sl',order_id='O6',raw_status='cancelled')

    def test_sales_group_totals_and_filters(self):
        data=sales_insights(self.store)
        self.assertEqual(data['amount'],'600.00')
        self.assertEqual(data['order_count'],5)
        for group in ['daily','channels','products','attribution']:
            self.assertEqual(sum(int(x['amount'].replace('.','')) for x in data[group]),60000)
        self.assertEqual(next(x for x in data['attribution'] if x['key']=='K1')['amount'],'150.00')
        filtered=sales_insights(self.store,account_id='demo-sl',product_id='P-POUCH',end='2026-01-02')
        self.assertEqual((filtered['amount'],filtered['order_count']),('50.00',1))

    def test_crossbuy_customer_scope_bundle_exclusion_and_missing_ids(self):
        data=crossbuy(self.store)
        pair=next(x for x in data['pairs'] if x['source_product_id']=='P-BOX' and x['target_product_id']=='P-POUCH')
        self.assertEqual((pair['eligible_buyers'],pair['same_order_buyers'],pair['later_buyers']),(2,1,1))
        self.assertEqual(data['missing_customer_orders'],1)
        self.assertFalse(any(x['source_product_id']=='SET-01' for x in data['pairs']))
        reverse=next(x for x in data['pairs'] if x['source_product_id']=='P-POUCH' and x['target_product_id']=='P-BOX')
        self.assertEqual(reverse['later_buyers'],0)
        scoped=crossbuy(self.store,account_id='demo-sl')
        self.assertEqual(scoped['pairs'][0]['eligible_buyers'],1)

    def test_new_bom_material_remains_visible_with_old_plan(self):
        materials=Materials(self.store)
        supplier=materials.supplier(dict(operation_id='s',name='合成廠'))
        first=materials.material(dict(operation_id='m1',code='OLD',name='舊材料',unit='張',supplier_id=supplier['id']))
        second=materials.material(dict(operation_id='m2',code='NEW',name='新材料',unit='張',supplier_id=supplier['id']))
        materials.bom(dict(operation_id='b1',product_id='P-BOX',expected_version=0,lines=[dict(material_id=first['id'],quantity='1',unit='張')]))
        materials.plan(dict(operation_id='p',number='PLAN',product_id='P-BOX',quantity=2))
        materials.bom(dict(operation_id='b2',product_id='P-BOX',expected_version=1,lines=[dict(material_id=second['id'],quantity='3',unit='張')]))
        rows={r['material_id']:r for r in product_overview(self.store,'P-BOX')['materials']}
        self.assertEqual(rows[first['id']]['demand'],'2')
        self.assertEqual(rows[second['id']]['demand'],'0')

    def test_product_overview_shared_id_and_no_writes(self):
        materials=Materials(self.store)
        supplier=materials.supplier(dict(operation_id='s',name='合成廠'))
        material=materials.material(dict(operation_id='m',code='PAPER',name='合成紙',unit='張',supplier_id=supplier['id']))
        materials.bom(dict(operation_id='b',product_id='P-BOX',expected_version=0,lines=[dict(material_id=material['id'],quantity='1.5',unit='張')]))
        with self.store.connect() as db:
            before=db.execute('SELECT count(*) FROM events').fetchone()[0]
        data=product_overview(self.store,'P-BOX')
        self.assertEqual(data['product']['sku'],'P-BOX')
        self.assertEqual(data['sales']['amount'],'300.00')
        self.assertEqual(data['stock']['available'],0)
        self.assertEqual(data['bom']['lines'][0]['quantity'],'1.5')
        self.assertEqual(data['materials'][0]['material_id'],material['id'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],before)
        with self.assertRaises((KeyError,ValueError)):
            product_overview(self.store,'unknown')
