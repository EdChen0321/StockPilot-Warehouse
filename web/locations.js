'use strict';
let locationData={enabled:false,places:[],transfers:[]},locationSequence=0;
const entryLabels={import:'入庫（一般品）',boxDamaged:'入庫（盒損品）',defective:'瑕疵－產品待換貨',destroy:'毀損',defectiveReturn:'瑕疵－廠商換貨中',other:'其他'};
const transferLabels={draft:'準備中',moving:'調撥中',complete:'已完成',cancelled:'已取消'};
function locationLabel(id){const p=(stockData.places||locationData.places).find(p=>p.id===id);return p?[p.address,p.position,p.name].filter(Boolean).join(' / '):'未配櫃';}
function locationField(form,label,value='',type='text'){
 const wrap=node('label',label),input=node(type==='textarea'?'textarea':'input');if(type==='textarea')input.rows=3;else input.type=type;
 if(type==='checkbox'){wrap.className='checkbox';input.checked=!!value;}else input.value=value;
 wrap.append(input);form.append(wrap);return input;
}
function locationSelect(form,label,items,value=''){
 const wrap=node('label',label),select=node('select');select.append(new Option('請選擇',''));for(const [id,text] of items)select.append(new Option(text,id));select.value=value;select.required=true;wrap.append(select);form.append(wrap);return select;
}
function locationTable(headers,rows){
 const wrap=node('div',undefined,'table-scroll'),table=node('table'),thead=node('thead'),tr=node('tr'),tbody=node('tbody');
 headers.forEach(h=>tr.append(node('th',h)));thead.append(tr);table.append(thead,tbody);wrap.append(table);
 for(const values of rows){const row=node('tr');values.forEach((value,index)=>{const td=node('td');if(value&&typeof value==='object')td.append(value);else if(/狀態|品質.*包裝/.test(headers[index]))td.append(statusBadge(value));else td.textContent=String(value??'—');row.append(td);});tbody.append(row);}
 if(!rows.length)emptyRow(tbody,headers.length,'沒有符合條件的資料。');return wrap;
}
function locationButton(text,path){const a=node('a',text,'button secondary');a.href='#'+path;return a;}
async function locationSubmit(form,event,work){event.preventDefault();const save=event.submitter;save.disabled=true;try{await work();}catch(e){let error=form.querySelector('[role=alert]');if(!error){error=node('p',undefined,'notice error');error.setAttribute('role','alert');form.append(error);}error.textContent=e.message;}finally{save.disabled=false;}}
async function renderLocations(parts){
 const seq=++locationSequence,route=location.hash;
 const [data]=await Promise.all([request('/api/locations'),ensureStockLoaded()]);
 if(seq!==locationSequence||route!==location.hash)return;locationData=data;
 const area=parts[0],target=$(area);target.replaceChildren();
 if(area==='putaway'){renderPutaway(target,parts[1]);return;}
 if(area==='transfers'){renderTransfers(target,parts);return;}
 if(parts[1]){
  target.classList.add('record-detail-page');target.append(locationButton('返回櫃位列表','locations'));
  const p=parts[1]==='new'?null:data.places.find(p=>p.id===parts[1]);
  if(parts[1]!=='new'&&!p){target.append(node('p','找不到此櫃位'));return;}
  if(parts[1]==='new'||parts[2]==='edit'){renderPlaceEditor(target,p);return;}
  const overview=node('section',undefined,'location-overview'),heading=node('div',undefined,'location-overview-heading'),actions=node('div',undefined,'location-overview-actions');
  actions.append(locationButton('編輯櫃位','locations/'+p.id+'/edit'),locationButton('建立調撥','transfers/new/'+p.id));
  heading.append(node('h2',locationLabel(p.id)),actions);
  const units=stockData.units.filter(u=>u.place_id===p.id&&['warehouse','staging'].includes(u.location));
  overview.append(heading,stockDetailSections([
   ['位置與庫存', [['櫃位名稱',p.name],['地址／樓層',p.address],['櫃位位置',p.position],['目前櫃內商品',units.length+' 件']]],
   ['入櫃規則', [['入櫃後狀態',statusBadge(entryLabels[p.entry_state])],['進貨權限',p.can_import?'允許進貨':'不允許進貨'],['出貨權限',p.can_export?'允許出貨':'不可出貨'],['退貨權限',p.can_return?'允許退貨':'不允許退貨']]]
  ]));
  if(p.comment)overview.append(node('p','備註：'+p.comment,'detail-note'));
  target.append(overview);
  target.append(node('h3',`櫃內商品 ${units.length} 件`));renderPlaceUnits(target,units);
  const remove=node('button','刪除櫃位','danger');remove.disabled=units.length>0;
  remove.onclick=()=>stockAction('stock',remove,async()=>{await request('/api/locations/'+p.id+'/delete',{operation_id:operation()});navigatePage('locations');});
  target.append(remove,node('p','有商品或未完成調撥的櫃位不能刪除；刪除後歷史紀錄仍保留。','hint'));return;
 }
 target.classList.remove('record-detail-page');const heading=node('div',undefined,'section-heading');heading.append(node('h2','櫃位管理'),locationButton('新增櫃位','locations/new'));target.append(heading);
 if(!data.enabled){const enable=node('button','啟用櫃位出貨規則');enable.onclick=()=>stockAction('stock',enable,async()=>{await request('/api/locations/enable',{operation_id:operation()});await refreshStock();await renderLocations(parts);});target.append(node('p','啟用後，未配櫃及非出貨櫃商品不可出貨。先建立櫃位並配好現有商品。'),enable);}
 const form=node('form',undefined,'search-bar'),q=locationField(form,'搜尋櫃位'),search=node('button','查詢','secondary'),result=node('div');search.type='submit';form.append(search);target.append(form,result);
 const render=()=>{const text=q.value.trim().toLowerCase();result.replaceChildren(locationTable(['地址／位置','名稱','入櫃狀態','功能','在庫件數','操作'],data.places.filter(p=>[p.address,p.position,p.name,p.comment].join(' ').toLowerCase().includes(text)).map(p=>[p.address+' / '+p.position,p.name,entryLabels[p.entry_state],[p.can_import?'進貨':'',p.can_export?'出貨':'',p.can_return?'退貨':''].filter(Boolean).join('、'),p.unit_count,locationButton('查看','locations/'+p.id)])));};
 form.onsubmit=e=>{e.preventDefault();render();};render();
 renderLocationProductSearch(target);
}
function locationProductRows(snapshot,query){
 const q=query.trim().toLowerCase(),products=new Map(snapshot.products.map(p=>[p.id,p])),groups=new Map();if(!q)return [];
 for(const u of snapshot.units){if(!['warehouse','staging'].includes(u.location))continue;const p=products.get(u.product_id);if(!p||![p.name,p.sku,p.barcode,u.id].some(v=>String(v||'').toLowerCase().includes(q)))continue;
  const key=JSON.stringify([u.product_id,u.place_id,u.quality,u.packaging]);if(!groups.has(key))groups.set(key,{product:p,place_id:u.place_id,quality:u.quality,packaging:u.packaging,count:0,available:0});const g=groups.get(key);g.count++;
  if(u.location==='warehouse'&&u.place_eligible&&u.quality==='good'&&!u.shipment_id&&!u.transfer_id&&!u.return_hold)g.available++;
 }return [...groups.values()];
}
function renderLocationProductSearch(target){
 const section=node('section',undefined,'content-section'),form=node('form',undefined,'search-bar'),q=locationField(form,'找商品所在櫃位'),search=node('button','查詢商品位置'),result=node('div');q.placeholder='商品名稱、SKU、條碼或品號';search.type='submit';form.append(search);section.append(node('h3','商品在哪個櫃位？'),form,result);target.append(section);let page=1;
 const render=()=>{const rows=locationProductRows(stockData,q.value),pages=Math.max(1,Math.ceil(rows.length/50));page=Math.min(page,pages);result.replaceChildren();if(!q.value.trim()){result.append(node('p','輸入商品名稱或代號，查看各櫃在庫件數；未配櫃商品也會列出。'));return;}
  result.append(node('p',`共 ${rows.reduce((n,r)=>n+r.count,0)} 件在庫 · ${rows.length} 筆櫃位／狀態結果`),locationTable(['商品／SKU','條碼','目前櫃位','品質／包裝','在庫件數','可出貨件數','操作'],rows.slice((page-1)*50,page*50).map(r=>[`${r.product.name} / ${r.product.sku}`,r.product.barcode||'—',r.place_id?locationButton(locationLabel(r.place_id),'locations/'+r.place_id):'未配櫃',({good:'一般品',defective:'瑕疵',damaged:'毀損',vendor_exchange:'廠商換貨中',other:'其他',pending:'歷史待驗'}[r.quality]||r.quality)+(r.packaging==='damaged'?'／盒損':''),r.count,r.available,r.place_id?locationButton('由此櫃建立調撥','transfers/new/'+r.place_id):locationButton('查看庫存','quality/by-product/'+r.product.id)])));
  result.append(node('p','可出貨件數已排除占用、調撥中及退貨限制；盒損品須用 #條碼出貨。'));
  const pager=node('div',undefined,'form-actions');pager.append(node('span',`${page} / ${pages} 頁`));for(const [label,step] of [['上一頁',-1],['下一頁',1]]){const b=node('button',label,'secondary');b.type='button';b.disabled=page+step<1||page+step>pages;b.onclick=()=>{page+=step;render();};pager.append(b);}result.append(pager);
 };form.onsubmit=e=>{e.preventDefault();page=1;render();};render();
}
function renderPlaceEditor(target,p){
 target.append(node('h2',p?'編輯櫃位':'新增櫃位'));const form=node('form',undefined,'editor-grid');
 const fields={};for(const [key,label] of [['address','地址／樓層'],['position','櫃位位置'],['name','櫃位名稱'],['comment','備註']]){fields[key]=locationField(form,label,p?.[key]||'',key==='comment'?'textarea':'text');fields[key].maxLength=key==='comment'?500:100;fields[key].required=['address','position'].includes(key);}
 for(const [key,label] of [['can_import','允許進貨'],['can_export','允許出貨'],['can_return','允許退貨']])fields[key]=locationField(form,label,p?.[key]||false,'checkbox');
 const state=locationSelect(form,'商品入櫃後狀態',Object.entries(entryLabels),p?.entry_state||'import');
 const save=node('button','保存櫃位');save.type='submit';const actions=node('div',undefined,'form-actions full-width');actions.append(save);form.append(node('p','更改入櫃規則會套用到櫃內未出庫商品；有占用或調撥時會阻擋。','hint'),actions);target.append(form);
 form.onsubmit=e=>locationSubmit(form,e,async()=>{const body={operation_id:operation(),entry_state:state.value,...(p?{id:p.id,expected_version:p.version}:{})};for(const [key,input] of Object.entries(fields))body[key]=input.type==='checkbox'?input.checked:input.value;const saved=await request('/api/locations',body);await refreshStock();navigatePage('locations/'+saved.id);});
}
function renderPlaceUnits(target,units){
 target.append(locationTable(['品號','商品／SKU','狀態','入庫單','成本'],units.map(u=>{const p=stockData.products.find(p=>p.id===u.product_id),r=stockData.receipts.find(r=>r.id===u.receipt_id);return [locationButton(u.id,'quality/'+u.id),p.name+' / '+p.sku,statusBadge(stockDisplayState(u)+(u.packaging==='damaged'?'／盒損':'')),r?.number||'—',costDisplay(u.cost_cents)];})));
}
function confirmPutawayCost(receipt){
 const cost=receiptCosts(receipt).total;
 if(cost===null)return confirm('此入庫單尚未填寫費用。確認繼續配櫃？按取消可返回入庫單填寫費用。');
 if(cost===0n)return confirm('此入庫單費用為 0 元，請確認是否正確。確認繼續配櫃？');
 return true;
}
function renderPutaway(target,id){
 target.classList.add('record-detail-page');target.append(locationButton('返回入庫單','stock/'+id));const receipt=stockData.receipts.find(r=>r.id===id);
 if(!receipt){target.append(node('p','找不到入庫單'));return;}
 const units=stockData.units.filter(u=>u.receipt_id===id&&u.location==='warehouse'&&!u.place_id);
 if(!units.length){navigatePage('stock/'+id);return;}
 target.append(node('h2',receipt.number+' · 配櫃'));
 const form=node('form',undefined,'editor-grid'),products=[...new Set(units.map(u=>u.product_id))].map(id=>{const p=stockData.products.find(p=>p.id===id);return [id,p.name+' / '+p.sku];});
 const product=locationSelect(form,'商品',products),qty=locationField(form,'配櫃件數','1','number');qty.min=1;qty.required=true;
 const destination=locationSelect(form,'目標櫃位',locationData.places.filter(p=>p.can_import).map(p=>[p.id,locationLabel(p.id)+' · '+entryLabels[p.entry_state]]));
 product.onchange=()=>{const count=units.filter(u=>u.product_id===product.value).length;qty.max=count;qty.value=count;};
 const save=node('button','確認配櫃');save.type='submit';const actions=node('div',undefined,'form-actions full-width');actions.append(save);form.append(actions);target.append(form);renderPlaceUnits(target,units);
 form.onsubmit=e=>locationSubmit(form,e,async()=>{if(!confirmPutawayCost(receipt))return;
  await request('/api/locations/assign',{operation_id:operation(),receipt_id:id,product_id:product.value,quantity:Number(qty.value),place_id:destination.value,cost_acknowledged:true,expected_cost_version:receipt.cost_version||0});await refreshStock();await renderLocations(['putaway',id]);});
}
function renderTransfers(target,parts){
 const id=parts[1];
 if(!id){target.classList.remove('record-detail-page');const heading=node('div',undefined,'section-heading');heading.append(node('h2','調撥管理'),locationButton('新增調撥','transfers/new'));target.append(heading);
  const form=node('form',undefined,'search-bar'),q=locationField(form,'搜尋單號'),state=locationSelect(form,'狀態',[['all','全部'],...Object.entries(transferLabels)],'all'),search=node('button','查詢','secondary'),result=node('div');form.append(search);target.append(form,result);
  const render=()=>result.replaceChildren(locationTable(['單號','狀態','來源櫃','目標櫃','件數','操作'],locationData.transfers.filter(t=>t.number.includes(q.value.trim())&&(state.value==='all'||t.state===state.value)).map(t=>[t.number,statusBadge(transferLabels[t.state]),locationLabel(t.from_place_id),locationLabel(t.to_place_id),t.unit_ids.length,locationButton('查看','transfers/'+t.id)])));form.onsubmit=e=>{e.preventDefault();render();};render();return;
 }
 target.classList.add('record-detail-page');target.append(locationButton('返回調撥列表','transfers'));
 if(id!=='new'){
  const t=locationData.transfers.find(t=>t.id===id);if(!t){target.append(node('p','找不到調撥單'));return;}
  const heading=node('div',undefined,'document-heading'),title=node('div'),actions=node('div',undefined,'document-actions');title.append(node('h2',t.number));heading.append(title,actions);target.append(heading);
  for(const action of t.state==='draft'?['start','cancel']:t.state==='moving'?['complete','cancel']:[]){const b=node('button',{start:`開始調撥（${t.unit_ids.length} 件）`,complete:`確認調撥完成（${t.unit_ids.length} 件）`,cancel:'取消調撥'}[action],action==='cancel'?'secondary':'');b.onclick=()=>stockAction('stock',b,async()=>{await request('/api/transfers/'+id+'/'+action,{operation_id:operation()});await refreshStock();await renderLocations(parts);});actions.append(b);}
  target.append(stockDetailSections([
   ['移出與移入', [['來源櫃位（移出）',stockRecordLink(locationLabel(t.from_place_id),'locations/'+t.from_place_id)],['目標櫃位（移入）',stockRecordLink(locationLabel(t.to_place_id),'locations/'+t.to_place_id)],['本次調撥',t.unit_ids.length+' 件']]],
   ['單據紀錄', [['調撥狀態',statusBadge(transferLabels[t.state])],['建立時間',dateTime(t.created_at)],['更新時間',dateTime(t.updated_at)]]]
  ]));
  target.append(node('p',{draft:'尚未開始調撥，請先核對來源、目標櫃位及商品。',moving:'商品調撥中；實際抵達目標櫃位後，請確認調撥完成。',complete:`已完成 ${t.unit_ids.length} 件調撥。`,cancelled:'此調撥已取消。'}[t.state],'detail-breakdown'));
  if(t.comment)target.append(node('p','備註：'+t.comment,'detail-note'));
  target.append(node('h3','調撥商品','document-section-title'));
  renderPlaceUnits(target,stockData.units.filter(u=>t.unit_ids.includes(u.id)));return;
 }
 target.append(node('h2','新增調撥'));const form=node('form',undefined,'editor-grid'),number=locationField(form,'調撥單號','MOVE-'+Date.now());number.required=true;
 const places=locationData.places.map(p=>[p.id,locationLabel(p.id)+' · '+entryLabels[p.entry_state]]),src=locationSelect(form,'來源櫃位',places,parts[2]||''),dst=locationSelect(form,'目標櫃位',places),comment=locationField(form,'備註','','textarea');comment.maxLength=1000;
 const choices=node('div',undefined,'full-width'),selected=new Set(),summary=node('p',undefined,'full-width'),save=node('button','保存調撥草稿');save.type='submit';summary.setAttribute('role','status');summary.setAttribute('aria-live','polite');
 const updateCount=()=>{summary.textContent=`已勾選 ${selected.size} 件 · 本次調撥 ${selected.size} 件`;save.textContent=`保存調撥草稿（${selected.size} 件）`;save.disabled=selected.size===0;};
 const render=()=>{selected.clear();choices.replaceChildren();const units=stockData.units.filter(u=>u.place_id===src.value&&u.location==='warehouse'&&!u.shipment_id&&!u.transfer_id&&!u.return_hold);
  choices.append(node('p',src.value?`本櫃可調撥 ${units.length} 件；可按商品輸入數量，也可逐件勾選。`:'請先選擇來源櫃位。'));
  renderTransferPicker(choices,units,selected,updateCount);updateCount();
 };src.onchange=render;render();const actions=node('div',undefined,'form-actions full-width');actions.append(save);form.append(summary,choices,dst.parentElement||dst.parentNode,actions);target.append(form);
 form.onsubmit=e=>locationSubmit(form,e,async()=>{const t=await request('/api/transfers',{operation_id:operation(),number:number.value,from_place_id:src.value,to_place_id:dst.value,comment:comment.value,unit_ids:[...selected]});navigatePage('transfers/'+t.id);});
}
function renderTransferPicker(target,units,selected,onChange){
 const products=new Map(stockData.products.map(p=>[p.id,p])),ordered=[...units].sort((a,b)=>String(a.fifo_at).localeCompare(String(b.fifo_at))||a.id.localeCompare(b.id));
 const controls=node('div',undefined,'editor-grid'),product=locationSelect(controls,'調撥商品',[...new Set(units.map(u=>u.product_id))].map(id=>[id,`${products.get(id)?.name||id} / ${products.get(id)?.sku||''}`]));product.required=false;
 const state=locationSelect(controls,'商品狀態',[]);state.required=false;
 const quantity=locationField(controls,'本次加入件數','1','number');quantity.min=1;quantity.step=1;
 const add=node('button','按數量加入選取','secondary'),all=node('button','全選符合條件的商品','secondary'),clear=node('button','清除全部選取','secondary'),info=node('p'),message=node('p'),list=node('div');
 for(const b of [add,all,clear])b.type='button';message.setAttribute('role','status');
 const actions=node('div',undefined,'form-actions full-width');actions.append(add,all,clear);controls.append(actions);target.append(controls,info,message,list);let page=1;
 const matches=()=>ordered.filter(u=>(!product.value||u.product_id===product.value)&&(!state.value||`${u.quality}/${u.packaging}`===state.value));
 const render=()=>{const filtered=matches(),remaining=filtered.filter(u=>!selected.has(u.id)),pages=Math.max(1,Math.ceil(filtered.length/50));page=Math.min(page,pages);
  info.textContent=`符合條件 ${filtered.length} 件 · 已選 ${filtered.length-remaining.length} 件 · 尚可加入 ${remaining.length} 件`;
  add.disabled=!product.value||!remaining.length;all.disabled=!remaining.length;
  all.textContent=`全選符合條件（${remaining.length} 件未選，含所有頁）`;
  list.replaceChildren(locationTable(['選擇','品號','商品／SKU','狀態'],filtered.slice((page-1)*50,page*50).map(u=>{const label=node('label',undefined,'checkbox'),check=node('input');check.type='checkbox';check.checked=selected.has(u.id);check.setAttribute('aria-label','選取 '+u.id);check.onchange=()=>{check.checked?selected.add(u.id):selected.delete(u.id);onChange();render();};label.append(check);const p=products.get(u.product_id);return [label,u.id,`${p?.name||''} / ${p?.sku||''}`,statusBadge(stockDisplayState(u)+(u.packaging==='damaged'?'／盒損':''))];})));
  const pager=node('div',undefined,'form-actions');pager.append(node('span',`第 ${page} / ${pages} 頁，每頁 50 件；跨頁保留勾選`));for(const [label,step] of [['上一頁',-1],['下一頁',1]]){const b=node('button',label,'secondary');b.type='button';b.disabled=page+step<1||page+step>pages;b.onclick=()=>{page+=step;render();};pager.append(b);}list.append(pager);
 };
 const refreshStates=()=>{state.replaceChildren(new Option('全部狀態',''));const map=new Map();for(const u of ordered.filter(u=>!product.value||u.product_id===product.value))map.set(`${u.quality}/${u.packaging}`,stockDisplayState(u)+(u.packaging==='damaged'?'／盒損':''));for(const [value,label] of map)state.append(new Option(label,value));state.value='';};
 product.onchange=()=>{refreshStates();page=1;message.textContent='';render();};state.onchange=()=>{page=1;message.textContent='';render();};
 add.onclick=()=>{const count=Number(quantity.value),remaining=matches().filter(u=>!selected.has(u.id));if(!product.value||!Number.isSafeInteger(count)||count<1||count>remaining.length){message.textContent=`請選商品並輸入 1～${remaining.length} 件；本次未加入。`;return;}for(const u of remaining.slice(0,count))selected.add(u.id);quantity.value='';message.textContent=`已加入 ${count} 件，依原入庫順序選取；可在下方調整。`;onChange();render();};
 all.onclick=()=>{for(const u of matches())selected.add(u.id);message.textContent='已選取所有符合條件的商品，包含其他頁。';onChange();render();};
 clear.onclick=()=>{selected.clear();message.textContent='已清除全部選取。';onChange();render();};refreshStates();render();
}
function returnPlaceSelect(form){
 if(!stockData.storage_enabled)return null;
 return locationSelect(form,'退貨放入櫃位',(stockData.places||[]).filter(p=>p.can_return).map(p=>[p.id,locationLabel(p.id)]));
}

if(['locations','transfers','putaway'].includes(location.hash.slice(1).split('/')[0]))renderRoute(true);
