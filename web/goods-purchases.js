'use strict';
let goodsData={purchases:[],suppliers:[],products:[]},goodsSequence=0;
const goodsStates={draft:'草稿',ordered:'已叫貨／待到貨',partial:'部分到貨',received:'已收齊／結案',cancelled:'已取消餘量／結案'};
const goodsListStates={draft:'草稿',ordered:'待到貨',partial:'部分到貨',received:'已收齊',cancelled:'已取消餘量'};
const goodsFilters={search:'',state:'',supplier:''};
const goodsMoney=cents=>cents==null?'未定價':centsText(cents);
const goodsLink=(label,path)=>{const a=node('a',label,'button secondary');a.href='#'+path;return a;};
const goodsButton=(label,work)=>stockButton('goods-purchases',label,work);
function goodsField(parent,label,value='',type='text',choices=null,required=true){
 const wrap=node('label',label),input=node(choices?'select':'input');
 if(choices){for(const [v,t] of choices)input.append(new Option(t,v));}else input.type=type;
 input.value=value;input.required=required;if(type==='text')input.maxLength=200;
 wrap.append(input);parent.append(wrap);return input;
}
// Keep a failed request's operation ID when retrying the same payload.
function goodsSubmitter(){let key='',id='';return async(url,body)=>{const next=JSON.stringify([url,body]);if(next!==key){key=next;id=operation();}return request(url,{...body,operation_id:id});};}
function goodsForm(target,title,label,save){
 target.append(node('h3',title));const form=node('form',undefined,'goods-form'),fields=node('div',undefined,'editor-grid');
 const actions=node('div',undefined,'form-actions'),submit=node('button',label);submit.type='submit';actions.append(submit);form.append(fields,actions);target.append(form);
 const send=goodsSubmitter();form.addEventListener('submit',e=>{e.preventDefault();stockAction('goods-purchases',submit,async()=>{
  const result=await save(send);navigatePage('goods-purchases/'+result.id);stockMessage('goods-purchases','已保存。');
 });});return {form,fields,actions};
}
async function renderGoodsPurchases(parts){
 const root=$('goods-purchases'),sequence=++goodsSequence;
 root.replaceChildren(node('h2','商品採購／叫貨'));const notice=node('p');notice.id='goods-purchases-notice';notice.hidden=true;notice.setAttribute('role','status');root.append(notice);
 const target=node('div',undefined,parts[1]?'record-detail-page':'record-list-page');root.append(target);target.append(node('p','載入採購資料中…'));
 try{
  const data=await request('/api/goods-purchases');if(sequence!==goodsSequence||location.hash.split('/')[0]!=='#goods-purchases')return;
  goodsData=data;target.replaceChildren();
  if(!parts[1]){goodsList(target);return;}
  target.append(goodsLink('返回採購列表','goods-purchases'));
  if(parts[1]==='new'){goodsEdit(target);return;}
  const p=data.purchases.find(x=>x.id===parts[1]);if(!p)throw new Error('找不到這張採購單，請返回列表核對。');
  if(parts[2])target.append(goodsLink('返回單據','goods-purchases/'+p.id));
  if(parts[2]==='edit')goodsEdit(target,p);
  else if(parts[2]==='receive')goodsReceive(target,p);
  else if(parts[2]==='schedule')goodsSchedule(target,p);
  else if(parts[2]==='cancel')goodsCancel(target,p);
  else goodsDetail(target,p);
 }catch(error){if(sequence===goodsSequence){target.replaceChildren(goodsLink('返回採購列表','goods-purchases'));stockMessage('goods-purchases',error.message,true);target.append(goodsButton('重新載入',()=>renderGoodsPurchases(parts)));}}
}
function goodsList(target){
 const heading=node('div',undefined,'section-heading goods-list-heading');heading.append(goodsLink('新增商品採購','goods-purchases/new'));target.append(heading);
 target.append(node('p','先記錄叫貨，收到商品時分批入庫；未到貨數量不計入庫存。','help'));
 const filters=node('form',undefined,'search-bar goods-filters');
 const search=goodsField(filters,'搜尋單號、商品或 SKU',goodsFilters.search,'search',null,false);
 const state=goodsField(filters,'狀態',goodsFilters.state,'text',[['','全部狀態'],...Object.entries(goodsStates)],false);
 const supplier=goodsField(filters,'供應商',goodsFilters.supplier,'text',[['','全部供應商'],...goodsData.suppliers.map(s=>[s.id,s.name])],false);
 const submit=node('button','查詢');submit.type='submit';filters.append(submit);target.append(filters);
 const result=node('div');target.append(result);
 const draw=()=>{
  Object.assign(goodsFilters,{search:search.value,state:state.value,supplier:supplier.value});const q=search.value.trim().toLowerCase();
  const rows=goodsData.purchases.filter(p=>(!state.value||p.state===state.value)&&(!supplier.value||p.supplier_id===supplier.value)&&
   [p.number,p.supplier_name,...p.lines.flatMap(l=>[l.name,l.sku])].join(' ').toLowerCase().includes(q));
  result.replaceChildren(node('p',`共 ${rows.length} 張採購單`,'help'));
  if(!rows.length){result.append(node('p','尚無符合條件的採購單。可調整查詢條件或新增商品採購。'));return;}
  const table=materialTable(['採購單號','供應商','狀態','預計到貨','訂購','已收','待收','操作'],rows.map(p=>{
   const view=goodsLink('查看','goods-purchases/'+p.id);view.setAttribute('aria-label','查看採購單 '+p.number);
   return [p.number,p.supplier_name,statusBadge(goodsListStates[p.state]),p.expected_date||'未排定',
    ...['quantity','received','pending'].map(k=>String(p.lines.reduce((n,l)=>n+l[k],0))),view];
  }));table.classList.add('goods-purchase-table');result.append(table);
 };filters.addEventListener('submit',e=>{e.preventDefault();draw();});draw();
}
function goodsDetail(target,p){
 const heading=node('div',undefined,'document-heading'),identity=node('div',undefined,'document-identity'),actions=node('div',undefined,'document-actions');
 identity.append(node('h3',p.number),statusBadge(goodsStates[p.state]));heading.append(identity,actions);target.append(heading);
 if(p.state==='draft'){
  actions.append(goodsLink('編輯草稿','goods-purchases/'+p.id+'/edit'));
  const send=goodsSubmitter();actions.append(goodsButton('確認叫貨',async()=>{if(!confirm('確認已向供應商叫貨？確認後商品與數量將固定，尚不增加庫存。'))return;
   await send('/api/goods-purchases/'+p.id+'/confirm',{expected_version:p.version});await renderGoodsPurchases(['goods-purchases',p.id]);}));
 }
 if(['ordered','partial'].includes(p.state))actions.append(goodsLink('登記本次收貨','goods-purchases/'+p.id+'/receive'));
 if(['draft','ordered','partial'].includes(p.state))actions.append(goodsLink('調整交期／備註','goods-purchases/'+p.id+'/schedule'),goodsLink(p.state==='draft'?'取消草稿':'取消未收部分','goods-purchases/'+p.id+'/cancel'));
 target.append(stockFacts([['供應商',p.supplier_name],['預計到貨',p.expected_date||'未排定'],['叫貨日期',p.ordered_at?dateTime(p.ordered_at):'尚未叫貨']]));
 if(p.comment)target.append(node('p',p.comment,'detail-note'));
 if(p.cancel_reason)target.append(node('p','取消原因：'+p.cancel_reason,'detail-note'));
 target.append(materialTable(['商品／SKU','訂購數量','參考單價','已收','待收','取消數量'],p.lines.map(l=>[
  `${l.name} / ${l.sku}`,String(l.quantity),goodsMoney(l.quoted_price_cents),String(l.received),String(l.pending),String(l.cancelled)])));
 target.append(node('h3','分批收貨紀錄'));
 if(!p.receipts.length)target.append(node('p','尚未收貨，這張採購單尚未建立庫存。','help'));
 else target.append(materialTable(['入庫單號','實收日期','本批數量','本批運費','費用狀態','操作'],p.receipts.map(r=>{
  const actions=node('div',undefined,'goods-receipt-actions');actions.append(goodsLink('查看入庫','stock/'+r.id),goodsLink('配櫃','putaway/'+r.id));
  return [r.number,dateTime(r.confirmed_at),String(r.lines.reduce((n,l)=>n+l.quantity,0)),r.freight_cents==null?'待填費用':goodsMoney(r.freight_cents),r.cost_status==='pending'?'待填費用':'已填完成',actions];
 })));
 const log=node('details',undefined,'detail-supporting');log.append(node('summary',`操作歷史（${p.history.length} 筆）`));
 const names={create:'建立草稿',edit:'修改草稿',confirm:'確認叫貨',schedule:'調整交期／備註',receive:'分批收貨',cancel:'取消未收部分'};
 for(const h of p.history){const entry=node('div',undefined,'goods-history-entry');entry.append(node('h4',`${dateTime(h.time)} · ${names[h.action]||h.action}`));
  const before=h.details.before,after=h.details.after;
  if(h.details.receipt_id)entry.append(node('p','入庫單號：'+h.details.receipt_number),goodsLink('查看入庫','stock/'+h.details.receipt_id));
  if(before&&h.action==='schedule')entry.append(node('p',`預計到貨：${before.expected_date||'未排定'} → ${after.expected_date||'未排定'}`),node('p',`備註：${before.comment||'無'} → ${after.comment||'無'}`));
  if(after?.cancel_reason&&h.action==='cancel')entry.append(node('p',after.cancel_reason));
  if(h.action==='edit'){
   entry.append(node('p',`單號：${before.number} → ${after.number}；供應商：${before.supplier_name} → ${after.supplier_name}`));
   entry.append(materialTable(['商品／SKU','原訂購數量','新訂購數量','原參考單價','新參考單價'],[...new Set([...before.lines,...after.lines].map(l=>l.product_id))].map(id=>{
    const a=before.lines.find(l=>l.product_id===id),b=after.lines.find(l=>l.product_id===id);return [`${(b||a).name} / ${(b||a).sku}`,String(a?.quantity||0),String(b?.quantity||0),a?goodsMoney(a.quoted_price_cents):'—',b?goodsMoney(b.quoted_price_cents):'—'];})));
  }
  log.append(entry);
 }target.append(log);
}
function goodsEdit(target,p=null){
 if(p&&p.state!=='draft')throw new Error('只有草稿可以編輯採購品項。');
 let number,supplier,expected,comment;const rows=[];
 const f=goodsForm(target,p?'編輯採購草稿':'新增商品採購','保存草稿',send=>send('/api/goods-purchases',{
  ...(p?{id:p.id,expected_version:p.version}:{}),number:number.value,supplier_id:supplier.value,expected_date:expected.value,comment:comment.value,
  lines:rows.filter(r=>r.row.isConnected).map(r=>({product_id:r.product.value,quantity:Number(r.quantity.value),quoted_price:r.price.value}))}));
 number=goodsField(f.fields,'採購單號',p?.number||'');number.maxLength=100;
 supplier=goodsField(f.fields,'供應商',p?.supplier_id||'','text',[['','請選擇供應商'],...goodsData.suppliers.filter(s=>s.active).map(s=>[s.id,s.name])]);
 f.fields.append(goodsLink('管理供應商','materials/suppliers'));
 expected=goodsField(f.fields,'預計到貨日',p?.expected_date||'','date',null,false);
 comment=goodsField(f.fields,'備註',p?.comment||'','text',null,false);comment.maxLength=2000;
 const lineBox=node('div');f.form.insertBefore(lineBox,f.actions);
 const add=line=>{const row=node('div',undefined,'goods-line');lineBox.append(row);
  const product=goodsField(row,'商品 SKU',line?.product_id||'','text',[['','請選擇商品'],...goodsData.products.filter(x=>x.active).map(x=>[x.id,`${x.name} / ${x.sku}`])]);
  const quantity=goodsField(row,'訂購數量',line?.quantity||1,'number');quantity.min=1;quantity.max=10000;quantity.step=1;
  const price=goodsField(row,'參考單價（可留白）',line?.quoted_price_cents==null?'':goodsMoney(line.quoted_price_cents),'text',null,false);price.inputMode='decimal';
  row.append(goodsButton('移除',()=>row.remove()));rows.push({row,product,quantity,price});
 };for(const line of p?.lines||[null])add(line);
 const addButton=goodsButton('新增商品',()=>add(null));f.form.insertBefore(addButton,f.actions);
 f.form.insertBefore(node('p','參考單價可留白；實際成本將於每批收貨時確認。','help'),f.actions);
}
function goodsSchedule(target,p){
 if(!['draft','ordered','partial'].includes(p.state))throw new Error('已結案採購單不可修改。');
 let expected,comment;const f=goodsForm(target,'調整交期與備註','保存變更',send=>send(`/api/goods-purchases/${p.id}/schedule`,{expected_version:p.version,expected_date:expected.value,comment:comment.value}));
 expected=goodsField(f.fields,'預計到貨日',p.expected_date,'date',null,false);comment=goodsField(f.fields,'備註',p.comment,'text',null,false);comment.maxLength=2000;
}
function goodsCancel(target,p){
 if(!['draft','ordered','partial'].includes(p.state))throw new Error('這張採購單已結案。');
 target.append(node('p',`將取消 ${p.lines.reduce((n,l)=>n+l.quantity-l.received,0)} 件未收數量，已收 ${p.lines.reduce((n,l)=>n+l.received,0)} 件庫存保持原狀。`));
 let reason;const f=goodsForm(target,'取消未收部分並結案','確認取消未收部分',send=>send(`/api/goods-purchases/${p.id}/cancel`,{expected_version:p.version,reason:reason.value}));
 reason=goodsField(f.fields,'取消原因');reason.maxLength=2000;
}
function goodsReceive(target,p){
 if(!['ordered','partial'].includes(p.state))throw new Error('這張採購單目前不能收貨。');
 let number,freight,tax,allocation;const rows=[];
 const f=goodsForm(target,`登記本次收貨 · ${p.number}`,'確認本批收貨並入庫',async send=>{
  const lines=rows.filter(r=>Number(r.quantity.value)>0).map(r=>({product_id:r.line.product_id,quantity:Number(r.quantity.value),unit_price:r.price.value}));
  if(!lines.length)throw new Error('請填至少一項本次實收數量。');
  const result=await send(`/api/goods-purchases/${p.id}/receive`,{expected_version:p.version,number:number.value,freight:freight.value,tax:tax.value,allocation:allocation.value,lines});
  await refreshStock();return result;
 });
 number=goodsField(f.fields,'本次入庫單號');number.maxLength=100;
 freight=goodsField(f.fields,'本批實際運費（無運費請填 0）','','text',null,false);freight.inputMode='decimal';
 tax=goodsField(f.fields,'本批稅費（可待補）','0.00','text',null,false);tax.inputMode='decimal';
 allocation=goodsField(f.fields,'運費分攤方式','quantity','text',[['quantity','依件數'],['value','依貨款金額']]);
 const box=node('div');f.form.insertBefore(box,f.actions);
 for(const line of p.lines.filter(l=>l.pending>0)){
  const row=node('div',undefined,'goods-line goods-receive-line');box.append(row);row.append(node('p',`${line.name} / ${line.sku} · 最多 ${line.pending} 件`));
  const quantity=goodsField(row,'本次實收數量',0,'number');quantity.min=0;quantity.max=line.pending;quantity.step=1;
  const price=goodsField(row,'本批實際單價','','text',null,false);price.inputMode='decimal';
  rows.push({line,quantity,price});
 }
 f.form.insertBefore(node('p','未收到的品項填 0。費用可留白，入庫單會標示「待填費用」；明確 0 元是有效費用。確認後建立本批庫存與入庫單，再前往配櫃。','help'),f.actions);
}
