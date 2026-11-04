import io
import re
import unittest
from zipfile import ZipFile, ZIP_DEFLATED
from ops.kol_import import read_xlsx, clean_rows
from test_kol_excel_cleaning import workbook_bytes, line, catalog


def rewrite_sheet(blob, transform):
    result = io.BytesIO()
    with ZipFile(io.BytesIO(blob)) as source, ZipFile(result, 'w', ZIP_DEFLATED) as target:
        for member in source.infolist():
            data = source.read(member)
            if member.filename == 'xl/worksheets/sheet1.xml':
                data = transform(data)
            target.writestr(member, data)
    return result.getvalue()


class ReaderEdges(unittest.TestCase):
    def test_underreported_dimension_cannot_drop_an_order(self):
        blob = workbook_bytes([line(),line(**{'訂單號碼':'SECOND'})])
        blob = rewrite_sheet(blob, lambda raw:re.sub(b'<dimension ref="[^"]+"', b'<dimension ref="A1:K2"',raw))
        result = clean_rows(read_xlsx(blob,'small-dimension.xlsx'), catalog())
        self.assertEqual(result['summary']['order_count'], 2)
        self.assertEqual(result['summary']['order_total'], '1920.00')

    def test_malformed_xml_is_a_validation_error(self):
        blob = rewrite_sheet(workbook_bytes([line()]), lambda raw:b'<broken')
        with self.assertRaises(ValueError):
            read_xlsx(blob,'broken.xlsx')

    def test_actual_column_limit_still_applies_without_dimensions(self):
        blob = workbook_bytes([{'x':'one'}],headers=[str(n) for n in range(201)])
        blob = rewrite_sheet(blob, lambda raw:re.sub(b'<dimension ref="[^"]+"', b'<dimension ref="A1:A2"',raw))
        with self.assertRaises(ValueError):
            read_xlsx(blob,'wide.xlsx')

    def test_encoded_entity_declaration_is_rejected(self):
        xml = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE worksheet [<!ENTITY sample "value">]><worksheet>&sample;</worksheet>'
        blob = rewrite_sheet(workbook_bytes([line()]),lambda raw:xml.encode('utf-16'))
        with self.assertRaises(ValueError):
            read_xlsx(blob,'entity.xlsx')
