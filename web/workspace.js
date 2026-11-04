'use strict';
const recordPages={},pageScroll=new Map();
const dirtyForms=new Set();let submittingForm=null;
let activeRoute='',pageDirty=false,routePending=false,stockLoaded=false,materialsLoaded=false;
const areas=['goods-purchases','catalogue','stock','orders','shipping','shipping-statistics','materials','dashboard','sales','import','history','inventory','quality','stock-reports','sources','product-tools','kol','locations','transfers','putaway','product-analytics','assistant','ai-settings','ai-evaluation'];
const defaultArea='catalogue';
const profileAreas=new Set(['goods-purchases','catalogue','stock','shipping','shipping-statistics','materials','inventory','quality','stock-reports','product-tools','locations','transfers','putaway','assistant','ai-settings','ai-evaluation']);
for(const link of document.querySelectorAll('#workspace-navigation a[href^="#"]')){
 if(!profileAreas.has(link.hash.slice(1).split('/')[0]))link.remove();
}
for(const group of document.querySelectorAll('#workspace-navigation .nav-group')){
 if(!group.querySelector('a'))group.remove();
}
const operationNotice=node('div');operationNotice.id='operation-notice';operationNotice.hidden=true;operationNotice.setAttribute('role','status');$('main').prepend(operationNotice);
window.pageHasUnsavedChanges=()=>pageDirty;
function pageLink(text,path){const a=node('a',text,'button secondary');a.href='#'+path;return a;}
function standalone(id,title){const s=node('section',undefined,'panel');s.id=id;s.append(node('h2',title));$('stock').after(s);return s;}
const invPage=standalone('inventory','SKU 庫存總覽'),qualityPage=standalone('quality','SKU 庫存詳細'),reportPage=standalone('stock-reports','出貨與庫存數據');
for(const [id,title] of [['locations','櫃位管理'],['transfers','調撥管理'],['putaway','配櫃']])standalone(id,title);
standalone('goods-purchases','商品採購／叫貨');
standalone('product-analytics','商品數據分析');
for(const section of [$('stock'),$('shipping'),invPage,qualityPage,reportPage])section.classList.add('stock-surface');
const invTable=$('stock-rows').closest('.table-scroll'),invHelp=invTable.nextElementSibling;invPage.append(invTable,invHelp);
const inventorySearchForm=node('form',undefined,'search-bar'),inventoryLabel=node('label','搜尋庫存'),inventorySearch=node('input'),inventorySubmit=node('button','查詢');inventorySearch.id='inventory-search';inventorySearch.type='search';inventorySearch.placeholder='商品、SKU 或條碼';inventoryLabel.append(inventorySearch);inventorySubmit.type='submit';inventorySearchForm.append(inventoryLabel,inventorySubmit);invTable.before(inventorySearchForm);inventorySearchForm.onsubmit=e=>{e.preventDefault();renderInventory();};
qualityPage.append($('unit-history').closest('.content-section'));qualityPage.querySelector('.content-section').open=true;
reportPage.append($('stock-report-form').closest('.content-section'));reportPage.querySelector('.content-section').open=true;
const stockReportProductLabel=node('label','商品 SKU'),stockReportProduct=node('select');stockReportProduct.id='stock-report-product';stockReportProduct.append(new Option('全部商品',''));stockReportProductLabel.append(stockReportProduct);$('stock-report-form').prepend(stockReportProductLabel);
$('stock-title').textContent='入庫單管理';
for(const section of [invPage,qualityPage])section.prepend(pageLink('返回商品列表','catalogue'));
reportPage.prepend(pageLink('返回出貨管理','shipping'));
for(const [area,editor,selected] of [['catalogue','product-editor',null],['stock','receipt-editor','receipt-detail'],['orders','order-editor','order-detail'],['shipping','shipment-editor','shipment-detail']]){
 const section=$(area),list=node('div',undefined,'record-list-page'),detail=node('div',undefined,'record-detail-page'),back=pageLink('返回列表',area);detail.append(back);
 if(selected){const view=node('div');view.id=selected;detail.append(view);}detail.append($(editor));
 for(const child of [...section.children])if(!child.id.endsWith('-notice'))list.append(child);
 section.append(list,detail);recordPages[area]={list,detail,back,editor:$(editor),selected:selected?$(selected):null};
}
const catalogueActions=node('div',undefined,'form-actions');
catalogueActions.style.margin='0';
catalogueActions.setAttribute('role','group');
catalogueActions.setAttribute('aria-label','商品庫操作');
catalogueActions.append(pageLink('庫存總覽','inventory'),$('new-product'));
recordPages.catalogue.list.querySelector('.section-heading').append(catalogueActions);
$('material-workspace').className='record-workspace';$('material-list').className='record-list-page';$('material-detail').className='record-detail-page';
const materialBack=pageLink('返回列表','materials/materials');$('material-workspace').before(materialBack);
const sourcesPage=standalone('sources','訂單來源管理'),sources=$('account-list').closest('.content-section');if(sources){sourcesPage.append(sources);sources.open=true;}
sourcesPage.classList.add('record-detail-page');sourcesPage.prepend(pageLink('返回訂單列表','orders'));
recordPages.orders.list.querySelector('.section-heading .form-actions').append(pageLink('管理訂單來源','sources'));
const productTools=standalone('product-tools','商品屬性與跨購分析');productTools.append(pageLink('返回商品列表','catalogue'),$('attribute-form').closest('.content-section'),crossbuyPanel);
crossbuyPanel.remove();
productTools.querySelector('h2').textContent='商品屬性';
const orderSourceLink=$('order-form').querySelector('a');if(orderSourceLink){orderSourceLink.href='#sources';orderSourceLink.textContent='新增／管理訂單來源';}
const originalRequest=request;
request=async function(url,body){const form=submittingForm;const result=await originalRequest(url,body);if(body!==undefined){if(form)dirtyForms.delete(form);pageDirty=dirtyForms.size>0;
 // Detail routes read the local snapshot: load the saved record before navigating.
 if((url==='/api/receipts'&&result.id)||(url==='/api/orders'&&result.order_id)||(url==='/api/shipments'&&result.id)){
  await refreshStock();
  if(url==='/api/receipts')navigatePage('stock');
  if(url==='/api/orders')navigatePage('orders/'+encodeURIComponent(JSON.stringify([body.account_id,result.order_id])));
  if(url==='/api/shipments')navigatePage('shipping/'+encodeURIComponent(result.id));
 }
 if(url.startsWith('/api/materials/')&&location.hash.endsWith('/new')&&(result.id||result.product_id))navigatePage('materials/'+materialTab+'/'+encodeURIComponent(result.id||result.product_id));
}return result;};
function navigatePage(path){if(!profileAreas.has(path.split('/')[0]))return false;if(pageDirty&&!confirm('有尚未保存的變更，確定離開並捨棄嗎？'))return false;dirtyForms.clear();pageDirty=false;if(location.hash==='#'+path)renderRoute(true);else location.hash=path;return true;}
window.navigatePage=navigatePage;
function markDirty(target){const form=target.closest('form');if(form&&target.closest('.record-detail-page')){dirtyForms.add(form);pageDirty=true;}}
for(const event of ['input','change'])document.addEventListener(event,e=>markDirty(e.target));
document.addEventListener('click',e=>{const b=e.target.closest('button');if(b&&/移除|加入物料|新增商品/.test(b.textContent))markDirty(b);},true);
document.addEventListener('submit',e=>{const others=[...dirtyForms].filter(f=>f!==e.target);if(others.length){if(!confirm('另有未保存的表單，繼續儲存會捨棄其他變更。確定繼續嗎？')){e.preventDefault();e.stopImmediatePropagation();return;}for(const f of others)dirtyForms.delete(f);}submittingForm=e.target;setTimeout(()=>{submittingForm=null;},0);},true);
window.guardDetailReplacement=()=>{if(pageDirty&&!confirm('切換操作會捨棄尚未保存的變更，確定繼續嗎？'))return false;dirtyForms.clear();pageDirty=false;return true;};
window.addEventListener('beforeunload',e=>{if(pageDirty){e.preventDefault();e.returnValue='';}});
document.addEventListener('click',e=>{const a=e.target.closest('a[href^="#"]');if(a){e.preventDefault();navigatePage(a.getAttribute('href').slice(1));}},true);
const productRenderer=editProduct;editProduct=id=>navigatePage('catalogue/'+(id?encodeURIComponent(id):'new'));
for(const [id,path] of [['new-product','catalogue/new'],['new-order','orders/new'],['new-receipt','stock/new'],['cancel-product','catalogue']])$(id).addEventListener('click',e=>{e.preventDefault();e.stopImmediatePropagation();navigatePage(path);},true);
const shippingNew=node('button','建立訂單');shippingNew.type='button';shippingNew.onclick=()=>navigatePage('shipping/new');recordPages.shipping.list.querySelector('.section-heading').append(shippingNew);
$('material-new').addEventListener('click',e=>{e.preventDefault();e.stopImmediatePropagation();navigatePage('materials/'+materialTab+'/new');},true);
function showNew(area){const p=recordPages[area];p.editor.open=true;if(p.selected)p.selected.hidden=true;
 if(area==='orders'){$('order-form').reset();$('order-lines').replaceChildren();newLine('order-lines');}
 if(area==='stock'){editingReceipt=null;$('receipt-form').reset();$('receipt-lines').replaceChildren();newLine('receipt-lines');}
 if(area==='shipping'){$('shipment-number-label').textContent='\u8a02\u55ae\u7de8\u865f';$('shipment-order-number').placeholder='\u8f38\u5165\u8a02\u55ae\u7de8\u865f';$('shipment-form').reset();$('shipment-origin').replaceChildren();$('shipment-parent').replaceChildren();$('shipment-parent').hidden=true;$('shipment-editor').querySelector('h2').textContent='建立訂單';$('shipment-save').textContent='建立訂單';$('shipment-order').value='';delete $('shipment-order').dataset.parentId;$('shipment-order-number').readOnly=false;$('shipment-save').disabled=false;$('shipment-empty').hidden=true;$('shipment-empty').textContent='';}
}
async function renderRoute(force=false){
 const requested=location.hash.slice(1)||defaultArea;
 const path=profileAreas.has(requested.split('/')[0])?requested:defaultArea;
 if(path!==requested)history.replaceState(null,'','#'+path);
 if(path!==activeRoute&&pageDirty){if(!confirm('有尚未保存的變更，確定離開並捨棄嗎？')){history.replaceState(null,'','#'+activeRoute);return;}dirtyForms.clear();pageDirty=false;}
 if(path===activeRoute&&!force)return;if(activeRoute&&path!==activeRoute)pageScroll.set(activeRoute,scrollY);activeRoute=path;
 const parts=path.split('/');let area=parts[0];if(!areas.includes(area))area=defaultArea;for(const name of areas)$(name).hidden=name!==area;
 $('main').classList.toggle('stock-workspace',['stock','shipping','inventory','quality','stock-reports'].includes(area));
 applyOperationsView(area);
 for(const link of document.querySelectorAll('.work-tabs a')){if(link.hash==='#'+area)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');}
 for(const a of document.querySelectorAll('.sidebar a')){const selected=a.hash==='#'+(['inventory','quality','stock-reports','product-analytics'].includes(area)?'catalogue':area);if(selected)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');}
 $('notice').textContent='';for(const id of ['stock-notice','shipping-notice']){const notice=$(id);notice.textContent='';notice.hidden=true;}routePending=false;
 for(const [name,p] of Object.entries(recordPages)){const detail=name===area&&!!parts[1];p.list.hidden=detail;p.detail.hidden=!detail;}
 materialBack.hidden=area!=='materials'||!parts[2];
 try{
  if(bootstrapReady&&['orders','shipping'].includes(area)&&!parts[1]){await renderStockLists();if(activeRoute!==path)return;}
  else if(bootstrapReady&&stockAreas.includes(area)){
    if(!stockLoaded)announce('正在載入庫存與單據…');
    await ensureStockLoaded();if(activeRoute!==path)return;announce('');
    if(!parts[1])await renderStockLists();
  }
  if(bootstrapReady&&area==='catalogue'){await loadStockProducts();if(activeRoute!==path)return;}
  if(bootstrapReady&&area==='materials'&&!materialsLoaded){await refreshMaterials();if(activeRoute!==path)return;}
  if(stockLoaded){await loadStockHistory(path);if(activeRoute!==path)return;}
  if(['locations','transfers','putaway'].includes(area)){if(typeof renderLocations==='function')await renderLocations(parts);else routePending=true;}
  if(area==='goods-purchases')await renderGoodsPurchases(parts);
  if(area==='kol')await renderKol(parts);
  if(area==='dashboard'){if(!bootstrapReady){routePending=true;$('dashboard-notice').textContent='正在載入商品與來源…';}else await refreshDashboard();}
  if(area==='sales'&&parts[1]){if(!bootstrapReady)routePending=true;else{const query=new URLSearchParams(decodeURIComponent(parts[1]));for(const key of ['start','end','account','product'])$('filter-'+key).value=query.get(key==='account'||key==='product'?key+'_id':key)||'';await refreshSales();}}
  if(area==='sales'&&!parts[1]){if(!bootstrapReady)routePending=true;else await refreshSales();}
  if(area==='product-analytics')await renderProductAnalytics(parts);
  if(area==='shipping-statistics')await refreshShippingStatistics();
  if(area==='quality'){routePending=!stockLoaded;renderStockExceptions();}
  if(area==='stock-reports'){routePending=!stockLoaded;options('stock-report-product',stockData.products.map(p=>({value:p.id,label:`${p.name} · ${p.sku}`})),'全部商品');const id=parts[1]?decodeURIComponent(parts[1]):'';if(id&&!stockData.products.some(p=>p.id===id)){if(stockLoaded)throw new Error('找不到此單品 SKU，請返回商品庫。');}else{$('stock-report-product').value=id;await refreshStockReport();}}
  if(recordPages[area]&&parts[1]){const id=decodeURIComponent(parts[1]),p=recordPages[area];p.editor.hidden=area!=='catalogue'&&id!=='new';
   const back=p.back;if(area==='stock'&&parts[2]==='edit'){back.href='#stock/'+encodeURIComponent(id);back.textContent='返回入庫單';}else{back.href='#'+area;back.textContent=area==='orders'?'返回訂單列表':'返回列表';}
   if(!bootstrapReady||(area==='catalogue'&&!catalogueReady)){routePending=true;p.editor.hidden=true;}
   else if(area==='catalogue')await productRenderer(id==='new'?null:id);else if(id==='new'){showNew(area);if(area==='shipping'&&parts[2]){if(!stockLoaded)routePending=true;else if(parts[2]==='parent')prepareSubShipment(decodeURIComponent(parts[3]));else prepareShipment(decodeURIComponent(parts[2]),true);}}else{
    const exists=area==='orders'?orderData.some(o=>orderKey(o)===id):stockData[area==='stock'?'receipts':'shipments'].some(r=>r.id===id);
    if(!exists){routePending=!stockLoaded;p.selected.hidden=false;p.selected.replaceChildren(node('p',stockLoaded?'找不到此單據，請返回列表核對。':'讀取單據中…'));}else if(area==='stock'&&parts[2]==='edit')editReceipt(id);else (area==='orders'?showOrder:area==='stock'?showReceipt:showShipment)(id);
   }
  }
  if(area==='materials'){
   materialTab=Object.hasOwn(materialTabs,parts[1])?parts[1]:'materials';materialSelection=null;renderMaterialList();const id=parts[2]?decodeURIComponent(parts[2]):null;
   $('material-tabs').hidden=!!id;$('material-new').hidden=!!id||materialTab==='summary';materialBack.href='#materials/'+materialTab;$('material-list').hidden=!!id;$('material-detail').hidden=!id;
   if(!bootstrapReady||!materialsLoaded){routePending=true;$('material-detail').replaceChildren(node('p','載入主檔中…'));}
   else if(materialTab==='summary')await renderMaterialSummary();else if(id==='new'){if(['materials','suppliers'].includes(materialTab))editMaterialMaster();else if(materialTab==='boms')editBom();else if(materialTab==='plans')newMaterialPlan();else newMaterialPurchase();}
   else if(id){if(!(materialData[materialTab]||[]).some(x=>(x.id||x.product_id)===id)){routePending=!materialsLoaded;$('material-detail').replaceChildren(node('p',materialsLoaded?'找不到此紀錄，請返回列表核對。':'讀取紀錄中…'));}else showMaterialRecord(id);}
   $('material-new').hidden=!!id||materialTab==='summary';
  }
 }catch(error){if(activeRoute!==path)return;announce(error.message,true);const p=recordPages[area];(p?.selected||p?.detail||$('material-detail')).append(node('p',error.message,'notice error'));}
 if(activeRoute===path&&(location.hash.slice(1)||defaultArea)===path)window.scrollTo(0,pageScroll.get(path)||0);
}
window.addEventListener('hashchange',()=>renderRoute());
window.addEventListener('stock-refreshed',()=>{stockLoaded=true;if(routePending&&!pageDirty)renderRoute(true);});
window.addEventListener('materials-refreshed',()=>{materialsLoaded=true;if(routePending&&!pageDirty)renderRoute(true);});
for(const event of ['workspace-refreshed','catalogue-refreshed'])window.addEventListener(event,()=>{if(routePending&&!pageDirty)renderRoute(true);});
renderRoute();
