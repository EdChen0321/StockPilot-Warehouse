'use strict';
const qualityNames={good:'良品',defective:'瑕疵',damaged:'損毀'};
const eventNames={receipt:'確認入庫',allocate:'配貨占用',pick:'完成揀貨',deduct:'完成扣庫',deduct_cancel:'撤銷扣庫並歸回',ship:'完成出貨',ship_reopen:'回到準備中',cancel_reopen:'取消單回到準備中',release:'解除占用',return:'退貨入庫',return_release:'轉回入庫',quality:'品質變更'};
const unitFilters={q:'',location:'',quality:'',hold:false,state:'',product:'',page:1};
eventNames.cost_revision='入庫費用修正';
Object.assign(eventNames,{putaway:'配櫃',transfer_lock:'開始調撥',transfer_unlock:'解除調撥占用',transfer_complete:'完成調撥',place_rule:'櫃位規則變更',storage_enable:'啟用櫃位管理'});
const locationNames={warehouse:'倉內',staging:'準備中',customer:'完成'};
function renderSkuStock(){
  for(const row of $('product-rows').children){const stock=stockData.products.find(p=>p.id===row.dataset.recordId);for(const cell of row.querySelectorAll('[data-stock-stat]'))cell.textContent=stock?stock[cell.dataset.stockStat]:'—';}
  let context=$('sku-stock-context');if(!context){context=node('div');context.id='sku-stock-context';$('product-editor').querySelector('.section-title').after(context);}
  context.replaceChildren();context.hidden=true;
}
for(const event of ['stock-refreshed','catalogue-refreshed','product-selected'])window.addEventListener(event,renderSkuStock);
function renderInventory(){
  const rows=$('stock-rows');rows.replaceChildren();const q=($('inventory-search')?.value||'').trim().toLocaleLowerCase();
  for(const p of stockData.products){if(q&&![p.name,p.sku,p.barcode].some(v=>String(v||'').toLocaleLowerCase().includes(q)))continue;const row=node('tr'),name=node('td'),link=stockRecordLink(p.name,'quality');link.onclick=()=>{Object.assign(unitFilters,{q:'',location:'',quality:'',hold:false,product:p.id,page:1});};name.append(link,node('small',p.sku));row.append(name,...[p.on_hand,p.staged,p.allocated,p.available,p.return_held,p.defective,p.damaged,p.packaging_damaged].map(value=>node('td',value,'numeric')),node('td',p.stock_cost_amount==null?'待填費用':formatAmount(p.stock_cost_amount),'numeric'));rows.append(row);}
  if(!rows.children.length)emptyRow(rows,10,stockData.products.length?'沒有符合條件的商品。':'先建立商品，再建立入庫單。');
}
function stockRecordLink(label,path){const a=node('a',label);a.href='#'+path;return a;}
function stockInfoTable(entries,columns=1){
  const table=node('table',undefined,'stock-info-table'),body=node('tbody');
  for(let i=0;i<entries.length;i+=columns){const row=node('tr');for(const [label,value] of entries.slice(i,i+columns)){const key=node('th',label),cell=node('td');key.scope='row';if(value&&typeof value==='object')cell.append(value);else cell.textContent=value||'—';row.append(key,cell);}body.append(row);}table.append(body);return table;
}
function stockDetailGrid(entries){
 const grid=node('div',undefined,'unit-info-grid document-info-grid'),middle=Math.ceil(entries.length/2);
 grid.append(stockInfoTable(entries.slice(0,middle)));if(entries.length>middle)grid.append(stockInfoTable(entries.slice(middle)));return grid;
}
function stockDetailSections(groups){
 const grid=node('div',undefined,'unit-info-grid document-info-grid detail-fact-groups');
 for(const [title,entries] of groups){const section=node('section');section.append(node('h3',title,'document-section-title'),stockInfoTable(entries));grid.append(section);}
 return grid;
}
function stockDetailOverview(entries){
 const summary=stockFacts(entries);summary.className='detail-overview';return summary;
}
function stockDetailNotes(title,entries){
 const details=node('details',undefined,'detail-supporting');details.append(node('summary',title),stockDetailGrid(entries));return details;
}
function unitShipment(unit){return stockData.shipments.find(s=>s.id===(unit.shipment_id||unit.last_shipment_id||shipmentEffectiveShips().find(e=>e.unit_id===unit.id)?.shipment_id));}
function renderUnitList(target){
  const selectedProduct=stockData.products.find(p=>p.id===unitFilters.product);
  if(selectedProduct){target.append(stockInfoTable([['商品',selectedProduct.name],['SKU',selectedProduct.sku],['商品條碼',selectedProduct.barcode],['實體庫存',String(selectedProduct.on_hand)],['一般品可用',String(selectedProduct.available)],['盒損品可用',String(selectedProduct.box_available||0)],['未配櫃',String(selectedProduct.unassigned||0)],['庫存成本',selectedProduct.stock_cost_amount==null?'待填費用':formatAmount(selectedProduct.stock_cost_amount)]],2));}
  const scoped=stockData.units.filter(u=>!unitFilters.product||u.product_id===unitFilters.product),counts=new Map();
  for(const unit of scoped){const state=stockDisplayState(unit);counts.set(state,(counts.get(state)||0)+1);}
  const statusBar=node('div',undefined,'inventory-status-filters');
  for(const [state,count] of [['',scoped.length],...counts]){const button=node('button',`${state||'全部'} ${count}`,'secondary');button.type='button';button.setAttribute('aria-pressed',String((unitFilters.state||'')===state));button.onclick=()=>{unitFilters.state=state;unitFilters.page=1;renderStockExceptions();};statusBar.append(button);}target.append(statusBar);
  const matched=stockData.units.filter(u=>stockUnitMatches(u,stockData.products.find(p=>p.id===u.product_id),unitFilters)&&(!unitFilters.state||stockDisplayState(u)===unitFilters.state));
  const pages=Math.max(1,Math.ceil(matched.length/50));unitFilters.page=Math.min(unitFilters.page,pages);
  const wrap=node('div',undefined,'table-scroll'),table=node('table',undefined,'inventory-unit-table'),head=node('thead'),hr=node('tr'),body=node('tbody');for(const text of ['品號','商品／SKU','商品狀態','進貨來源','進貨單','單件成本','進貨時間','出貨單','出貨通路'])hr.append(node('th',text,text==='單件成本'?'numeric':undefined));head.append(hr);table.append(head,body);wrap.append(table);target.append(wrap);
  for(const unit of matched.slice((unitFilters.page-1)*50,unitFilters.page*50)){const p=stockData.products.find(p=>p.id===unit.product_id),receipt=stockData.receipts.find(r=>r.id===unit.receipt_id),shipment=unitShipment(unit),row=node('tr'),code=node('td',undefined,'unit-code'),incoming=node('td'),outgoing=node('td');code.append(stockRecordLink(unit.id,'quality/'+unit.id));incoming.append(stockRecordLink(receipt?.number||'—','stock/'+unit.receipt_id));if(shipment)outgoing.append(stockRecordLink(shipment.number,'shipping/'+shipment.id));else outgoing.textContent='—';row.append(code,node('td',`${p?.name||''} · ${p?.sku||''}`),statusCell(`${stockDisplayState(unit)}${unit.packaging==='damaged'?'／盒損':''}`),node('td',receipt?.source||'—'),incoming,node('td',costDisplay(unit.cost_cents),'numeric'),node('td',dateTime(unit.first_received_at)),outgoing,node('td',shipment?.channel||'—'));body.append(row);}
  if(!matched.length)emptyRow(body,9,stockData.units.length?'沒有符合條件的庫存件。':'確認入庫後，這裡會出現逐件庫存。');
  const pager=node('div',undefined,'form-actions');pager.append(node('span',`${matched.length} 件 · ${unitFilters.page} / ${pages} 頁`));for(const [label,step] of [['上一頁',-1],['下一頁',1]]){const button=node('button',label);button.type='button';button.disabled=unitFilters.page+step<1||unitFilters.page+step>pages;button.onclick=()=>{unitFilters.page+=step;renderStockExceptions();};pager.append(button);}target.append(pager);
}
function renderStockExceptions() {
  if(location.hash.startsWith('#quality/')&&window.pageHasUnsavedChanges?.())return;
  const target=$('unit-history');target.replaceChildren();
  const parts=location.hash.split('/');const byProduct=parts[1]==='by-product';
  if(byProduct){const id=decodeURIComponent(parts[2]||'');if(unitFilters.product!==id)Object.assign(unitFilters,{product:id,q:'',location:'',quality:'',hold:false,state:'',page:1});if(stockLoaded&&!stockData.products.some(p=>p.id===id)){target.append(node('p','找不到此單品 SKU，請返回商品庫。'));return;}}
  const selected=location.hash.startsWith('#quality/')&&!byProduct?decodeURIComponent(parts[1]):null;
  target.classList.toggle('record-detail-page',!!selected);
  if(!selected){renderUnitList(target);return;}
  const chosen=stockData.units.find(u=>u.id===selected);target.append(stockRecordLink('返回此商品庫存列表',chosen?'quality/by-product/'+chosen.product_id:'quality'));
  if(!stockData.units.some(u=>u.id===selected)){target.append(node('p',stockLoaded?'找不到此庫存件。':'載入庫存件中…'));return;}
  for(const unit of stockData.units.filter(u=>u.id===selected)) {
    const details=node('section',undefined,'unit-detail');details.append(node('h3','商品資訊','document-section-title'));
    const p=stockData.products.find(p=>p.id===unit.product_id),receipt=stockData.receipts.find(r=>r.id===unit.receipt_id),shipment=unitShipment(unit);
    const info=node('div',undefined,'unit-info-grid');info.append(stockInfoTable([['商品',p?.name],['SKU',p?.sku],['商品條碼',p?.barcode],['品號',unit.id],['商品狀態',statusBadge(stockDisplayState(unit))],['包裝',statusBadge(unit.packaging==='damaged'?'盒損':'正常')],['櫃位',unit.place_label||'未配櫃']]));
    info.append(stockInfoTable([['進貨單',stockRecordLink(receipt?.number||unit.receipt_id,'stock/'+unit.receipt_id)],['進貨來源',receipt?.source],['單件成本',costDisplay(unit.cost_cents)],['進貨時間',dateTime(unit.first_received_at)],['出貨單',shipment?stockRecordLink(shipment.number,'shipping/'+shipment.id):'—'],['出貨通路',shipment?.channel],['完成時間',shipment?.shipped_at?dateTime(shipment.shipped_at):'—']]));details.append(info);
    const events=[...stockData.events,...(stockData.unit_cost_revisions||[])].filter(e=>e.unit_id===unit.id).sort((a,b)=>String(b.time||'').localeCompare(String(a.time||'')));
    const history=node('section',undefined,'content-section');history.append(node('h3','異動紀錄','document-section-title'));
    const logWrap=node('div',undefined,'table-scroll'),log=node('table',undefined,'unit-history-table'),logHead=node('thead'),logTitles=node('tr'),logBody=node('tbody');for(const title of ['時間','異動','單據','櫃位異動','狀態異動','原因'])logTitles.append(node('th',title));logHead.append(logTitles);log.append(logHead,logBody);logWrap.append(log);history.append(logWrap);
    for(const event of events){
      const row=node('tr'),documentCell=node('td'),d=event.details;
      const source=stockData.shipments.find(s=>s.id===event.shipment_id);
      if(d.transfer_id)documentCell.append(stockRecordLink(d.transfer_number||d.transfer_id,'transfers/'+d.transfer_id));
      else if(source)documentCell.append(stockRecordLink(source.number,'shipping/'+source.id));
      else documentCell.append(stockRecordLink(d.item_snapshot?.receipt_number||receipt?.number||'—','stock/'+(event.receipt_id||unit.receipt_id)));
      const placeText=p=>p?[p.address,p.position,p.name].filter(Boolean).join(' / '):'未配櫃';
      const placeChange=event.kind==='cost_revision'?'不變':Object.hasOwn(d,'from_place')?placeText(d.from_place)+' → '+placeText(d.to_place):'歷史未記錄';
      row.append(node('td',dateTime(event.time)),node('td',eventNames[event.kind]||event.kind),documentCell,node('td',placeChange),node('td',shipmentAuditState(event)),node('td',({cancel_item:'取消單件',quality_changed:'品質異常'}[d.reason]||d.reason||'—')));logBody.append(row);
    }
    if(!events.length)emptyRow(logBody,6,'尚無異動紀錄。');details.append(history);
    const form=node('form',undefined,'editor-grid');const reasonLabel=node('label','修改原因');const reason=node('textarea');reason.rows=3;reason.required=true;reason.maxLength=200;reasonLabel.append(reason);
    if(['warehouse','staging'].includes(unit.location)) {
      const qualityLabel=node('label','品質');const quality=node('select');for(const [value,label] of Object.entries(qualityNames))quality.append(new Option(label,value));if(unit.quality==='pending'){const legacy=new Option('歷史待驗狀態','pending');legacy.disabled=true;quality.append(legacy);}quality.value=unit.quality;qualityLabel.append(quality);
      const packageLabel=node('label','包裝');const packaging=node('select');packaging.append(new Option('正常','good'),new Option('盒損','damaged'));packaging.value=unit.packaging;packageLabel.append(packaging);
      const save=node('button','保存狀態');save.type='submit';const actions=node('div',undefined,'form-actions full-width');actions.append(save);form.append(qualityLabel,packageLabel,reasonLabel,actions);
      const updateReason=()=>{reasonLabel.hidden=quality.value==='defective';reason.required=quality.value!=='defective';if(quality.value==='defective')reason.value='';};quality.addEventListener('change',updateReason);updateReason();
      form.addEventListener('submit',e=>{e.preventDefault();stockAction('stock',save,async()=>{await request(`/api/stock/units/${unit.id}/quality`,{operation_id:operation(),quality:quality.value,packaging:packaging.value,reason:reason.value});await refreshStock();stockMessage('stock','品質狀態與原因已保存。');});});
      if(unit.return_hold){
        details.append(node('p','此件已退貨入庫。轉回入庫後解除退貨限制；瑕疵、損毀或盒損仍不可配貨。'));
        const releaseForm=node('form',undefined,'editor-grid');
        const releaseReasonLabel=node('label','入庫備註'),releaseReason=node('textarea');releaseReason.rows=3;releaseReason.required=true;releaseReason.maxLength=200;releaseReason.value='退貨商品轉回入庫';releaseReasonLabel.append(releaseReason);
        const releaseButton=node('button','轉回入庫');releaseButton.type='submit';const releaseActions=node('div',undefined,'form-actions full-width');releaseActions.append(releaseButton);releaseForm.append(releaseReasonLabel,releaseActions);details.append(releaseForm);
        releaseForm.addEventListener('submit',e=>{e.preventDefault();stockAction('stock',releaseButton,async()=>{await request(`/api/stock/units/${unit.id}/restock-return`,{operation_id:operation(),ship_event_id:unit.return_ship_event_id,reason:releaseReason.value});await refreshStock();stockMessage('stock','已轉回入庫，原成本與品號保留。');});});
      }
    } else if(unit.location==='customer') {
      const shipped=shipmentEffectiveShips().find(e=>e.unit_id===unit.id);const receive=node('button','確認實際退回');receive.type='submit';const returnPlace=returnPlaceSelect(form);const returnActions=node('div',undefined,'form-actions full-width');returnActions.append(receive);form.append(reasonLabel,returnActions);
      form.addEventListener('submit',e=>{e.preventDefault();stockAction('stock',receive,async()=>{await request(`/api/stock/units/${unit.id}/return`,{operation_id:operation(),shipment_id:shipped.shipment_id,place_id:returnPlace?.value,reason:reason.value});await refreshStock();stockMessage('stock','已記錄退貨入庫；請轉回入庫後再安排出貨。');});});
    }
    const change=node('section',undefined,'content-section');change.append(node('h3',unit.location==='customer'?'辦理退貨':'修改品質／包裝狀態'),form);details.append(change);target.append(details);
  }
}
let stockReportSequence=0,lastStockReport=null,alertSettingDirty=false;
function loadAlertSetting(){const p=lastStockReport?.inventory.find(p=>p.product_id===$('alert-product').value)||stockData.alert_settings?.find(p=>p.product_id===$('alert-product').value);$('alert-minimum').value=p?.stock_min??'';$('alert-days').value=p?.coverage_threshold??p?.coverage_days??'';}
async function refreshStockReport() {
  const sequence=++stockReportSequence;const query=new URLSearchParams();
  if($('stock-report-start').value)query.set('start',$('stock-report-start').value);
  if($('stock-report-end').value)query.set('end',$('stock-report-end').value);
  if($('stock-report-asof').value)query.set('as_of',localInstant($('stock-report-asof').value));
  if($('stock-report-product')?.value)query.set('product_id',$('stock-report-product').value);
  const data=await request('/api/stock/reports?'+query);if(sequence!==stockReportSequence)return;lastStockReport=data;
  if(!alertSettingDirty)loadAlertSetting();
  query.set('as_of',data.as_of);query.set('start',data.start);query.set('end',data.end);
  const exports=node('div',undefined,'form-actions');for(const [kind,title] of [['inventory','匯出庫存與成本'],['daily','匯出每日數據'],['monthly','匯出月報'],['totals','匯出進出貨總計']]){const a=node('a',title,'button secondary');a.href='/api/stock/reports/export?'+query+'&kind='+kind;a.download='stock-'+kind+'.csv';exports.append(a);}
  const summary=$('stock-report-summary');summary.replaceChildren(node('p',`${data.start} ～ ${data.end}：入庫 ${data.totals.received} 件／出貨 ${data.totals.shipped} 件／實退 ${data.totals.returned} 件`),node('p',`出貨訂單 ${data.totals.shipping_orders} 單／出貨單 ${data.totals.shipment_count} 張；入庫成本 ${data.totals.received_cost==null?'待填費用':formatAmount(data.totals.received_cost)}／出貨成本 ${data.totals.shipped_cost==null?'待填費用':formatAmount(data.totals.shipped_cost)}`));
  const meta=node('section',undefined,'content-section');meta.append(node('h3','時間與統計依據'),node('p',`庫存截至 ${dateTime(data.as_of)}；台北日期含起訖。出貨量依實際出貨事件，退回另列；訂單數於全區間去重，不相加月份單數。${data.data_complete?'期間資料完整。':'期間資料不足，無法推估覆蓋天數。'}`));summary.append(exports,meta);
  const rows=$('stock-report-rows');rows.replaceChildren();
  for(const p of data.inventory){const row=node('tr');row.append(node('td',p.name),node('td',`${p.on_hand}／${p.available}`),node('td',p.stock_cost==null?'待填費用':formatAmount(p.stock_cost),'numeric'),node('td',p.stock_alert===null?'未設定':p.stock_alert?'低於門檻':'正常'),node('td',p.coverage_days===null?p.coverage_status:`${p.coverage_days} 天${p.coverage_alert?' · 低於門檻':''}`));rows.append(row);}
  if(!data.inventory.length)emptyRow(rows,5,'尚無商品庫存資料。');
  const months=$('stock-monthly');months.replaceChildren(node('h3','每日出貨數據'),materialTable(['日期','入庫','完成','實退','訂單數','出貨單數'],(data.daily||[]).map(d=>[d.day,String(d.received),String(d.shipped),String(d.returned),String(d.shipping_orders),String(d.shipment_count)])),node('h3','月報'),materialTable(['月份','入庫','完成','實退','訂單數','出貨單數'],data.monthly.map(d=>[d.month,String(d.received),String(d.shipped),String(d.returned),String(d.shipping_orders),String(d.shipment_count)])));
}
$('stock-report-form').addEventListener('submit',e=>{e.preventDefault();stockAction('stock',e.submitter,refreshStockReport);});
$('alert-form').addEventListener('submit',e=>{e.preventDefault();stockAction('stock',e.submitter,async()=>{const saved=await request('/api/stock/alerts',{operation_id:operation(),product_id:$('alert-product').value,stock_min:Number($('alert-minimum').value),coverage_days:$('alert-days').value});stockData.alert_settings=[...(stockData.alert_settings||[]).filter(p=>p.product_id!==saved.id),{...saved,product_id:saved.id}];await refreshStockReport();stockMessage('stock','預警門檻已保存。');});});
$('alert-product').addEventListener('change',()=>{alertSettingDirty=false;loadAlertSetting();});
for(const id of ['alert-minimum','alert-days'])$(id).addEventListener('input',()=>{alertSettingDirty=true;});
window.addEventListener('stock-refreshed',()=>{if(location.hash.startsWith('#quality'))renderStockExceptions();const products=stockData.products.map(p=>({value:p.id,label:`${p.name} · ${p.sku}`}));options('alert-product',products,'請選擇商品');if($('stock-report-product'))options('stock-report-product',products,'全部商品');if(location.hash.startsWith('#stock-reports'))refreshStockReport().catch(e=>stockMessage('stock',e.message,true));});

