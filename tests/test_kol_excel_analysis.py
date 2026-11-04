"""Analysis attribution and internal-order behavior for confirmed Excel imports."""
from uuid import uuid4
from ops.kol import Kol, report
from test_kol_excel_import import ImportFixture
from test_kol_excel_cleaning import line

class ExcelAnalysisTests(ImportFixture):
    def test_every_order_has_exactly_one_destination_without_changing_metrics(self):
        k=self.save_kol()
        rows=[line(**{'訂單號碼':'MATCH'}),
              line(**{'訂單號碼':'GENERAL','UTM 媒介':'  '}),
              line(**{'訂單號碼':'UNKNOWN','UTM 媒介':'new-tag'}),
              line(**{'訂單號碼':'EARLY','訂單日期':'2025-12-01 10:00:00'}),
              line(**{'訂單號碼':'CASE','UTM 媒介':'Sunny'}),
              line(**{'訂單號碼':'CANCEL','UTM 媒介':None,'訂單狀態':'已取消'}),
              line(**{'訂單號碼':'EXCLUDED','商品貨號':None})]
        batch=self.preview(rows);self.service.confirm({'batch_id':batch['id']})
        before=self.business_snapshot()
        result=report(self.store,status='all')
        orders={o['order_id']:o for o in result['orders']}
        self.assertEqual(len(orders),7)
        self.assertEqual({oid:o['kol_id'] for oid,o in orders.items()},
            dict(MATCH=k['id'],GENERAL='general',UNKNOWN='unattributed',EARLY='unattributed',
                 CASE='unattributed',CANCEL='general',EXCLUDED=k['id']))
        self.assertEqual(result['status_counts'],dict(active=5,cancelled=1,excluded=1,all=7))
        self.assertEqual(result['summary']['order_count'],5)
        self.assertEqual(sum(g['order_count'] for g in result['groups']),5)
        from decimal import Decimal
        self.assertEqual(sum(Decimal(g['total']) for g in result['groups']),Decimal(result['summary']['total']))
        general=report(self.store,kol_id='general',status='all')
        self.assertEqual(general['kol']['name'],'一般訂單')
        self.assertTrue(general['kol']['system_group'])
        self.assertEqual({o['order_id'] for o in general['orders']},{'GENERAL','CANCEL'})
        self.assertEqual(general['summary']['order_count'],1)
        self.assertEqual(report(self.store,status='excluded')['orders'][0]['order_id'],'EXCLUDED')
        self.assertEqual(before,self.business_snapshot())

    def test_system_groups_persist_and_reassignment_does_not_need_reimport(self):
        from ops.kol import export_csv
        from csv import DictReader
        from io import StringIO
        empty=report(self.store)
        self.assertEqual({g['id'] for g in empty['groups']},{'general','unattributed'})
        b=self.preview([line(),line(**{'訂單號碼':'NONE','UTM 媒介':None})]);self.service.confirm({'batch_id':b['id']})
        self.assertEqual(report(self.store,kol_id='unattributed')['summary']['order_count'],1)
        k=self.save_kol()
        self.assertEqual(report(self.store,kol_id=k['id'])['summary']['order_count'],1)
        self.assertEqual(report(self.store,kol_id='unattributed')['summary']['order_count'],0)
        self.assertEqual(report(self.store,kol_id='general')['summary']['order_count'],1)
        exported=list(DictReader(StringIO(export_csv(report(self.store),'list'))))
        self.assertEqual({g['id'] for g in exported},{k['id'],'general','unattributed'})
        Kol(self.store).delete(dict(operation_id=uuid4().hex,id=k['id'],expected_version=1))
        self.assertEqual(report(self.store,kol_id='unattributed')['summary']['order_count'],1)
        self.assertEqual(report(self.store,kol_id='general')['summary']['order_count'],1)

    def test_system_groups_cannot_be_edited_or_deleted(self):
        service=Kol(self.store)
        for key in ('general','unattributed'):
            with self.assertRaisesRegex(ValueError,'系統分類'):
                service.save(dict(operation_id=uuid4().hex,id=key,expected_version=1,name='bad',utms='bad',started_at='2026-01-01T00:00:00+08:00'))
            with self.assertRaisesRegex(ValueError,'系統分類'):
                service.delete(dict(operation_id=uuid4().hex,id=key,expected_version=1))

    def save_kol(self, utms='sunny'):
        return Kol(self.store).save(dict(operation_id=uuid4().hex,name='合成人物',utms=utms,
            platforms='IG',started_at='2026-01-01T00:00:00+08:00',comment=''))

    def test_unknown_utm_and_no_utm_are_separate_and_recomputed(self):
        b=self.preview([line(),line(**{'訂單號碼':'NONE','UTM 媒介':None})])
        self.assertEqual(b['unmatched_utms'],[{'utm_medium':'sunny','order_count':1}])
        self.assertEqual(b['no_utm_count'],1)
        self.service.confirm({'batch_id':b['id']})
        k=self.save_kol()
        self.assertEqual(self.service.detail(b['id'])['unmatched_utms'],[])
        matched=report(self.store,kol_id=k['id'])
        self.assertEqual(matched['summary']['order_count'],1)
        self.assertEqual(matched['summary']['total'],'960.00')
        self.assertEqual(report(self.store,kol_id='unattributed')['summary']['order_count'],0)
        self.assertEqual(report(self.store,kol_id='general')['summary']['order_count'],1)

    def test_internal_orders_preserved_and_default_excluded(self):
        self.save_kol()
        b=self.preview([line(),line(**{'訂單號碼':'INTERNAL','顧客':'合成D1業配'})])
        self.assertEqual(len(b['orders']),2)
        self.assertEqual(sum(bool(o['internal']) for o in b['orders']),1)
        self.service.confirm({'batch_id':b['id']})
        self.assertEqual(report(self.store)['summary']['order_count'],1)
        self.assertEqual(report(self.store,include_internal=True)['summary']['order_count'],2)
        self.assertEqual(report(self.store,include_internal=True)['summary']['total'],'1920.00')

    def test_first_row_total_is_not_lost_when_first_line_is_excluded(self):
        self.save_kol()
        b=self.preview([line(**{'商品貨號':None}),line(**{'訂單合計':None,'UTM 媒介':None})])
        self.assertEqual(len(b['exclusions']),1)
        self.service.confirm({'batch_id':b['id']})
        result=report(self.store)
        self.assertEqual(result['summary']['order_count'],1)
        self.assertEqual(result['summary']['total'],'960.00')
        self.assertEqual(len(result['products']),1)

    def test_existing_operational_source_is_deduplicated_and_not_changed(self):
        from test_orders import row, as_csv
        self.save_kol()
        standard=self.store.preview('demo-sl','standard.csv',as_csv([row(order_id='SYN-1',utm_medium='sunny',order_total='800')]),True)
        self.store.confirm(standard['id'])
        before=self.business_snapshot()
        b=self.preview();self.service.confirm({'batch_id':b['id']})
        result=report(self.store)
        self.assertEqual(result['summary']['order_count'],1)
        self.assertEqual(result['summary']['total'],'960.00')
        self.assertEqual(result['duplicate_source_count'],1)
        self.assertEqual(before,self.business_snapshot())
        cancel=self.preview([line(**{'訂單狀態':'已取消'})],filename='cancel.xlsx')
        self.service.confirm({'batch_id':cancel['id']})
        self.assertEqual(report(self.store)['summary']['order_count'],0)
        self.assertEqual(before,self.business_snapshot())

