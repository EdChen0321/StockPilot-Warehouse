import csv
import io
import unittest

from ops.importing import FIELDS, parse_orders


def row(**changes):
    value = dict(order_id='O-001', source_revision='1',
                 ordered_at='2026-01-02T09:00:00+08:00', raw_status='active',
                 currency='TWD', expected_line_count='1', order_item_amount='200',
                 line_id='L1', external_sku='P-BOX', item_name='小收納盒',
                 quantity='2', line_item_amount='200')
    value.update(changes)
    return value


def as_csv(rows):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


class ImportTests(unittest.TestCase):
    def parse(self, *rows):
        return parse_orders(as_csv(rows))[1]

    def test_amount_is_whole_line_and_ids_remain_strings(self):
        order = self.parse(row(order_id='00001', external_sku='00002'))[0]
        self.assertEqual(order['errors'], [])
        self.assertEqual(order['amount_cents'], 20000)
        self.assertEqual(order['lines'][0]['external_sku'], '00002')

    def test_exact_duplicate_line_not_double_counted(self):
        order = self.parse(row(), row())[0]
        self.assertEqual(order['errors'], [])
        self.assertEqual(len(order['lines']), 1)
        self.assertTrue(order['warnings'])

    def test_same_product_different_line_ids_remain_separate(self):
        order = self.parse(row(expected_line_count='2', order_item_amount='400'),
                           row(line_id='L2', expected_line_count='2', order_item_amount='400'))[0]
        self.assertEqual(order['errors'], [])
        self.assertEqual(len(order['lines']), 2)

    def test_errors_block_entire_order_but_not_other_order(self):
        orders = self.parse(row(expected_line_count='2'), row(order_id='O-002'))
        self.assertTrue(orders[0]['errors'])
        self.assertEqual(orders[1]['errors'], [])

    def test_bad_values_do_not_reach_sales(self):
        for field, value in [('line_item_amount', '-1'), ('line_item_amount', 'NaN'),
                             ('line_item_amount', '0.001'), ('line_item_amount', '1e2'),
                             ('quantity', '0'), ('quantity', '1.5'), ('currency', 'USD'),
                             ('raw_status', 'refunded'), ('source_revision', '-1'),
                             ('ordered_at', '2026-01-02'), ('order_item_amount', '300')]:
            with self.subTest(field=field, value=value):
                self.assertTrue(self.parse(row(**{field: value}))[0]['errors'])

    def test_conflicting_duplicate_and_header_are_rejected(self):
        self.assertTrue(self.parse(row(), row(quantity='3'))[0]['errors'])
        self.assertTrue(self.parse(row(), row(line_id='L2', source_revision='2'))[0]['errors'])

    def test_required_columns_and_empty_files_are_rejected(self):
        for value in ['', 'order_id\nO1\n', ','.join(FIELDS) + '\n']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_orders(value)

    def test_file_and_column_limits(self):
        cases = [as_csv([row()]).replace('item_name,', 'item_name,unknown,'),
                 as_csv([row(item_name='x' * 2001)]), as_csv([row()]) + ',extra\n',
                 'x' * 2_000_001, as_csv([row()] * 5001), as_csv([row()]).replace('quantity,', 'order_id,')]
        for text in cases:
            with self.subTest(length=len(text)), self.assertRaises(ValueError):
                parse_orders(text)

    def test_optional_header_and_timestamp_errors(self):
        self.assertTrue(self.parse(row(source_updated_at='no-time'))[0]['errors'])
        self.assertTrue(self.parse(row(), row(line_id='L2', kol_ref='different'))[0]['errors'])

    def test_utf8_bom_is_accepted(self):
        self.assertEqual(parse_orders('\ufeff' + as_csv([row()]))[1][0]['errors'], [])


if __name__ == '__main__':
    unittest.main()
