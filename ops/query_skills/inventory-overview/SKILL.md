---
name: inventory-overview
description: 查商品種類、目前庫存與可出貨量、逐件庫存、單據、櫃位、調撥或庫存期間報表；包含商品分頁追問。
---

# 庫存概況與明細配方

使用 inventory，account_id 固定空字串。search 可填已確認商品名稱／SKU／條碼／ID／單號／櫃位，不猜識別碼；預設 search=''、status=all、offset=0、limit=50、as_of=''。

## 概況與分頁

- 商品種類、在庫、可出貨及各商品數量先查一次 summary；products 具相同總計與明細，相同條件不要兩者都查。product_count 是單品主檔SKU數（含零庫存、不含套組），in_stock_sku_count 是有庫存SKU數，on_hand 是在庫，available 是一般品可出貨。
- summary/products 的 totals 涵蓋全部符合篩選商品，rows 只為本頁；問全庫SKU總數不能只查 status=in_stock。只列有庫存商品且同時問全部總計時，先用 all 的結果；若本頁不足以列全，再說明分頁，不能宣稱已完整列出。
- 明確指定每頁筆數用 limit（1–50）；下一頁沿歷史已確認的篩選、limit 與已顯示列範圍，把 offset 改成已顯示最後一筆的位置。上下文不足或多份清單無法識別時先澄清，不猜頁數。新期間或商品條件應重設 offset=0。
- 回答標示「顯示第N–M筆，共T筆」，不把本頁列數當全部。零結果明示零筆，不稱第1–0筆；各狀態可能重疊，不相加為總數。

## 視圖與條件

- summary/products/units/locations 是目前狀態，start/end 留空。summary/products 支援 all/in_stock/available/unassigned/return_held/defective/damaged/packaging_damaged/allocated/transferring；products 另有 active/inactive。
- units 逐件：status 支援 all/warehouse/staging/customer/available/unassigned/return_held/allocated/transferring/good/pending/defective/damaged/vendor_exchange/other/packaging_damaged。all 含已出庫件，不能當在庫總數。
- receipts 入庫單、shipments 出貨單、transfers 調撥單按建立日篩選。receipts 狀態 draft/ordered/confirmed/cancelled；shipments 為 draft/allocated/picked/staged/shipped/cancelled；transfers 為 draft/moving/complete/cancelled，均可 all。
- events 異動、cost_history 逐件成本修訂按異動日；locations 櫃位為目前狀態。這三種 status=all。
- report 查期間入出退貨、低庫存與支應天數；report_daily/report_monthly 查期間日／月統計，status=all。按全部或單一明確SKU查，不猜多商品合併。未指定期間沿報表預設截至昨日近30天，須按證據明示。
- 僅 report/report_daily/report_monthly 可用 as_of 指定含時區歷史時點（例如 2026-10-31T23:59:59+08:00）。其庫存為 as_of 時點，異動為事件期間；缺歷史涵蓋不能說完整，沒低庫存門檻或期間不足不能推估補貨。

只按問題加查入庫單或櫃位，不因查概況固定額外查所有視圖。成本 null 不當零，cents 為分、amount 為元；櫃位未啟用時，未配櫃不直接等於不可出貨。回答目前庫存須重新查，不能沿用歷史答案或出貨數推算。
