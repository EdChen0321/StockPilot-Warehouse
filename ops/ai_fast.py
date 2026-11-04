"""Closed, full-utterance grammar for unambiguous read-only questions.

Never infer a product, time range or follow-up scope from keyword fragments.
Unsupported wording falls back to the existing model flow.
"""
import re


def fixed_plan(question, history, calendar):
    if history:
        return None
    text = question.strip().rstrip('？?。').strip()
    base = dict(start='', end='', status='all', account_id='')
    if text in ('未出完貨訂單', '未出貨訂單', '目前有哪些訂單還沒出完貨', '目前有哪些訂單還沒出完貨？先列 5 筆'):
        return 'order-list', [dict(base, tool='orders', view='unshipped', status='active', offset=0, limit=5)]
    if text in ('目前庫存總量是多少', '目前有多少庫存', '查詢目前庫存總量'):
        return 'inventory', [dict(base, tool='inventory', view='summary')]
    match = re.fullmatch(r'SKU「([A-Za-z0-9][A-Za-z0-9_.+/-]{0,99})」目前庫存是多少', text)
    if match:
        return 'sku', [dict(base, tool='inventory', view='products', search=match[1])]
    if text in ('本月有效訂單有幾筆', '這個月有效訂單有幾筆'):
        return 'orders', [dict(base, tool='orders', view='summary', status='active', **calendar['this_month'])]
    if text == '目前未出貨的有效訂單有幾筆':
        return 'unshipped', [dict(base, tool='orders', view='unshipped', status='active')]
    if text in ('昨天下單未出完貨', '昨天有哪些訂單還沒出完貨', '昨天有哪些訂單還沒出貨', '列出昨天下單、目前還沒出完貨的訂單'):
        return 'yesterday-unshipped', [dict(base, tool='orders', view='unshipped', status='active', **calendar['yesterday'])]
    if text == '比較本月與上月有效訂單筆數':
        return 'comparison', [dict(base, tool='orders', view='summary', status='active', **calendar[period])
                              for period in ('this_month', 'last_month')]
    return None


def fixed_answer(kind, results):
    result = results[0]
    if kind == 'order-list':
        return f'目前符合條件的未出完貨訂單共 {result["unshipped_order_count"]:,} 筆（含尚未出貨與部分出貨，排除取消單）。'
    if kind == 'yesterday-unshipped':
        return '以下按台北時間的昨天下單日期篩選，出貨進度以本次查詢時為準。'
    if kind in ('inventory', 'sku'):
        if kind == 'sku':
            search = result['filters']['search']
            rows = [row for row in result['rows'] if row['sku'].casefold() == search.casefold()]
            if len(rows) != 1 or result['total_rows'] != 1:
                return f'無法唯一確認 SKU「{search}」。請確認完整 SKU，或提供商品名稱以進一步查詢。'
            counts = rows[0]
            subject = f'SKU「{search}」'
        else:
            counts = result['totals']
            subject = '目前全部商品'
        return (f'{subject}在庫 {counts["on_hand"]:,} 件，可出貨 {counts["available"]:,} 件。'
                '\n範圍：目前工作庫的庫存狀態；可出貨為在庫的一部分，不另加總。')
    if kind == 'unshipped':
        return (f'目前有 {result["unshipped_order_count"]:,} 筆有效訂單尚未完成出貨。'
                '\n範圍：工作庫全部下單日期；有效訂單實體需求減已完成出貨，排除取消單。實退不自動形成補寄需求，未判定缺貨原因。')
    def period(row):
        return f'{row["filters"]["start"]}～{row["filters"]["end"]}'
    if kind == 'orders':
        return f'本月有效訂單 {result["order_count"]:,} 筆。\n期間：{period(result)}，按台北下單日期，排除取消單。'
    previous = results[1]
    delta = result['order_count'] - previous['order_count']
    return (f'本月（{period(result)}）：{result["order_count"]:,} 筆。\n'
            f'上月（{period(previous)}）：{previous["order_count"]:,} 筆。\n'
            f'本月比上月差額 {delta:+,} 筆。\n'
            '按台北下單日期，排除取消單。本月至今日對比上月完整曆月，並非等長期間；不據此推論成長趨勢。')
