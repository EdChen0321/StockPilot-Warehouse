---
name: order-comparison
description: 查營運訂單數、金額、UTM 或未完成出貨，包含本月與上月比較；不代替實際出貨或獨立 KOL 分析。
---

# 訂單與月度比較配方

使用固定 orders 工具，view 為 summary／utm／unshipped。search、as_of 空字串，offset 預設0、limit預設50；unshipped 可指定每頁1–50筆。account_id 未指定留空，不把 UTM 名稱當 account_id。指定來源但無已確認 ID 時澄清。

- 訂單數／金額用 summary；UTM medium 分組用 utm；未完成出貨用 unshipped。
- 預設 status=active，明示排除取消；問包含取消的總訂單用 all，要看取消另用 cancelled。不要每題固定查三種狀態。
- 本月與上月訂單金額比較：依伺服器 calendar_context 的 this_month／last_month，各查一次 summary，同一 status／account_id。本月是月初至今日，上月是完整曆月；明示天數範圍不同，不能直接當同長度成長率。明確期間優先，未指定期間 start/end 空字串查全部已記錄訂單。
- 每單總額只算一次，非商品小計、實收或毛利。order_amount=null 時總額未知，列 known_amount 與 missing_amount_count，不用已知小計當完整總額。前期為零或缺值時，不給百分比成長率。
- unshipped 是有效實體需求減完成出貨，包含部分出貨，不代表逾期或缺貨。昨天預設指台北時間昨天下單、目前尚未完成出貨；使用 yesterday 的起訖日期。清單按下單時間、來源、編號排序。下一頁沿用日期／狀態／來源／limit，offset 加上一頁 limit；每次查詢最新資料，不聲稱跨頁固定快照。程式會呈現實際訂單編號、狀態、件數與原單入口，模型不重複匿名清單。
- 「賣多少」未確認口徑時澄清訂單金額、商品銷量或實際出貨。混合訂單／出貨問題分別用 orders 和 shipping，兩者日期及母體不等同。

輸出比較時以期間、有效訂單數、訂單總額、缺漏為主；所有數字取本次證據，追問也重新查。
