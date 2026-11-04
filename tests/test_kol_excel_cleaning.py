"""Synthetic fixtures exercise the legacy cleaning contract without historical files."""
import io
import unittest
from datetime import datetime
from openpyxl import Workbook
from ops.kol_import import read_xlsx, clean_rows, load_catalog

HEADERS = ['訂單號碼', '訂單日期', '訂單狀態', '訂單合計', '商品貨號', '商品名稱', '商品結帳價', '數量', '顧客 ID', '顧客', 'UTM 媒介']

def line(**changes):
    result = dict(zip(HEADERS, ['SYN-1', datetime(2026, 1, 2), '已完成', 960, 'AA-01001', '原始名稱', 200, 2, 'SYN-C', '合成客戶', 'sunny']))
    result.update(changes)
    return result

def workbook_bytes(rows, headers=HEADERS, extra_sheet=False):
    wb = Workbook(); ws = wb.active
    ws.append(headers)
    for row in rows: ws.append([row.get(h) for h in headers])
    if extra_sheet: wb.create_sheet('second')
    stream = io.BytesIO(); wb.save(stream); wb.close()
    return stream.getvalue()

def catalog():
    return [dict(sku=sku, name=name, category=category, product_code=sku)
            for sku, name, category in [('AA-01001', '標準甲', '合成類'), ('BB-02001', '標準乙', '合成類'),
                                        ('HO-02001', '修正甲', '合成類'), ('GX-16001', '歷史清潔', '合成類'),
                                        ('ZZ-01001', '刪除商品', '上傳 刪除')]]

def cleaned(rows, products=None):
    return clean_rows(read_xlsx(workbook_bytes(rows), 'synthetic.xlsx'), products or catalog())

class CleaningTests(unittest.TestCase):
    def test_grouped_header_total_and_utm(self):
        result = cleaned([line(), line(**{'商品貨號':'BB-02001','訂單合計':None,'UTM 媒介':None})])
        self.assertFalse(result['errors'])
        self.assertEqual(len(result['orders']), 1)
        order = result['orders'][0]
        self.assertEqual(order['utm_medium'], 'sunny')
        self.assertEqual(len(order['lines']), 2)
        self.assertEqual(result['summary']['order_total'], '960.00')

    def test_normalization_and_component_provenance(self):
        result = cleaned([line(**{'商品貨號':'#AA-X01001*3+BB-02001'})])
        self.assertFalse(result['errors'])
        lines = result['orders'][0]['lines']
        self.assertEqual([(r['sku'],r['quantity']) for r in lines], [('AA-01001',6),('BB-02001',2)])
        self.assertEqual({r['row_index'] for r in lines}, {2})
        self.assertEqual(len({r['component_index'] for r in lines}), 2)
        self.assertTrue(all(r['original_sku']=='#AA-X01001*3+BB-02001' for r in lines))

    def test_name_boundary_and_manual_history_fixes(self):
        for date, expected in [(datetime(2021,8,31),'原始名稱'),(datetime(2021,9,1),'標準甲')]:
            with self.subTest(date=date):
                result=cleaned([line(**{'訂單日期':date})])
                self.assertEqual(result['orders'][0]['lines'][0]['name'], expected)
        result=cleaned([line(**{'商品貨號':'HO-01001'})])
        self.assertEqual(result['orders'][0]['lines'][0]['sku'],'HO-02001')
        result=cleaned([line(**{'商品貨號':'GX-01001','商品名稱':'天王星清潔慕斯','訂單日期':datetime(2021,8,31)})])
        self.assertEqual(result['orders'][0]['lines'][0]['sku'],'GX-16001')

    def test_name_reverse_lookup_only_for_unmatched_sku(self):
        result=cleaned([line(**{'商品名稱':'標準甲'}),line(**{'訂單號碼':'SYN-2','商品貨號':'UNKNOWN','商品名稱':'標準甲'})])
        self.assertEqual(sum(len(o['lines']) for o in result['orders']),2)
        self.assertEqual({r['sku'] for o in result['orders'] for r in o['lines']},{'AA-01001'})

    def test_excluded_rows_have_source_and_reason(self):
        rows=[line(**{'訂單號碼':'C','訂單狀態':'已取消'}),line(**{'訂單號碼':'N','商品貨號':None}),line(**{'訂單號碼':'U','商品貨號':'UNKNOWN','商品名稱':'無法辨識'}),line(**{'訂單號碼':'D','商品貨號':'ZZ-01001'})]
        result=cleaned(rows)
        self.assertEqual(len(result['exclusions']),4)
        self.assertEqual({e['row_index'] for e in result['exclusions']},{2,3,4,5})
        self.assertTrue(all(e['reason'] for e in result['exclusions']))
        self.assertEqual(result['summary']['clean_line_count'],0)

    def test_conflicting_total_and_missing_total_block(self):
        for rows in [[line(**{'訂單合計':None})],[line(),line(**{'訂單合計':961})]]:
            with self.subTest(rows=len(rows)): self.assertTrue(cleaned(rows)['errors'])

    def test_reader_rejects_corrupt_formula_multisheet_and_duplicate_headers(self):
        cases=[b'not an xlsx',workbook_bytes([line(**{'商品名稱':'=1+1'})]),workbook_bytes([line()],extra_sheet=True),workbook_bytes([line()],HEADERS+['數量'])]
        for blob in cases:
            with self.subTest(size=len(blob)):
                with self.assertRaises(ValueError): read_xlsx(blob,'synthetic.xlsx')
