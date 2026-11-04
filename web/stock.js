'use strict';
let stockData = {products:[], units:[], receipts:[], groups:[], shipments:[], events:[]};
let orderData = [];
let stockSequence = 0;
let stockLoadPromise=null,stockProductsPromise=null,stockProductsReady=false;
const stockAreas=['stock','shipping','orders','inventory','quality','stock-reports','locations','transfers','putaway'];
async function ensureStockLoaded(){
  if(stockLoaded)return;
  if(!stockLoadPromise)stockLoadPromise=refreshStock().finally(()=>{stockLoadPromise=null;});
  return stockLoadPromise;
}
async function loadStockProducts(){
  if(stockProductsReady||stockLoaded)return;
  if(!stockProductsPromise)stockProductsPromise=request('/api/stock/products').then(data=>{
    if(!stockLoaded)stockData.products=data.products;
    stockProductsReady=true;renderSkuStock();
  }).finally(()=>{stockProductsPromise=null;});
  return stockProductsPromise;
}
const loadedStockHistory = new Set();
let stockHistorySequence=0;
async function loadStockHistory(path=location.hash.slice(1)) {
  const [area,rawId] = path.split('/');
  const scope = {shipping:'shipment',quality:'unit',stock:'receipt'}[area];
  if(!scope||!rawId||['new','by-product'].includes(rawId))return;
  const id=decodeURIComponent(rawId),key=scope+'/'+id,sequence=stockSequence;
  if(loadedStockHistory.has(key))return;
  const historySequence=++stockHistorySequence;
  const data=await request('/api/stock/history/'+scope+'/'+encodeURIComponent(id));
  if(sequence!==stockSequence||historySequence!==stockHistorySequence||location.hash.slice(1)!==path)return;
  for(const field of ['events','cost_revisions','unit_cost_revisions']){
    stockData[field]=data[field];
  }
  loadedStockHistory.clear();
  loadedStockHistory.add(key);
}
let selectedShipment = null;
let selectedOrder = null;
let selectedReceipt = null;
let editingReceipt=null;
const orderKey = order => JSON.stringify([order.account_id, order.order_id]);
for(const id of ['order-number']){const input=$(id);input.required=false;input.readOnly=true;input.placeholder='保存後自動產生年月日＋六位數';}
const selectedSourceOrder = () => orderData.find(o => orderKey(o) === $('shipment-order').value);
function shipmentState(state){return ['draft','allocated','picked','staged'].includes(state)?'preparing':state==='shipped'?'complete':state==='cancelled'?'cancel':state;}
const stockStates = {draft:'草稿', preparing:'準備中', complete:'完成', cancel:'取消', ordered:'已訂貨／待到貨', confirmed:'已入庫', allocated:'準備中', picked:'準備中', staged:'準備中', shipped:'完成', cancelled:'已取消', active:'待履約'};
const centsText = cents => `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2,'0')}`;
function stockMessage(area, text, error=false) { const el=$(area)?.hidden?$('operation-notice')||$(area+'-notice'):$(area+'-notice'); el.hidden=!text; el.textContent=text; el.className=error?'notice error':'notice';return el; }
async function stockAction(area, control, work) {
  control.disabled=true;
  try { await work(); } catch(error) { stockMessage(area,error.message,true).scrollIntoView({block:'nearest'}); }
  finally { control.disabled=false; }
}
function stockButton(area, label, work) { const b=node('button',label,'table-button'); b.type='button'; b.addEventListener('click',()=>stockAction(area,b,work)); return b; }
function productName(id) { return boot.products.find(p=>p.id===id)?.name || id; }
function lineNames(lines) { return lines.map(x=>`${x.line_type==='non_stock'?`${x.item_name}（非庫存）`:productName(x.product_id)} × ${x.quantity}`).join('、'); }
function documentSearchTerms(lines){return lines.flatMap(line=>{const p=boot.products.find(p=>p.id===line.product_id);return [p?.name||'',p?.sku||'',p?.barcode||''];});}
function receiptProductCell(lines){
  const cell=node('td');
  for(const line of lines){const product=boot.products.find(p=>p.id===line.product_id);cell.append(node('div',product?`${product.name} / ${product.sku||product.id}`:line.product_id));}
  return cell;
}
for(const [id,area] of [['receipt-search-form','stock'],['shipment-search-form','shipping']])$(id).addEventListener('submit',e=>{e.preventDefault();stockAction(area,e.submitter,renderStockLists);});
function newLine(container, pricing=true, item={}) {
  if(container==='receipt-lines'&&!item.product_id)return;
  const wrap=node('div',undefined,'document-line'); const label=node('label','商品'); const select=node('select'); select.required=true;
  if(container==='order-lines'&&item.line_type==='non_stock'){
    wrap.dataset.lineType='non_stock';label.textContent='非庫存明細名稱';
    const name=node('input');name.required=true;name.maxLength=200;name.dataset.field='item_name';name.value=item.item_name||'';label.append(name);
  }else if(container==='receipt-lines'){
    const p=boot.products.find(p=>p.id===item.product_id);select.append(new Option(p?.name||item.product_id,item.product_id));select.value=item.product_id;select.hidden=true;
    label.append(node('strong',p?.name||item.product_id),node('span',[p?.sku,p?.specification].filter(Boolean).join(' · '),'muted'),select);wrap.classList.add('receipt-item');
  }else{
    select.append(new Option('選擇已建立商品',''));
    for(const p of boot.products.filter(p=>p.active!==0))select.append(new Option(`${p.name} · ${p.sku}`,p.id));select.value=item.product_id||'';label.append(select);
  }
  const qtyLabel=node('label','數量'); const qty=node('input'); qty.type='number'; qty.min='1'; qty.max='10000'; qty.step='1'; qty.required=true; qty.value=item.quantity||1; qty.dataset.field='quantity'; qtyLabel.append(qty);
  wrap.append(label,qtyLabel);
  if(pricing) { const isReceipt=container==='receipt-lines'; const priceLabel=node('label',isReceipt?'進貨單價（可待補）':'銷售單價'); const price=node('input'); price.inputMode='decimal'; price.required=!isReceipt; price.value=item.unit_price_cents!=null?centsText(item.unit_price_cents):isReceipt?'':'0.00'; price.dataset.field='price'; priceLabel.append(price); wrap.append(priceLabel); }
  const remove=node('button','移除','secondary'); remove.type='button'; remove.addEventListener('click',()=>wrap.remove()); wrap.append(remove); $(container).append(wrap);
}
function documentLines(id) { return [...$(id).children].map(row=>{const item=row.dataset.lineType==='non_stock'?{line_type:'non_stock',item_name:row.querySelector('[data-field=item_name]').value}:{product_id:row.querySelector('select').value};item.quantity=Number(row.querySelector('[data-field=quantity]').value); const price=row.querySelector('[data-field=price]'); if(price)item.unit_price=price.value; return item;}); }
function openReceiptPicker(){
  const dialog=node('dialog',undefined,'receipt-picker');dialog.setAttribute('aria-labelledby','receipt-picker-title');
  const heading=node('header',undefined,'document-heading');heading.append(node('h2','選擇進貨商品'));heading.querySelector('h2').id='receipt-picker-title';const close=node('button','關閉','secondary');close.type='button';close.onclick=()=>dialog.close();heading.append(close);dialog.append(heading);
  const searchForm=node('form',undefined,'search-bar'),searchLabel=node('label','搜尋商品'),search=node('input');search.type='search';search.placeholder='名稱、SKU、條碼或規格';searchLabel.append(search);const searchButton=node('button','搜尋');searchButton.type='submit';searchForm.append(searchLabel,searchButton);dialog.append(searchForm);
  const tableWrap=node('div',undefined,'table-scroll'),table=node('table'),thead=node('thead'),tr=node('tr'),tbody=node('tbody');for(const title of ['選擇','SKU','商品／規格','條碼'])tr.append(node('th',title));thead.append(tr);table.append(thead,tbody);tableWrap.append(table);dialog.append(tableWrap);
  const pager=node('div',undefined,'picker-pagination'),footer=node('footer',undefined,'picker-footer'),count=node('span'),add=node('button','加入進貨單');count.setAttribute('role','status');add.type='button';footer.append(count,add);dialog.append(pager,footer);
  const selected=new Set(),existing=new Set(documentLines('receipt-lines').map(x=>x.product_id));let page=1,query='';
  function updateCount(){count.textContent=`已選 ${selected.size} 項`;add.disabled=selected.size===0;}
  function render(){
    const result=receiptProductPage(boot.products,query,page);page=result.page;tbody.replaceChildren();
    for(const p of result.items){const row=node('tr'),choose=node('td'),label=node('label'),check=node('input');check.type='checkbox';check.checked=selected.has(p.id);check.disabled=existing.has(p.id);check.setAttribute('aria-label',`選擇 ${p.name} ${p.sku}`);label.className='picker-check';label.append(check,node('span',existing.has(p.id)?'已加入':''));choose.append(label);const name=node('td',p.name);if(p.specification)name.append(node('small',p.specification));row.append(choose,node('td',p.sku),name,node('td',p.barcode||'—'));check.onchange=()=>{if(check.checked)selected.add(p.id);else selected.delete(p.id);updateCount();};tbody.append(row);}
    if(!result.total)emptyRow(tbody,4,'沒有符合條件的啟用單品。請調整搜尋，或先至商品庫建立商品。');
    pager.replaceChildren(node('span',`${result.total} 項 · 第 ${page} / ${result.pages} 頁`));
    for(const [text,step] of [['上一頁',-1],['下一頁',1]]){const b=node('button',text,'secondary');b.type='button';b.disabled=page+step<1||page+step>result.pages;b.onclick=()=>{page+=step;render();tableWrap.scrollTop=0;};pager.append(b);}
    const all=node('button','勾選本頁','secondary');all.type='button';all.onclick=()=>{for(const p of result.items)if(!existing.has(p.id))selected.add(p.id);render();};pager.append(all);updateCount();
  }
  searchForm.onsubmit=e=>{e.preventDefault();query=search.value;page=1;render();};
  add.onclick=()=>{
    const current=new Set(documentLines('receipt-lines').map(x=>x.product_id));let added=0;
    for(const id of selected){const p=boot.products.find(p=>p.id===id&&p.kind==='product'&&Boolean(p.active));if(p&&!current.has(id)){newLine('receipt-lines',true,{product_id:id});current.add(id);added++;}}
    if(added){const input=$('receipt-lines').querySelector('input');input?.dispatchEvent(new Event('change',{bubbles:true}));}
    dialog.close();stockMessage('stock',added?`已加入 ${added} 項商品，請填本次數量與進價。${selected.size>added?` ${selected.size-added} 項已停用或已加入，未新增。`:''}`:'所選商品已停用或已加入，未新增明細。');
  };
  const closeOnNavigate=()=>dialog.close();window.addEventListener('hashchange',closeOnNavigate);
  dialog.addEventListener('close',()=>{window.removeEventListener('hashchange',closeOnNavigate);dialog.remove();$('receipt-add').focus();},{once:true});document.body.append(dialog);render();dialog.showModal();search.focus();
}
async function refreshStock() {
  const sequence=++stockSequence; const [stock,orders]=await Promise.all([request('/api/stock?summary=1'),request('/api/orders')]); if(sequence!==stockSequence)return;
  stockData=stock; orderData=orders.orders;loadedStockHistory.clear();
  await loadStockHistory();if(sequence!==stockSequence)return;
  options('order-account',boot.accounts.map(a=>({value:a.id,label:a.name})),'請選擇來源');
  options('order-filter-account',boot.accounts.map(a=>({value:a.id,label:a.name})),'全部來源');
  options('shipment-filter-source',[...new Set(stock.shipments.map(s=>s.channel).filter(Boolean))].map(channel=>({value:channel,label:channel})),'全部通路');
  await renderStockLists();
  stockLoaded=true;stockProductsReady=true;
  window.dispatchEvent(new Event('stock-refreshed'));
}
async function renderStockLists(){
  const stock=stockData,area=location.hash.slice(1).split('/')[0]||'catalogue';
  if(area==='inventory')renderInventory();
  if(area==='stock'){
  const receipts=$('receipt-rows');receipts.replaceChildren();
  const receiptFilters={q:$('receipt-search').value,state:$('receipt-filter-state').value,putaway:$('receipt-filter-putaway').value};
  const matchingReceipts=stock.receipts.filter(r=>{
    if(!documentMatches(r,receiptFilters,[r.source,r.comment,...documentSearchTerms(r.lines)]))return false;
    const putaway=receiptPutaway(r,stock.units),filter=receiptFilters.putaway;
    return !(filter==='pending'?!putaway.pending:filter&&filter!==putaway.state);
  });
  for(const r of pageItems('receipt-rows',matchingReceipts,renderStockLists,JSON.stringify(receiptFilters))){
    const putaway=receiptPutaway(r,stock.units);
    const row=node('tr');row.dataset.recordId=r.id;const controls=node('td');
    controls.append(stockButton('stock','查看',async()=>{navigatePage('stock/'+encodeURIComponent(r.id));}));
    if(putaway.pending)controls.append(stockButton('stock','配櫃',async()=>{navigatePage('putaway/'+encodeURIComponent(r.id));}));
    const placement=statusCell(putaway.label),receiptState=statusCell(stockStates[r.state]);if(receiptCosts(r).total===null)receiptState.append(node('small','待填費用'));
    row.append(node('td',r.number),node('td',r.source||'—'),node('td',r.lines.reduce((n,x)=>n+x.quantity,0)),receiptState,placement,node('td',dateTime(r.created_at)),node('td',r.comment||'—'),controls);receipts.append(row);
  }
  if(selectedReceipt&&location.hash==='#stock/'+encodeURIComponent(selectedReceipt)&&!window.pageHasUnsavedChanges?.())showReceipt(selectedReceipt);
  if(!receipts.children.length)emptyRow(receipts,8,stock.receipts.length?'沒有符合條件的入庫單。':'尚無入庫單。草稿保存後，需確認實際入庫。');
  }
  if(area==='orders'){if(!location.hash.slice(1).includes('/'))await refreshOrderList();if(selectedOrder&&location.hash==='#orders/'+encodeURIComponent(selectedOrder)&&!window.pageHasUnsavedChanges?.())showOrder(selectedOrder);}
  if(area==='shipping'){
  if(!location.hash.slice(1).includes('/'))await refreshShipmentList();
  if(selectedShipment&&location.hash.startsWith('#shipping/'+encodeURIComponent(selectedShipment))&&!window.pageHasUnsavedChanges?.())showShipment(selectedShipment);
  if(selectedOrder&&location.hash==='#orders/'+encodeURIComponent(selectedOrder)&&!window.pageHasUnsavedChanges?.())showOrder(selectedOrder);

  }
}
const remotePages={orders:{page:1,key:''},shipping:{page:1,key:''}};
let orderSearchSequence=0,shipmentSearchSequence=0;
function remotePageQuery(area,filters){
  const key=JSON.stringify(filters),state=remotePages[area];
  if(state.key!==key){state.page=1;state.key=key;}
  return new URLSearchParams({...filters,page:String(state.page)});
}
function renderRemotePager(bodyId,area,result,refresh){
  const state=remotePages[area];state.page=result.page;
  let pager=$(bodyId+'-pager');
  if(!pager){pager=node('div',undefined,'form-actions');pager.id=bodyId+'-pager';$(bodyId).closest('.table-scroll').after(pager);}
  pager.replaceChildren(node('span',`${result.total} 筆 · 第 ${result.page} / ${result.pages} 頁`));
  for(const [label,step] of [['上一頁',-1],['下一頁',1]]){
    const b=button(label,async()=>{state.page=result.page+step;await refresh();});
    b.disabled=result.page+step<1||result.page+step>result.pages;pager.append(b);
  }
}
async function refreshOrderList(){
  const sequence=++orderSearchSequence;
  options('order-filter-account',boot.accounts.map(a=>({value:a.id,label:a.name})),'全部來源');
  const params=remotePageQuery('orders',{q:$('order-search').value,status:$('order-filter-status').value,account_id:$('order-filter-account').value});
  const result=await request('/api/orders?'+params);if(sequence!==orderSearchSequence)return;
  const rows=$('order-rows');rows.replaceChildren();
  for(const o of result.orders){const row=node('tr');row.dataset.recordId=orderKey(o);row.classList.toggle('selected-record',selectedOrder===orderKey(o));const controls=node('td');controls.append(stockButton('orders','查看',async()=>{navigatePage('orders/'+encodeURIComponent(orderKey(o)));}));row.append(node('td',`${boot.accounts.find(a=>a.id===o.account_id)?.name||o.platform} / ${o.order_id}`),node('td',lineNames(o.lines)),node('td',formatAmount(centsText(o.amount_cents)),'numeric'),statusCell(o.status==='active'?'有效':'取消'),controls);rows.append(row);}
  if(!result.orders.length)emptyRow(rows,5,'沒有符合條件的訂單。');
  renderRemotePager('order-rows','orders',result,refreshOrderList);
}
async function refreshShipmentList(){
  const sequence=++shipmentSearchSequence;
  const params=remotePageQuery('shipping',{q:$('shipment-search').value,state:$('shipment-filter-state').value,source:$('shipment-filter-source').value});
  const result=await request('/api/stock/shipments?'+params);if(sequence!==shipmentSearchSequence)return;
  options('shipment-filter-source',result.channels.map(value=>({value,label:value})),'全部通路');
  const rows=$('shipment-rows');rows.replaceChildren();
  for(const shipment of result.shipments){
    const row=node('tr');row.dataset.recordId=shipment.id;const title=node('td',shipment.number);
    title.append(node('small',shipment.parent_id?'副單':'主單'));const controls=node('td');
    controls.append(stockButton('shipping','查看',async()=>navigatePage('shipping/'+encodeURIComponent(shipment.id))));
    if(!shipment.parent_id)controls.append(stockButton('shipping','建立副單',async()=>openSubShipment(shipment)));
    row.append(title,statusCell(stockStates[shipmentState(shipment.state)]),node('td',shipment.channel||'—'),node('td',shipment.first_completed_at?dateTime(shipment.first_completed_at):'尚未完成'),node('td',shipment.comment||'—'),controls);rows.append(row);
  }
  if(!rows.children.length)emptyRow(rows,6,'沒有符合條件的出貨單。');
  renderRemotePager('shipment-rows','shipping',result,refreshShipmentList);
}
$('order-search-form').addEventListener('submit',event=>{event.preventDefault();stockAction('orders',event.submitter,refreshOrderList);});
function remainingLines(order) {
  if (!order || order.status !== 'active') return [];
  const quantities={};
  for(const line of order.lines.filter(l=>l.line_type!=='non_stock'))for(const part of line.components?.length?line.components:[{product_id:line.product_id,quantity:1}])quantities[part.product_id]=(quantities[part.product_id]||0)+line.quantity*part.quantity;
  const group=stockData.groups.find(g=>g.account_id===order.account_id&&g.order_id===order.order_id);
  for(const addition of stockData.additions||[])if(addition.account_id===order.account_id&&addition.order_id===order.order_id)for(const [pid,q] of Object.entries(addition.requirements))quantities[pid]=(quantities[pid]||0)+q;
  for(const s of stockData.shipments.filter(s=>s.group_id===group?.id&&['allocated','picked','staged','shipped'].includes(s.state)))for(const line of s.lines)quantities[line.product_id]-=line.quantity;
  return Object.entries(quantities).filter(([,q])=>q>0).map(([product_id,quantity])=>({product_id,quantity}));
}
function prepareShipment(key, routed=false) {
  if(!routed&&!location.hash.startsWith('#shipping/new'))return navigatePage('shipping/new/'+encodeURIComponent(key));
  $('shipment-order').value=key;
  const order=selectedSourceOrder();
  $('shipment-order-number').value=order?.order_id||'';$('shipment-order-number').readOnly=false;
  const group=order&&stockData.groups.find(g=>g.account_id===order.account_id&&g.order_id===order.order_id);
  const parent=group&&stockData.shipments.find(s=>s.group_id===group.id&&!s.parent_id);
  $('shipment-editor').querySelector('h2').textContent=parent?`建立副單 · 主單 ${parent.number}`:'建立出貨單';
  $('shipment-save').textContent=parent?'保存副單':'保存出貨單';

  const lines=remainingLines(order);
  const source=order&&boot.accounts.find(a=>a.id===order.account_id);
  $('shipment-origin').replaceChildren();if(order)$('shipment-origin').append(stockFacts([['訂單來源',source?.name],['單據類型',parent?'副單':'主單']]));
  const parentPanel=$('shipment-parent');parentPanel.replaceChildren();parentPanel.hidden=!parent;
  if(parent){$('shipment-order-number').readOnly=true;$('shipment-order').dataset.parentId=parent.id;}else delete $('shipment-order').dataset.parentId;
  if(parent)parentPanel.append(node('h3','主單資訊','document-section-title'),stockFacts([['主單編號',parent.number],['主單狀態',stockStates[shipmentState(parent.state)]],['建立時間',dateTime(parent.created_at)]]));
  if(!$('shipment-channel').value)$('shipment-channel').value=parent?.channel||source?.name||'';
  $('shipment-channels').replaceChildren(...[...new Set(stockData.shipments.map(s=>s.channel).filter(Boolean))].map(value=>new Option(value,value)));
  $('shipment-empty').hidden=lines.length>0;
  $('shipment-empty').textContent=order?'此訂單已全部配貨或出貨。請返回出貨列表查看既有單據；需要重排時先取消未出貨單。':'請輸入並核對原訂單編號。';
  $('shipment-save').disabled=!lines.length;
  $('shipment-editor').open=true;
  $('shipment-detail').hidden=true;
  if(lines.length)$('shipment-order-number').focus();
}
function openSubShipment(shipment){navigatePage('shipping/new/parent/'+encodeURIComponent(shipment.parent_id||shipment.id));}
function prepareSubShipment(id){
  const parent=stockData.shipments.find(s=>s.id===id);if(!parent)throw new Error('找不到主單');
  $('shipment-save').disabled=false;$('shipment-empty').hidden=true;
  $('shipment-order').dataset.parentId=parent.id;$('shipment-order-number').value='';$('shipment-order-number').readOnly=false;
  $('shipment-number-label').textContent='副單條碼';$('shipment-order-number').placeholder='輸入副單條碼';
  $('shipment-channel').value=parent.channel||'';$('shipment-editor').querySelector('h2').textContent='建立副單';$('shipment-save').textContent='建立副單';
  $('shipment-parent').hidden=false;$('shipment-parent').replaceChildren(stockFacts([['主單',parent.number],['狀態',stockStates[shipmentState(parent.state)]]]));
}
function showOrder(key) {
  selectedOrder=key;for(const row of $('order-rows').children)row.classList.toggle('selected-record',row.dataset.recordId===key);
  const order=orderData.find(o=>orderKey(o)===key);const panel=$('order-detail');if(!order)return;
  panel.hidden=false;panel.classList.add('order-document');panel.replaceChildren();
  const heading=node('header',undefined,'order-document-heading'),identity=node('div'),actions=node('div',undefined,'form-actions');
  identity.append(node('h2',order.order_id),statusBadge(order.status==='active'?'有效訂單':'已取消'));heading.append(identity,actions);panel.append(heading);
  const money=value=>value==null?'未提供':formatAmount(centsText(value))+' 元';
  panel.append(stockFacts([['訂單來源',boot.accounts.find(a=>a.id===order.account_id)?.name||'—'],['下單時間',order.ordered_at?dateTime(order.ordered_at):'未提供'],['商品金額',money(order.amount_cents)],['訂單總金額',money(order.order_total_cents)]]));
  const items=node('section',undefined,'order-section');items.append(node('h3','訂購商品'),materialTable(['商品','SKU','數量','商品金額'],order.lines.map(line=>[line.line_type==='non_stock'?line.item_name:productName(line.product_id),boot.products.find(p=>p.id===line.product_id)?.sku||'—',line.quantity,money(line.amount_cents)])));panel.append(items);
  const attribution=node('p',`來源標記（UTM medium）：${order.utm_medium||'未提供'}`,'statistics-note');panel.append(attribution);
  const group=stockData.groups.find(g=>g.account_id===order.account_id&&g.order_id===order.order_id);
  const related=stockData.shipments.filter(s=>s.group_id===group?.id);
  if(related.length){const links=node('section',undefined,'order-section');links.append(node('h3','相關出貨單'));const list=node('div',undefined,'order-related-links');for(const shipment of related){const a=node('a',shipment.number+' · '+(stockStates[shipment.state]||shipment.state),'button secondary');a.href='#shipping/'+encodeURIComponent(shipment.id);list.append(a);}links.append(list);panel.append(links);}
  if(order.status==='active'){
    const create=stockButton('orders','建立出貨單',async()=>prepareShipment(key));create.className='button';create.disabled=!remainingLines(order).length;actions.append(create);
    if(create.disabled)panel.append(node('p','商品已全部配貨或出貨；可由相關出貨單查看處理進度。','statistics-note'));
    const cancellation=node('section',undefined,'order-cancellation');cancellation.append(node('h3','取消訂單'),node('p','取消後不計入有效銷售，並釋放未出貨占用；已出貨紀錄保留。實際退回商品需另外辦理退貨入庫。'));
    const cancel=stockButton('orders','取消這張訂單',async()=>{if(!confirm('確定取消這張訂單？取消後將排除銷售統計，已出貨紀錄仍保留。'))return;await request('/api/orders/cancel',{operation_id:operation(),account_id:order.account_id,order_id:order.order_id,expected_revision:order.revision});await refreshStock();await refreshSales();stockMessage('orders','訂單已取消；未出貨占用已釋放，已出貨紀錄保留。');});cancel.className='button danger';cancellation.append(cancel);panel.append(cancellation);
  }else panel.append(node('p','訂單已取消，不計入有效銷售；已出貨歷史仍保留。','notice'));
}
function stockDocumentHeader(panel,number,state){
  panel.classList.add('stock-document');panel.replaceChildren();
  const header=node('header',undefined,'document-heading'),identity=node('div',undefined,'document-identity'),badge=statusBadge(stockStates[state]||state);badge.dataset.state=state;
  identity.append(node('h2',number),badge);const actions=node('div',undefined,'document-actions');header.append(identity,actions);panel.append(header);return actions;
}
function stockFacts(entries){
  const grid=node('dl',undefined,'document-facts');for(const [label,value] of entries){const item=node('div'),detail=node('dd');if(value&&typeof value==='object')detail.append(value);else if(label.includes('狀態'))detail.append(statusBadge(value));else detail.textContent=value||'—';item.append(node('dt',label),detail);grid.append(item);}return grid;
}
let shipmentScanQueue=Promise.resolve();
function shipmentUnitAction(unit,shipment,mode){
  const returning=unit.location==='customer';
  const dialog=node('dialog',undefined,'receipt-picker return-dialog');dialog.setAttribute('aria-label',mode==='return'?'退貨入庫':'設為瑕疵');
  const form=node('form'),title=node('h2',mode==='return'?'退貨入庫':'設為瑕疵'),label=node('label','原因'),reason=node('textarea'),save=node('button','確認'),cancel=node('button','取消','secondary');reason.required=mode==='return';reason.maxLength=200;label.append(reason);save.type='submit';cancel.type='button';cancel.onclick=()=>dialog.close();form.append(title);if(mode==='return')form.append(label);
  if(returning){const check=node('input'),checkLabel=node('label','已收到這件實際退回的商品','checkbox');check.type='checkbox';check.required=true;checkLabel.prepend(check);form.append(checkLabel);}
  const returnPlace=returning?returnPlaceSelect(form):null;const actions=node('div',undefined,'form-actions');actions.append(save,cancel);form.append(actions);dialog.append(form);form.onsubmit=event=>{event.preventDefault();save.disabled=true;const body={operation_id:operation(),reason:mode==='defective'?'標記瑕疵':reason.value};if(returning){body.place_id=returnPlace?.value;body.shipment_id=shipment.id;body.quality=mode==='defective'?'defective':'good';}else{body.quality='defective';body.packaging=unit.packaging;}
    request(`/api/stock/units/${unit.id}/${returning?'return':'quality'}`,body).then(async()=>{dialog.close();await refreshStock();stockMessage('shipping',returning?'已記錄退貨入庫。':'已記錄瑕疵。');}).catch(error=>{save.disabled=false;let message=form.querySelector('[role=alert]');if(!message){message=node('p');message.setAttribute('role','alert');form.append(message);}message.textContent=error.message;});};
  dialog.addEventListener('close',()=>dialog.remove(),{once:true});document.body.append(dialog);dialog.showModal();if(mode==='return')reason.focus();else save.focus();
}
function receiptCosts(receipt){
  const goods=receipt.lines.some(l=>l.unit_price_cents==null)?null:receipt.lines.reduce((n,l)=>n+BigInt(l.unit_price_cents)*BigInt(l.quantity),0n);
  const freight=receipt.freight_cents==null?null:BigInt(receipt.freight_cents),tax=receipt.tax_cents===undefined?0n:receipt.tax_cents===null?null:BigInt(receipt.tax_cents);
  return {goods,freight,tax,total:receipt.cost_status==='pending'||[goods,freight,tax].includes(null)?null:goods+freight+tax};
}
function costDisplay(cents){
  if(cents==null)return '待填費用';const value=BigInt(cents);return formatAmount(`${value/100n}.${String(value%100n).padStart(2,'0')}`);
}
function stockCostSummary(units){
  const pending=units.filter(u=>u?.cost_cents==null).length,total=units.reduce((n,u)=>n+BigInt(u?.cost_cents??0),0n);
  return pending?`已知成本 ${costDisplay(total)} · 待填費用 ${pending} 件`:`成本合計 ${costDisplay(total)}`;
}
function shipmentTotals(shipment){
  const events=shipmentCycleEvents(shipment).current,eventIds=new Set(events.map(e=>e.id)),ships=shipmentEffectiveShips().filter(e=>e.shipment_id===shipment.id&&eventIds.has(e.id));
  if(ships.length){
    const returned=new Set(events.filter(e=>e.kind==='return').map(e=>e.details.ship_event_id));
    const remaining=ships.filter(e=>!returned.has(e.id));
    const units=remaining.map(e=>stockData.units.find(u=>u.id===e.unit_id));
    return `原出貨 ${ships.length} 件 · 已退貨 ${ships.length-remaining.length} 件 · 未退貨 ${remaining.length} 件 · 未退貨${stockCostSummary(units)}`;
  }
  const units=stockData.units.filter(u=>u.shipment_id===shipment.id);
  return `本單 ${units.length} 件 · ${stockCostSummary(units)}`;
}
function shipmentEffectiveShips(){
  const reversed=new Set(stockData.events.filter(e=>e.kind==='ship_reopen').map(e=>e.details.ship_event_id));
  return stockData.events.filter(e=>e.kind==='ship'&&!reversed.has(e.id));
}
function shipmentCycleEvents(shipment){
  const events=stockData.events.filter(e=>e.shipment_id===shipment.id),marker=events.findIndex(e=>e.kind==='cancel_reopen');
  return marker<0?{current:events,prior:[]}:{current:events.slice(0,marker),prior:events.slice(marker)};
}
function shipmentAuditState(event){
  if(event.kind==='cost_revision')return `成本：${costDisplay(event.details.before_cost_cents)} → ${costDisplay(event.details.after_cost_cents)}（版本 ${event.details.cost_version}）`;
  if(event.kind==='cancel_reopen')return '取消 → 準備中（僅重開單據／原商品不變）';
  const names={warehouse:'倉內',staging:'待寄',customer:'完成',good:'良品',pending:'待驗',defective:'瑕疵',damaged:'損毀',draft:'準備中',allocated:'準備中',shipped:'完成',cancelled:'取消'};
  const describe=value=>{if(value==null)return '未記錄';if(typeof value!=='object')return names[value]||String(value);const keys={location:'位置',quality:'品質',packaging:'包裝',shipment_id:'占用單',return_hold:'退貨入庫未完成',state:'單據狀態'};const parts=Object.entries(keys).filter(([key])=>Object.hasOwn(value,key)).map(([key,label])=>label+'：'+(names[value[key]]??String(value[key]??'無')));return parts.join('、')||'未記錄';};
  if(event.details.before!==undefined||event.details.after!==undefined)return describe(event.details.before)+' → '+describe(event.details.after);
  return ({allocate:'可用 → 本單占用',pick:'占用 → 已揀貨',deduct:'倉內 → 待寄',ship:'準備中 → 完成',ship_reopen:'完成 → 準備中（原件仍占用）',release:'本單占用 → 解除占用',return:'出庫 → 退貨入庫',return_release:'退貨入庫 → 入庫'}[event.kind]||'未記錄');
}
function renderShipmentHistory(panel,shipment){
  const labels={allocate:'配貨占用',pick:'完成揀貨',deduct:'完成扣庫',ship:'完成',ship_reopen:'回到準備中',cancel_reopen:'取消單回到準備中',return:'退貨入庫',release:'取消／解除占用',quality:'狀態異動',return_release:'轉回入庫'};
  const events=stockData.events.filter(e=>e.shipment_id===shipment.id&&Object.hasOwn(labels,e.kind));if(!events.length)return;
  const disclosure=node('details',undefined,'shipment-history-disclosure');
  disclosure.append(node('summary',`異動紀錄（${events.length} 筆）`));panel.append(disclosure);
  if(events.some(e=>e.kind==='cancel_reopen'))disclosure.append(node('p','先前已釋放紀錄'),node('p','歷史紀錄僅供追查；本輪商品以目前商品表為準，不會取回先前已釋放品號。'));
  const wrap=node('div',undefined,'table-scroll'),table=node('table',undefined,'shipment-history'),head=node('thead'),header=node('tr'),body=node('tbody');
  for(const label of ['時間','商品／SKU','條碼','品號','原入庫單／來源','原件成本','異動','前後狀態','原因'])header.append(node('th',label,label==='原件成本'?'numeric':undefined));head.append(header);table.append(head,body);
  for(const event of events.slice().reverse()){
    const unit=stockData.units.find(u=>u.id===event.unit_id),product=boot.products.find(p=>p.id===unit?.product_id),receipt=stockData.receipts.find(r=>r.id===unit?.receipt_id),row=node('tr');
    const noUnit=!event.unit_id,missing='未記錄',snapshot=event.details.item_snapshot;
    const item=snapshot||{name:product?.name,sku:product?.sku,barcode:product?.barcode,receipt_number:receipt?.number,source:receipt?.source,cost_cents:unit?.cost_cents??event.details.cost_cents};
    let reason=({cancel_item:'單件取消',quality_changed:'品質異常'}[event.details.reason]||event.details.reason||(event.kind==='cancel_reopen'?'僅重開單據／原商品不變':missing));
    if(!noUnit&&!snapshot)reason+='（歷史未留快照，依目前資料顯示）';
    row.append(node('td',event.time?dateTime(event.time):missing),node('td',noUnit?'—（僅單據）':[item.name,item.sku].filter(Boolean).join(' / ')||unit?.product_id||missing),node('td',noUnit?'—':item.barcode||missing),node('td',event.unit_id||'—'),node('td',noUnit?'—':[item.receipt_number,item.source].filter(Boolean).join(' / ')||missing),node('td',noUnit?'—':item.cost_cents!=null?formatAmount(centsText(item.cost_cents)):missing,'numeric'),node('td',labels[event.kind]),node('td',shipmentAuditState(event)),node('td',reason));body.append(row);
  }
  wrap.append(table);disclosure.append(wrap);
}
function renderShipmentItems(panel,shipment,headerActions){
  const cycle=shipmentCycleEvents(shipment);
  panel.append(node('h3',cycle.prior.length?'本輪出貨商品':'出貨商品','document-section-title'));
  if(['draft','allocated'].includes(shipment.state)){
    const bar=node('div',undefined,'form-actions'),label=node('label','商品條碼'),scan=node('input'),add=node('button','加入一件');scan.id='shipment-scan';scan.autocomplete='off';scan.placeholder='一般品輸入條碼；盒損品輸入 #條碼';label.append(scan);add.type='button';bar.append(label,add);panel.append(bar);
    const enqueue=()=>{const barcode=scan.value.trim();if(!barcode)return;scan.value='';const operationId=operation();shipmentScanQueue=shipmentScanQueue.then(async()=>{await request(`/api/shipments/${shipment.id}/scan`,{operation_id:operationId,barcode,expected_reopen_count:shipment.reopen_count||0});await refreshStock();stockMessage('shipping','已加入一件。');}).catch(error=>stockMessage('shipping',`${barcode}：${error.message}`,true));scan.focus();};
    add.onclick=enqueue;scan.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();enqueue();}};
  }
  const effectiveShips=shipmentEffectiveShips(),activeShipIds=new Set(effectiveShips.map(e=>e.id));
  const ids=new Set(cycle.current.filter(e=>e.unit_id&&(['allocate','deduct','ship_reopen'].includes(e.kind)||(e.kind==='ship'&&activeShipIds.has(e.id)))).map(e=>e.unit_id));
  for(const unit of stockData.units)if(unit.shipment_id===shipment.id)ids.add(unit.id);
  const scroll=node('div',undefined,'table-scroll'),table=node('table',undefined,'shipment-unit-table'),head=node('thead'),tr=node('tr'),rows=node('tbody');
  for(const text of ['商品／SKU','條碼','進貨來源','成本','數量','品號','商品狀態','功能'])tr.append(node('th',text,text==='成本'?'numeric':undefined));head.append(tr);table.append(head,rows);
  for(const unit of stockData.units.filter(u=>ids.has(u.id))){
    const p=boot.products.find(p=>p.id===unit.product_id),receipt=stockData.receipts.find(r=>r.id===unit.receipt_id),lastShip=effectiveShips.find(e=>e.unit_id===unit.id);
    const owned=unit.shipment_id===shipment.id,shippedHere=lastShip?.shipment_id===shipment.id&&cycle.current.some(e=>e===lastShip);
    const history=cycle.current.filter(e=>e.unit_id===unit.id);
    const returnedHere=history.some(e=>e.kind==='return');
    let state=owned?stockDisplayState(unit):shippedHere&&unit.location==='customer'?'出庫':returnedHere?(unit.quality==='defective'?'瑕疵／退貨入庫':unit.return_hold?'退貨入庫':'已轉回入庫'):unit.quality==='defective'?'瑕疵':'取消';
    if(!owned&&unit.shipment_id)state=returnedHere?'退貨入庫／已轉其他出貨單':'已轉其他出貨單';
    const row=node('tr'),code=node('td',unit.id,'unit-code'),controls=node('td',undefined,'unit-actions');
    const detailLink=node('a','查看品項','button secondary');detailLink.href='#quality/'+unit.id;controls.append(detailLink);
    if(owned&&unit.location==='warehouse')controls.append(stockButton('shipping','取消',async()=>{await request(`/api/shipments/${shipment.id}/items/${unit.id}/cancel`,{operation_id:operation(),expected_reopen_count:shipment.reopen_count||0});await refreshStock();}));
    if((owned||(shippedHere&&returnedHere&&!unit.shipment_id))&&['warehouse','staging'].includes(unit.location)&&unit.quality!=='defective')controls.append(stockButton('shipping','瑕疵',async()=>shipmentUnitAction(unit,shipment,'defective')));
    if(shippedHere&&unit.location==='customer'){controls.append(stockButton('shipping','退貨入庫',async()=>shipmentUnitAction(unit,shipment,'return')),stockButton('shipping','瑕疵',async()=>shipmentUnitAction(unit,shipment,'defective')));}
    if(returnedHere&&unit.location==='warehouse'&&unit.return_hold&&history.some(e=>e.kind==='return'&&e.details.ship_event_id===unit.return_ship_event_id))controls.append(stockButton('shipping','轉回入庫',async()=>{await request(`/api/stock/units/${unit.id}/restock-return`,{operation_id:operation(),ship_event_id:unit.return_ship_event_id,reason:'退貨商品轉回入庫'});await refreshStock();stockMessage('shipping','已轉回入庫；異常品質與包裝狀態保留。');}));
    if(!controls.children.length)controls.append(node('span','—'));
    const status=statusCell(state+(unit.packaging==='damaged'?'／盒損':''));
    row.append(node('td',`${p?.name||''} · ${p?.sku||unit.product_id}`),node('td',p?.barcode||'—'),node('td',receipt?.source||'—'),node('td',costDisplay(unit.cost_cents),'numeric'),node('td','×1'),code,status,controls);rows.append(row);
  }
  if(!rows.children.length)emptyRow(rows,8,'尚未加入商品，請掃描或手動輸入商品條碼。');
  scroll.append(table);panel.append(node('p',shipmentTotals(shipment),'shipment-totals'),scroll);
  renderShipmentHistory(panel,shipment);
}
function showShipment(id) {
  selectedShipment=id;for(const row of $('shipment-rows').children)row.classList.toggle('selected-record',row.dataset.recordId===id);
  const shipment=stockData.shipments.find(s=>s.id===id);if(!shipment)return;
  const scan=$('shipment-scan');const scanText=scan?.value||'';const scanFocused=document.activeElement===scan;
  const panel=$('shipment-detail');panel.hidden=false;const headerActions=stockDocumentHeader(panel,shipment.number,shipmentState(shipment.state));
  const group=stockData.groups.find(g=>g.id===shipment.group_id);
  const links=node('div',undefined,'document-relations');
  const info=node('table',undefined,'shipment-info'),infoBody=node('tbody');
  for(const entries of [[['單據類型',shipment.parent_id?'副單':'主單'],['出貨通路',shipment.channel||'—']],[['建立時間',dateTime(shipment.created_at)],['首次完成時間',shipment.first_completed_at?dateTime(shipment.first_completed_at):'尚無完成紀錄']],[['最近取消時間',shipment.cancelled_at?dateTime(shipment.cancelled_at):'尚無取消紀錄']]]){const row=node('tr');for(const [label,value] of entries){const th=node('th',label);th.scope='row';row.append(th,node('td',value));}infoBody.append(row);}
  const noteRow=node('tr'),noteLabel=node('th','備註'),noteValue=node('td',shipment.comment||'—');noteLabel.scope='row';noteValue.colSpan=3;noteRow.append(noteLabel,noteValue);infoBody.append(noteRow);info.append(infoBody);panel.append(info);
  if(shipment.parent_id){const parent=stockData.shipments.find(s=>s.id===shipment.parent_id);links.append(pageLink(`返回主單 ${parent?.number||''}`,'shipping/'+shipment.parent_id));}
  for(const child of stockData.shipments.filter(s=>s.parent_id===id))links.append(pageLink(`副單 ${child.number}`,'shipping/'+child.id));
  if(group&&!shipment.parent_id){const sub=stockButton('shipping','建立副單',async()=>openSubShipment(shipment));sub.title='建立同一訂單的副單，各單狀態獨立';headerActions.append(sub);}
  panel.append(links);

  renderShipmentItems(panel,shipment,headerActions);
  const actions=['shipped','cancelled'].includes(shipment.state)?['reopen']:shipment.state==='draft'?['cancel']:['allocated','picked','staged'].includes(shipment.state)?['complete','cancel']:[];
  for(const action of actions)headerActions.append(stockButton('shipping',{complete:'完成',cancel:'取消',reopen:'回到準備中'}[action],async()=>{const legacyCancel=action==='cancel'&&shipment.state==='staged';await request(`/api/shipments/${id}/${legacyCancel?'cancel-staged':action}`,{operation_id:operation(),expected_reopen_count:shipment.reopen_count||0,...(action==='reopen'?{expected_state:shipment.state,...(shipment.state==='shipped'?{expected_shipped_at:shipment.shipped_at}:{})}:{}),...(legacyCancel?{returned_to_warehouse:true,reason:'取消出貨單'}:{})});await refreshStock();stockMessage('shipping',{cancel:'出貨單已取消。',complete:'出貨單已完成。',reopen:shipment.state==='cancelled'?'取消單已回準備中，請重新加入商品；先前釋放商品不會取回。':'已回到準備中，原商品仍由本單占用；取消才釋放庫存。'}[action]);}));
  if($('shipment-scan')){$('shipment-scan').value=scanText;if(scanFocused)$('shipment-scan').focus();}
}
function showReceipt(id) {
  selectedReceipt=id;for(const row of $('receipt-rows').children)row.classList.toggle('selected-record',row.dataset.recordId===id);const receipt=stockData.receipts.find(r=>r.id===id);if(!receipt)return;
  const panel=$('receipt-detail');panel.hidden=false;const actions=stockDocumentHeader(panel,receipt.number,receipt.state);let cancellation=null;
  const receiptLinks=[];
  const putaway=receiptPutaway(receipt,stockData.units);
  if(putaway.pending)receiptLinks.push(['配櫃','putaway/'+id]);
  if(receipt.purchase_id)receiptLinks.push(['來源商品採購','goods-purchases/'+receipt.purchase_id]);
  for(const [label,path] of receiptLinks){const link=stockRecordLink(label,path);link.className='button secondary';actions.append(link);}
  if(receipt.state==='confirmed'){
    actions.append(stockButton('stock','修改費用',async()=>editReceiptCost(receipt)));
    const placement=node('div',undefined,'receipt-putaway-status');placement.append(statusBadge(putaway.label));
    if(putaway.pending)placement.append(node('span',`尚有 ${putaway.pending} 件未配櫃`));panel.append(placement);
  }
  if(['draft','ordered'].includes(receipt.state)){
    actions.append(stockButton('stock','確認入庫',async()=>{if(!confirm('確認本單商品已全數實收、數量正確？未填費用會保留待補。'))return;await request(`/api/receipts/${id}/confirm`,{operation_id:operation()});await refreshStock();stockMessage('stock','已確認入庫，庫存已更新。');}));
    actions.append(stockButton('stock','編輯進貨內容',async()=>navigatePage('stock/'+id+'/edit')));
    const cancel=node('section',undefined,'content-section');cancel.append(node('h3','取消未入庫單'));
    const form=node('form'),label=node('label','取消原因'),reason=node('input'),submit=node('button','確認取消');reason.required=true;reason.maxLength=500;label.append(reason);submit.type='submit';form.append(label,submit);
    form.addEventListener('submit',e=>{e.preventDefault();stockAction('stock',submit,async()=>{await request(`/api/receipts/${id}/cancel`,{operation_id:operation(),reason:reason.value});await refreshStock();stockMessage('stock','已取消，庫存未變動。');});});cancel.append(form);cancel.className='document-operation';cancellation=cancel;
  }
  const costs=receiptCosts(receipt),cash=costDisplay;
  panel.append(stockDetailSections([
    ['單據資訊', [['進貨來源',receipt.source||'未填寫'],['進貨件數',receipt.lines.reduce((n,l)=>n+l.quantity,0)+' 件'],['建立時間',dateTime(receipt.created_at)],['入庫時間',receipt.confirmed_at?dateTime(receipt.confirmed_at):'尚未入庫']]],
    ['費用組成', [['費用狀態',costs.total===null?'待填費用':'已填完成'],['商品貨款',cash(costs.goods)],['進貨運費',cash(costs.freight)],['稅費',cash(costs.tax)],['本單總額',node('strong',cash(costs.total))],['費用分攤',({quantity:'依件數',value:'依貨款金額'}[receipt.allocation]||'未記錄')]]]
  ]));
  if(receipt.comment)panel.append(node('p','備註：'+receipt.comment,'detail-note'));
  if(receipt.production_plan_id){const link=stockRecordLink('查看來源生產單','materials/plans/'+receipt.production_plan_id);link.className='button secondary';panel.append(link);}
  if(receipt.cancelled_at)panel.append(node('p','取消原因：'+receipt.cancel_reason,'notice error'));
  if(receipt.cancelled_at)panel.append(stockInfoTable([['取消時間',dateTime(receipt.cancelled_at)]]));
  panel.append(node('h3','入庫商品','document-section-title'));
  panel.append(materialTable(['商品／SKU','數量','進貨單價','貨款小計'],receipt.lines.map(line=>{const p=boot.products.find(p=>p.id===line.product_id);return [`${p?.name||line.product_id} · ${p?.sku||''}`,String(line.quantity),cash(line.unit_price_cents),cash(line.unit_price_cents==null?null:BigInt(line.unit_price_cents)*BigInt(line.quantity))];})));
  if(cancellation)panel.append(cancellation);
  const revisions=(stockData.cost_revisions||[]).filter(r=>r.receipt_id===id);
  if(revisions.length){panel.append(node('h3','費用修改紀錄','document-section-title'));
    panel.append(materialTable(['時間','原因','原貨款 → 新貨款','原運費 → 新運費','原稅費 → 新稅費'],revisions.map(revision=>{
      const before=receiptCosts(revision.before_json),after=receiptCosts(revision.after_json);
      return [dateTime(revision.time),revision.reason,...['goods','freight','tax'].map(key=>`${costDisplay(before[key])} → ${costDisplay(after[key])}`)];
    })));
  }
}
function editReceiptCost(receipt){
  const previousFocus=document.activeElement,dialog=node('dialog',undefined,'receipt-picker'),form=node('form'),fields=node('div',undefined,'editor-grid'),rows=[];
  dialog.setAttribute('aria-label','修改入庫費用');form.append(node('h2',receipt.number+' · 修改費用'),node('p','留白代表待填費用，0 元是有效金額。保存後會同步更新本批已出貨商品與分析成本。','help'),fields);
  const field=(label,value)=>{const wrap=node('label',label),input=node('input');input.inputMode='decimal';input.value=value==null?'':centsText(value);wrap.append(input);fields.append(wrap);return input;};
  for(const line of receipt.lines){const p=boot.products.find(p=>p.id===line.product_id);rows.push({line,input:field(`${p?.name||line.product_id} / ${p?.sku||''} · ${line.quantity} 件 · 單價`,line.unit_price_cents)});}
  const freight=field('進貨運費',receipt.freight_cents),tax=field('稅費',receipt.tax_cents===undefined?0:receipt.tax_cents),allocationLabel=node('label','費用分攤'),allocation=node('select');
  allocation.append(new Option('依件數','quantity'),new Option('依貨款金額','value'));allocation.value=receipt.allocation;allocationLabel.append(allocation);fields.append(allocationLabel);
  const reasonLabel=node('label','修改原因'),reason=node('textarea');reason.required=true;reason.maxLength=500;reasonLabel.append(reason);form.append(reasonLabel);
  const save=node('button','保存費用'),cancel=node('button','取消','secondary'),actions=node('div',undefined,'form-actions'),error=node('p');error.setAttribute('role','alert');save.type='submit';cancel.type='button';cancel.onclick=()=>dialog.close();actions.append(save,cancel);form.append(error,actions);dialog.append(form);
  let lastPayload='',operationId='';form.addEventListener('submit',async event=>{event.preventDefault();save.disabled=true;error.textContent='';
    const body={expected_cost_version:receipt.cost_version||0,reason:reason.value,lines:rows.map(({line,input})=>({product_id:line.product_id,unit_price:input.value})),freight:freight.value,tax:tax.value,allocation:allocation.value},key=JSON.stringify(body);
    if(key!==lastPayload){lastPayload=key;operationId=operation();}
    try{await request(`/api/receipts/${receipt.id}/cost`,{...body,operation_id:operationId});dialog.close();await refreshStock();stockMessage('stock','費用已更新，關聯商品、出貨單與分析使用修正後成本。');}
    catch(e){error.textContent=e.message;save.disabled=false;}
  });
  const close=()=>dialog.close();window.addEventListener('hashchange',close);dialog.addEventListener('close',()=>{window.removeEventListener('hashchange',close);dialog.remove();previousFocus?.focus();},{once:true});document.body.append(dialog);dialog.showModal();rows[0]?.input.focus();
}
function editReceipt(id){const receipt=stockData.receipts.find(r=>r.id===id);if(!receipt||!['draft','ordered'].includes(receipt.state))throw new Error('只有未入庫且未取消的單據可編輯');editingReceipt=receipt;$('receipt-detail').hidden=true;$('receipt-editor').hidden=false;$('receipt-editor').open=true;$('receipt-number').value=receipt.number;$('receipt-source').value=receipt.source||'';$('receipt-comment').value=receipt.comment||'';$('receipt-freight').value=receipt.freight_cents==null?'':centsText(receipt.freight_cents);$('receipt-tax').value=receipt.tax_cents===null?'':centsText(receipt.tax_cents||0);$('receipt-allocation').value=receipt.allocation;$('receipt-lines').replaceChildren();for(const line of receipt.lines)newLine('receipt-lines',true,line);}
function clearShipmentOrder(){
  $('shipment-order').value='';$('shipment-origin').replaceChildren();$('shipment-parent').replaceChildren();$('shipment-parent').hidden=true;$('shipment-save').disabled=false;$('shipment-empty').hidden=true;$('shipment-empty').textContent='';
}
$('shipment-order-number').addEventListener('input',clearShipmentOrder);
$('new-receipt').addEventListener('click',()=>{$('receipt-editor').open=true;$('receipt-number').focus();});
$('receipt-add').textContent='從商品表選擇';
$('receipt-add').addEventListener('click',openReceiptPicker);
$('new-order').addEventListener('click',()=>{$('order-detail').hidden=true;$('order-editor').open=true;$('order-form').reset();$('order-lines').replaceChildren();newLine('order-lines');$('order-number').focus();});
$('order-add').addEventListener('click',()=>newLine('order-lines'));
$('receipt-form').addEventListener('submit',e=>{e.preventDefault();stockAction('stock',e.submitter,async()=>{await request('/api/receipts',{operation_id:operation(),...(editingReceipt?{id:editingReceipt.id,expected_snapshot:{number:editingReceipt.number,lines:editingReceipt.lines,freight_cents:editingReceipt.freight_cents,tax_cents:editingReceipt.tax_cents,cost_version:editingReceipt.cost_version||0,allocation:editingReceipt.allocation,source:editingReceipt.source||'',comment:editingReceipt.comment||''}}:{}),number:$('receipt-number').value,source:$('receipt-source').value,comment:$('receipt-comment').value,freight:$('receipt-freight').value,tax:$('receipt-tax').value,allocation:$('receipt-allocation').value,lines:documentLines('receipt-lines')});await refreshStock();$('receipt-editor').open=false;stockMessage('stock','入庫單已保存。收到商品後，請按「確認入庫」。');});});
$('order-form').addEventListener('submit',e=>{e.preventDefault();stockAction('orders',e.submitter,async()=>{await request('/api/orders',{operation_id:operation(),account_id:$('order-account').value,order_id:$('order-number').value,ordered_at:localInstant($('order-time').value),customer_ref:$('order-customer').value,kol_ref:$('order-kol').value,utm_medium:$('order-utm').value,order_total:$('order-total').value,lines:documentLines('order-lines')});await refreshBootstrap();await refreshStock();await refreshSales();$('order-editor').open=false;stockMessage('orders','訂單已保存。下一步建立出貨單；尚未扣庫存。');});});
$('shipment-form').addEventListener('submit',e=>{e.preventDefault();stockAction('shipping',e.submitter,async()=>{const parentId=$('shipment-order').dataset.parentId;const sourceOrder=selectedSourceOrder();const saved=await request('/api/shipments',{operation_id:operation(),...(parentId?{parent_id:parentId,number:$('shipment-order-number').value.trim()}:sourceOrder?{account_id:sourceOrder.account_id,order_id:sourceOrder.order_id,number:$('shipment-order-number').value.trim()}:{entry_number:$('shipment-order-number').value.trim()}),channel:$('shipment-channel').value,comment:$('shipment-comment').value});selectedShipment=saved.id;await refreshStock();$('shipment-editor').open=false;stockMessage('shipping','');});});
window.addEventListener('workspace-refreshed',()=>{
  stockLoaded=false;stockProductsReady=false;
  const area=location.hash.slice(1).split('/')[0]||'catalogue';
  if(['orders','shipping'].includes(area)&&!location.hash.slice(1).includes('/'))renderStockLists().catch(e=>stockMessage(area,e.message,true));
  else if(stockAreas.includes(area))ensureStockLoaded().catch(e=>stockMessage('stock',e.message,true));
  else if(area==='catalogue')loadStockProducts().catch(e=>catalogueMessage(e.message,true));
});








$('order-add-non-stock').addEventListener('click',()=>newLine('order-lines',true,{line_type:'non_stock'}));
