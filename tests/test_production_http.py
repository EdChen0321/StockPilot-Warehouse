import unittest
import test_http


class ProductionHttpTests(unittest.TestCase):
    setUp = test_http.HttpTests.setUp
    tearDown = test_http.HttpTests.tearDown
    request = test_http.HttpTests.request
    def test_disabled_product_and_empty_factory_http(self):
        _, factory=self.request('/api/materials/suppliers',dict(operation_id='factory',name='工廠'))
        _, product=self.request('/api/catalogue/products',dict(operation_id='product',name='成品',sku='EDGE'))
        _, material=self.request('/api/materials/items',dict(operation_id='material',code='M',name='材料',unit='個',supplier_id=factory['id']))
        self.request('/api/materials/boms',dict(operation_id='bom',product_id=product['id'],expected_version=0,lines=[dict(material_id=material['id'],quantity='1',unit='個')]))
        for index,value in enumerate(['',None,False,0,[],{}]):
            self.assertEqual(self.request('/api/materials/plans',dict(operation_id='empty'+str(index),number='EMPTY',factory_id=value,product_id=product['id'],quantity=1))[0],400)
        self.assertEqual(self.request('/api/materials')[1]['plans'],[])
        _, plan=self.request('/api/materials/plans',dict(operation_id='valid',number='VALID',factory_id=factory['id'],product_id=product['id'],quantity=1))
        self.assertEqual(self.request('/api/catalogue/products/'+product['id'],dict(operation_id='disable',expected_version=1,active=False))[0],200)
        self.assertEqual(self.request('/api/materials/plans/'+plan['id']+'/confirm',dict(operation_id='confirm',expected_version=1))[0],409)
        snapshot=self.request('/api/materials')[1]
        self.assertEqual((snapshot['plans'][0]['state'],snapshot['plans'][0]['version']),('draft',1))
        self.assertEqual(self.request('/api/stock')[1]['units'],[])

    def test_factory_production_http(self):
        _, f=self.request('/api/materials/suppliers',dict(operation_id='f',name='產品工廠'))
        _, p=self.request('/api/catalogue/products',dict(operation_id='p',name='成品',sku='PROD',barcode='PROD'))
        status, plan=self.request('/api/materials/plans',dict(operation_id='plan',number='PLAN',factory_id=f['id'],product_id=p['id'],quantity=100))
        self.assertEqual(status,200)
        path='/api/materials/plans/'+plan['id']
        _, plan=self.request(path+'/confirm',dict(operation_id='confirm',expected_version=plan['version']))
        _, supplier=self.request('/api/materials/suppliers',dict(operation_id='supplier',name='物料工廠'))
        _, material=self.request('/api/materials/items',dict(operation_id='material',code='CASE',name='外殼',unit='個',supplier_id=supplier['id']))
        _, po=self.request('/api/materials/purchases',dict(operation_id='po',number='PO',supplier_id=supplier['id'],plan_id=plan['id'],lines=[dict(material_id=material['id'],quantity='30')]))
        self.assertEqual(po['factory_name'],'產品工廠')
        self.request('/api/materials/purchases/'+po['id']+'/confirm',dict(operation_id='po-confirm'))
        self.assertEqual(self.request('/api/materials/movements',dict(operation_id='material-receipt',number='MR',kind='receipt',purchase_id=po['id'],lines=[dict(material_id=material['id'],quantity='30')]))[0],200)
        self.assertEqual(self.request('/api/materials/movements',dict(operation_id='consume',number='USE',kind='consume',plan_id=plan['id'],lines=[dict(material_id=material['id'],quantity='25')]))[0],200)
        _, materials=self.request('/api/materials')
        self.assertEqual(materials['materials'][0]['on_hand'],'5')
        self.assertEqual(self.request('/api/stock')[1]['units'],[])
        plan=materials['plans'][0]
        self.request('/api/locations/enable',dict(operation_id='enable'))
        body=dict(operation_id='receive',expected_version=plan['version'],number='R',quantity=40)
        status, plan=self.request(path+'/receive',body)
        self.assertEqual(status,200)
        self.assertEqual((plan['received'],plan['pending']),(40,60))
        self.assertEqual(self.request(path+'/receive',body)[1],plan)
        self.assertEqual(self.request(path+'/receive',dict(body,operation_id='stale'))[0],409)
        _, snapshot=self.request('/api/stock')
        self.assertEqual((snapshot['products'][0]['on_hand'],snapshot['products'][0]['available']),(40,0))
        self.assertEqual(snapshot['receipts'][0]['production_plan_id'],plan['id'])
        _, shipment=self.request('/api/shipments',dict(operation_id='shipment',entry_number='SHIP'))
        self.assertEqual(self.request('/api/shipments/'+shipment['id']+'/scan',dict(operation_id='scan',barcode='PROD'))[0],409)
        _, place=self.request('/api/locations',dict(operation_id='place',address='1F',position='A',name='成品櫃',can_import=True,can_export=True,can_return=True,entry_state='import'))
        assign=dict(operation_id='assign',receipt_id=plan['receipts'][0]['id'],quantity=40,place_id=place['id'])
        self.assertEqual(self.request('/api/locations/assign',assign)[0],409)
        assign.update(cost_acknowledged=True,expected_cost_version=0)
        self.assertEqual(self.request('/api/locations/assign',assign)[0],200)
        self.assertEqual(self.request('/api/shipments/'+shipment['id']+'/scan',dict(operation_id='scan-after',barcode='PROD'))[0],200)
        self.assertEqual(self.request('/api/materials')[1]['plans'][0]['received'],40)
        self.assertEqual(self.request('/api/materials')[1]['materials'][0]['on_hand'],'5')
