'use strict';
function statusTone(label){
 const text=String(label||'');
 if(/毀損|損毀|瑕疵|錯誤|失敗|待修正/.test(text))return 'danger';
 if(/盒損|退貨|待驗|未配櫃|待到貨|已訂貨|部分/.test(text))return 'warning';
 if(/取消|停用|排除|刪除/.test(text))return 'muted';
 if(/準備|調撥中|占用|待履約|待出貨|處理中|揀貨/.test(text))return 'info';
 if(/完成|入庫|出庫|良品|一般品|正常|啟用|有效|已收貨|已確認|已配櫃/.test(text))return 'success';
 return 'neutral';
}
function statusBadge(label){
 return node('span',label||'—','status-label status-'+statusTone(label));
}
function statusCell(label){const cell=node('td');cell.append(statusBadge(label));return cell;}
