'use strict';

function formatAmount(value) {
  if (typeof value !== 'string' || !/^-?\d+\.\d{2}$/.test(value)) throw new Error('無效金額格式');
  const [whole, fraction] = value.split('.');
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction}`;
}

function documentMatches(record,filters,terms=[]){
  const q=(filters.q||'').trim().toLocaleLowerCase();
  return (!filters.state||record.state===filters.state)&&(!filters.source||record.account_id===filters.source)&&(!q||[record.number||'',...terms].some(value=>String(value).toLocaleLowerCase().includes(q)));
}
function stockUnitMatches(unit,product,filters={}){
  const q=(filters.q||'').trim().toLocaleLowerCase();
  return (!filters.product||unit.product_id===filters.product)&&(!filters.location||unit.location===filters.location)&&
    (!filters.quality||unit.quality===filters.quality)&&(!filters.hold||unit.return_hold)&&
    (!q||[unit.id,product?.name,product?.sku,product?.barcode].some(v=>String(v||'').toLocaleLowerCase().includes(q)));
}
function stockDisplayState(unit){
  if(unit.location==='customer')return '出庫';
  if(unit.return_hold)return '退貨入庫';
  if(unit.transfer_id)return '調撥中';
  if(unit.storage_required&&!unit.place_id)return '未配櫃';
  if(unit.quality==='vendor_exchange')return '廠商換貨中';
  if(unit.quality==='other')return '其他';
  if(unit.quality==='damaged')return '毀損';
  if(unit.quality==='defective')return '瑕疵';
  if(unit.quality==='pending')return '待驗收';
  if(unit.location==='staging')return '準備中';
  if(unit.shipment_id)return '準備中';
  return '入庫';
}
function receiptProductPage(products,q='',page=1,size=25){
  const words=q.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const matches=products.filter(p=>p.kind==='product'&&Boolean(p.active)&&words.every(word=>[p.name,p.sku,p.barcode,p.specification,p.group_name].some(v=>String(v||'').toLocaleLowerCase().includes(word))));
  const pages=Math.max(1,Math.ceil(matches.length/size)),current=Math.max(1,Math.min(page,pages));
  return {items:matches.slice((current-1)*size,current*size),total:matches.length,pages,page:current};
}
function findShipmentOrder(orders, number){
  const value=number.trim();
  if(!value)throw new Error('請輸入原訂單編號。');
  const matches=orders.filter(o=>o.order_id===value&&o.status==='active');
  if(!matches.length)throw new Error('找不到有效的原訂單，請核對編號或先接收訂單。');
  if(matches.length>1)throw new Error('不同來源有相同訂單編號，請從訂單詳情建立出貨單。');
  return matches[0];
}
function receiptPutaway(receipt,units){
  if(receipt.state!=='confirmed')return {state:'not_received',label:'尚未入庫',pending:0};
  const own=units.filter(u=>u.receipt_id===receipt.id);
  const pending=own.filter(u=>u.location==='warehouse'&&!u.place_id).length;
  const placed=own.filter(u=>Boolean(u.place_id)).length;
  if(pending)return {state:placed?'partial':'unassigned',label:placed?'部分配櫃':'未配櫃',pending};
  return own.length&&placed===own.length?{state:'complete',label:'已配櫃',pending:0}:{state:'none',label:'無待配櫃',pending:0};
}
if (typeof module !== 'undefined') module.exports = { formatAmount, documentMatches, stockUnitMatches, receiptProductPage, stockDisplayState, findShipmentOrder, receiptPutaway };
