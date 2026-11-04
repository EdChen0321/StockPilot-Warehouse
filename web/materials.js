'use strict';
let materialData={materials:[],suppliers:[],boms:[],plans:[],purchases:[],movements:[],plan_versions:[]};
let materialTab='materials', materialSelection=null, materialSequence=0;
const materialTabs={materials:'物料',suppliers:'供應商',boms:'商品 BOM',plans:'生產計畫',purchases:'採購單',summary:'需求彙總'};
const materialFilters={};
const materialStates={draft:'草稿',confirmed:'已預留',ordered:'已叫貨',partial:'部分到貨',received:'全部到貨',cancelled:'已取消'};
const milli=value=>{const [a,b='']=String(value).split('.');return BigInt(a)*1000n+BigInt(b.padEnd(3,'0'));};
const decimal=value=>`${value/1000n}.${String(value%1000n).padStart(3,'0')}`.replace(/\.?0+$/,'');
const subtract=(a,b)=>decimal(milli(a)-milli(b));
const materialName=id=>{const m=materialData.materials.find(x=>x.id===id);return m?`${m.name} · ${m.code}`:id;};
const supplierName=id=>materialData.suppliers.find(x=>x.id===id)?.name||id;
function materialButton(label,work,secondary=true){const b=stockButton('materials',label,work);if(secondary)b.classList.add('secondary');return b;}
function materialBadge(state){const b=statusBadge(materialStates[state]||({active:'啟用',inactive:'停用'}[state])||state);b.dataset.state=state;return b;}
function materialField(form,label,value='',choices=null,type='text'){
  const wrap=node('label',label), input=node(choices?'select':'input');
  if(choices){input.append(new Option('請選擇',''));for(const c of choices)input.append(new Option(c.label,c.value));}
  else {input.type=type;input.maxLength=200;}
  input.required=true;input.value=value;wrap.append(input);form.append(wrap);return input;
}
function materialForm(title,saveText,save){
  if(window.guardDetailReplacement&&!window.guardDetailReplacement())throw new Error('已保留未保存變更，未切換操作。');
  const target=$('material-detail');target.replaceChildren(node('h3',title));
  const form=node('form',undefined,'material-form'), fields=node('div',undefined,'editor-grid');form.append(fields);
  const actions=node('div',undefined,'form-actions'), submit=node('button',saveText);submit.type='submit';actions.append(submit);form.append(actions);target.append(form);
  form.addEventListener('submit',e=>{e.preventDefault();stockAction('materials',submit,async()=>{await save();await refreshMaterials();stockMessage('materials','已保存。');});});
  return {form,fields,actions};
}
function materialLines(parent, initial=[], allowed=null, unit=false){
  const box=node('div');parent.append(box);
  const choices=()=>materialData.materials.filter(m=>m.active&&(allowed===null||allowed.includes(m.id)));
  function add(line={}){
    const row=node('div',undefined,'document-line');
    const select=materialField(row,'物料',line.material_id||'',choices().map(m=>({value:m.id,label:`${m.name} · ${m.code}（${m.unit}）`})));
    const q=materialField(row,unit?'每件用量':'本次數量',line.quantity??'');q.inputMode='decimal';
    row.append(materialButton('移除',async()=>row.remove()));box.append(row);
  }
  for(const line of initial)add(line);
  if(!initial.length)add();
  parent.append(materialButton('加入物料',async()=>add()));
  return ()=>[...box.children].map(row=>{const id=row.querySelector('select').value;return {material_id:id,quantity:row.querySelector('input').value,unit:materialData.materials.find(m=>m.id===id)?.unit};});
}
function materialTable(headers, records){
  const wrap=node('div',undefined,'table-scroll'), table=node('table'), head=node('thead'), row=node('tr');
  const numeric=label=>/在庫|數量|用量|需求|預留|可用|缺口|在途|異動前|異動後|本次量|已發|已退|已收|待收|^採購$|版本|品項/.test(label);
  for(const label of headers){const th=node('th',label,numeric(label)?'numeric':'');th.scope='col';row.append(th);}head.append(row);table.append(head);
  const body=node('tbody');for(const cells of records){const tr=node('tr');cells.forEach((value,index)=>{const td=node('td',undefined,numeric(headers[index])?'numeric':'');td.append(typeof value==='string'?document.createTextNode(value):value);tr.append(td);});body.append(tr);}table.append(body);wrap.append(table);return wrap;
}
function materialLineTable(lines,fields){
  return materialTable(['物料',...fields.map(x=>x[1]),'單位'],lines.map(line=>[materialName(line.material_id),...fields.map(x=>String(line[x[0]])),line.unit]));
}
async function refreshMaterials(){
  const sequence=++materialSequence;const data=await request('/api/materials');if(sequence!==materialSequence)return;materialData=data;renderMaterialList();
  if(materialTab==='summary')await renderMaterialSummary();
  else if(materialSelection&&location.hash==='#materials/'+materialTab+'/'+encodeURIComponent(materialSelection)&&!window.pageHasUnsavedChanges?.())showMaterialRecord(materialSelection);
  window.dispatchEvent(new Event('materials-refreshed'));
}
function renderMaterialList(){
  $('materials-title').textContent=materialTabs[materialTab]==='需求彙總'?'需求彙總':`${materialTabs[materialTab]}管理`;
  const tabs=$('material-tabs');tabs.replaceChildren();
  for(const [key,label] of Object.entries(materialTabs)){const link=node('a',label);link.href='#materials/'+key;if(key===materialTab)link.setAttribute('aria-current','page');tabs.append(link);}
  $('material-workspace').hidden=materialTab==='summary';$('material-summary').hidden=materialTab!=='summary';$('material-new').hidden=materialTab==='summary'||location.hash.split('/').length>2;
  $('material-new').textContent={materials:'新增物料',suppliers:'新增供應商',boms:'建立／修訂 BOM',plans:'新增生產計畫',purchases:'新增採購單'}[materialTab]||'';
  const target=$('material-list');target.replaceChildren();
  let items=Array.isArray(materialData[materialTab])?materialData[materialTab]:[];
  if(materialTab==='boms')items=materialData.boms.filter(b=>!materialData.boms.some(other=>other.product_id===b.product_id&&other.version>b.version));
  if(materialTab==='summary')return;
  const selectedTab=materialTab,filter=materialFilters[selectedTab]||{q:'',state:''};
  const search=node('form',undefined,'search-bar');
  const query=materialField(search,'搜尋',filter.q);query.required=false;query.type='search';query.placeholder=selectedTab==='purchases'?'採購單號、供應商或物料':'名稱、代碼或單號';
  const choices=['materials','suppliers'].includes(selectedTab)?[{value:'active',label:'啟用'},{value:'inactive',label:'停用'}]:[...new Set(items.map(item=>item.state).filter(Boolean))].map(value=>({value,label:materialStates[value]||value}));
  let status=null;if(choices.length){status=materialField(search,'狀態',filter.state,choices);status.required=false;status.options[0].textContent='全部狀態';}
  const submit=node('button','查詢');submit.type='submit';search.append(submit);target.append(search);
  search.addEventListener('submit',event=>{event.preventDefault();materialFilters[selectedTab]={q:query.value,state:status?.value||''};renderMaterialList();});
  const headers={materials:['物料名稱','代碼','供應商','在庫','單位','狀態','操作'],suppliers:['供應商','狀態','操作'],boms:['商品','BOM 版本','物料品項','操作'],plans:['生產計畫','商品','產品工廠','生產數量','已收成品','待收成品','狀態','操作'],purchases:['採購單','供應商','到貨進度','狀態','操作']}[selectedTab];
  const rows=[];
  for(const item of items){
    const key=item.id||item.product_id,state=item.state||(item.active===false||item.active===0?'inactive':'active');
    const searchTerms=[item.name||'',item.code||'',item.number||'',item.factory_name||'',item.product_id?productName(item.product_id):'',item.supplier_id?supplierName(item.supplier_id):'',...(item.lines||[]).map(line=>materialName(line.material_id))];
    if(!documentMatches({number:item.number||'',state},filter,searchTerms))continue;
    const view=materialButton('查看',async()=>navigatePage('materials/'+selectedTab+'/'+encodeURIComponent(key)));
    const badge=materialBadge(state);
    let cells;
    if(selectedTab==='materials')cells=[item.name,item.code,supplierName(item.supplier_id),item.on_hand,item.unit,badge,view];
    else if(selectedTab==='suppliers')cells=[item.name,badge,view];
    else if(selectedTab==='boms')cells=[productName(item.product_id),String(item.version),String(item.lines.length),view];
    else if(selectedTab==='plans')cells=[item.number,productName(item.product_id),item.factory_name||'既有 BOM 計畫',String(item.quantity),item.mode==='reported'?String(item.received):'—',item.mode==='reported'?String(item.pending):'—',item.mode==='reported'?statusBadge(({draft:'草稿',ordered:'已下單',partial:'部分成品到貨',received:'成品已收齊',cancelled:'已取消未交量'})[item.state]):badge,view];
    else {const complete=item.lines.filter(line=>milli(line.received)>=milli(line.ordered)).length;cells=[item.number,supplierName(item.supplier_id),`${complete} / ${item.lines.length} 項到齊`,badge,view];}
    rows.push(cells);
  }
  if(rows.length){target.append(node('p',`共 ${rows.length} 筆`,'material-result-count'),materialTable(headers,rows));}
  else target.append(node('p',items.length?'沒有符合條件的紀錄，請調整搜尋或狀態。':'尚無資料，請使用上方新增。','material-empty'));
}
function editMaterialMaster(item=null){
  let name,code,unit,supplier,active;
  const isSupplier=materialTab==='suppliers';
  const {fields}=materialForm(isSupplier?'供應商資料':'物料資料','保存',async()=>{
    const body={operation_id:operation(),name:name.value,...(item?{id:item.id,expected_version:item.version,active:active.checked}:{})};
    if(!isSupplier)Object.assign(body,{code:code.value,unit:unit.value,supplier_id:supplier.value});
    const saved=await request('/api/materials/'+(isSupplier?'suppliers':'items'),body);materialSelection=saved.id;
  });
  name=materialField(fields,isSupplier?'供應商名稱':'物料名稱',item?.name||'');
  if(!isSupplier){code=materialField(fields,'物料代碼',item?.code||'');unit=materialField(fields,'基礎單位',item?.unit||'');if(item)unit.readOnly=true;supplier=materialField(fields,'供應商',item?.supplier_id||'',materialData.suppliers.filter(s=>s.active).map(s=>({value:s.id,label:s.name})));}
  if(item){const label=node('label','啟用','checkbox');active=node('input');active.type='checkbox';active.checked=!!item.active;label.prepend(active);fields.append(label);}
  if(!isSupplier&&item){const history=node('section',undefined,'content-section');history.append(node('h3','物料歷史帳'));for(const event of materialData.movements){const lines=event.lines.filter(l=>l.material_id===item.id);if(lines.length)history.append(node('p',`${dateTime(event.time)} · ${event.number} · ${{receipt:'收貨',issue:'發料',return:'實退',consume:'實際用料'}[event.kind]}`),materialLineTable(lines,[['quantity','數量'],['before','異動前'],['after','異動後']]));}$('material-detail').append(history);}
}
function editBom(productId=''){
  const current=materialData.boms.filter(b=>b.product_id===productId).at(-1);let select,lines;
  const {fields,form,actions}=materialForm('商品 BOM','保存 BOM 版本',async()=>{const saved=await request('/api/materials/boms',{operation_id:operation(),product_id:select.value,expected_version:current?.version||0,lines:lines()});materialSelection=saved.product_id;});
  select=materialField(fields,'生產商品',productId,boot.products.filter(p=>p.kind==='product'&&p.active).map(p=>({value:p.id,label:`${p.name} · ${p.sku}`})));
  select.addEventListener('change',()=>editBom(select.value));
  const linesBox=node('div');form.insertBefore(linesBox,actions);lines=materialLines(linesBox,current?.lines||[],null,true);
  if(current)fields.append(node('p',`目前版本 ${current.version}，既有計畫使用原快照。`));
}
function newMaterialPlan(){
  let number,p,qty,factory,date,comment;const {fields}=materialForm('新增工廠生產單','保存生產草稿',async()=>{const saved=await request('/api/materials/plans',{operation_id:operation(),number:number.value,product_id:p.value,quantity:Number(qty.value),factory_id:factory.value,expected_date:date.value,comment:comment.value});materialSelection=saved.id;});
  number=materialField(fields,'計畫單號');p=materialField(fields,'生產商品','',boot.products.filter(p=>p.kind==='product'&&p.active).map(p=>({value:p.id,label:`${p.name} · ${p.sku}`})));qty=materialField(fields,'預計生產數量','1',null,'number');qty.min='1';qty.step='1';
  factory=materialField(fields,'產品工廠','',materialData.suppliers.filter(s=>s.active).map(s=>({value:s.id,label:s.name})));
  date=materialField(fields,'預計成品到貨日','',null,'date');date.required=false;
  comment=materialField(fields,'備註');comment.required=false;
  fields.append(node('p','依工廠回報叫料，不需先建立 BOM。確認生產不會扣料或增加成品庫存。'),productionLink('新增／管理工廠與供應商','materials/suppliers'));
}
function newMaterialPurchase(plan=null){
  let number,supplier,lines;const {fields,form,actions}=materialForm('新增物料採購單','保存採購草稿',async()=>{const saved=await request('/api/materials/purchases',{operation_id:operation(),number:number.value,supplier_id:supplier.value,lines:lines(),...(plan?{plan_id:plan.id}:{})});materialSelection=saved.id;materialTab='purchases';navigatePage('materials/purchases/'+saved.id);});
  number=materialField(fields,'採購單號');supplier=materialField(fields,'採購供應商','',materialData.suppliers.filter(s=>s.active).map(s=>({value:s.id,label:s.name})));
  if(plan)fields.append(stockFacts([['來源生產單',plan.number],['送往產品工廠',plan.factory_name]]),node('p','填寫工廠回報的缺料數量。實收才增加物料總庫存，不分存放位置。'));
  const box=node('div');form.insertBefore(box,actions);
  function update(){box.replaceChildren();lines=materialLines(box,[],materialData.materials.filter(m=>m.supplier_id===supplier.value).map(m=>m.id));}
  supplier.addEventListener('change',update);update();
}
function movementForm(kind,source){
  const labels={receipt:'物料驗收入庫',issue:'實際發料',return:'實際退料'};let number;
  const {fields,form,actions}=materialForm(labels[kind],'確認'+labels[kind],async()=>{
    const lines=inputs.filter(x=>x.input.value!=='').map(x=>({material_id:x.id,quantity:x.input.value}));
    await request('/api/materials/movements',{operation_id:operation(),number:number.value,kind,lines,...(kind==='receipt'?{purchase_id:source.id}:kind==='issue'?{plan_id:source.id}:{source_id:source.id})});
  });
  number=materialField(fields,'本次異動單號');const inputs=[];
  for(const line of source.lines){let remaining=kind==='receipt'?subtract(line.ordered,line.received):kind==='issue'?line.reserved:line.quantity;
    if(kind==='return')for(const r of materialData.movements.filter(r=>r.source_id===source.id))for(const l of r.lines)if(l.material_id===line.material_id)remaining=subtract(remaining,l.quantity);
    if(milli(remaining)<=0n)continue;
    const input=materialField(fields,`${materialName(line.material_id)}（最多 ${remaining} ${line.unit}）`,'');input.required=false;input.inputMode='decimal';inputs.push({id:line.material_id,input});
  }
  const hint=node('p','只填本次實際數量；未動到的物料留空。');form.insertBefore(hint,actions);
}
function showMaterialRecord(id){
  materialSelection=id;renderMaterialList();
  if(materialTab==='materials'||materialTab==='suppliers')return editMaterialMaster(materialData[materialTab].find(x=>x.id===id));
  if(materialTab==='boms')return editBom(id);
  const item=materialData[materialTab].find(x=>x.id===id);if(!item)return;
  if(materialTab==='plans'&&item.mode==='reported')return showProductionRecord(item);
  const target=$('material-detail');target.replaceChildren();
  const isPlan=materialTab==='plans', path=`/api/materials/${materialTab}/${id}`;
  const header=node('header',undefined,'document-heading'),identity=node('div',undefined,'document-identity'),actions=node('div',undefined,'document-actions');
  identity.append(node('h3',item.number),materialBadge(item.state));header.append(identity,actions);target.append(header);
  target.append(stockFacts(isPlan?[['生產商品',productName(item.product_id)],['生產數量',`${item.quantity} 件`],['BOM 版本',String(item.bom_version)]]:[['供應商',supplierName(item.supplier_id)],['採購品項',`${item.lines.length} 項`],['已到齊品項',`${item.lines.filter(l=>milli(l.received)>=milli(l.ordered)).length} 項`]]));
  if(item.plan_id)target.append(stockFacts([['送往產品工廠',item.factory_name]]),productionLink('查看來源生產單','materials/plans/'+item.plan_id));
  const lines=node('section',undefined,'content-section');lines.append(node('h3',isPlan?'物料需求與發料進度':'採購與到貨明細'),materialLineTable(isPlan?item.lines:item.lines.map(l=>({...l,remaining:subtract(l.ordered,l.received)})),isPlan?[['required','需求'],['reserved','預留'],['issued','已發'],['returned','已退']]:[['ordered','採購'],['received','已收'],['remaining','待收']]));target.append(lines);
  const act=(action,label)=>{const button=materialButton(label,async()=>{await request(path+'/'+action,{operation_id:operation()});await refreshMaterials();stockMessage('materials','狀態已更新。');},action==='cancel');if(action==='cancel')button.classList.add('danger');actions.append(button);};
  if(item.state==='draft')act('confirm',isPlan?'確認並預留':'確認叫貨');
  if(isPlan&&item.state==='confirmed')actions.append(materialButton('登記發料',async()=>movementForm('issue',item),false));
  if(!isPlan&&['ordered','partial'].includes(item.state))actions.append(materialButton('登記本次收貨',async()=>movementForm('receipt',item),false));
  if(!['cancelled','received'].includes(item.state))act('cancel',isPlan?'取消未發需求':'取消未收部分');
  if(isPlan&&item.state!=='cancelled'){
    const details=node('section',undefined,'content-section');details.append(node('h3','修訂生產數量'));const form=node('form',undefined,'editor-grid');const input=materialField(form,'新生產數量',String(item.quantity),null,'number');input.min='1';input.step='1';const save=node('button','保存修訂');save.type='submit';form.append(save);form.addEventListener('submit',e=>{e.preventDefault();stockAction('materials',save,async()=>{await request(path+'/revise',{operation_id:operation(),expected_version:item.version,quantity:Number(input.value)});await refreshMaterials();stockMessage('materials','修訂及預留已一起保存。');});});details.append(form);target.append(details);
  }
  const history=node('section',undefined,'content-section');history.append(node('h3','來源與實物紀錄'));
  const movements=materialData.movements.filter(m=>isPlan?m.plan_id===id:m.purchase_id===id);
  if(!movements.length)history.append(node('p','尚無實物異動紀錄。','material-empty'));
  for(const event of movements){
    const entry=node('section',undefined,'material-history-entry'),heading=node('div',undefined,'section-heading');
    heading.append(node('h4',`${event.number} · ${{receipt:'收貨',issue:'發料',return:'實退',consume:'實際用料'}[event.kind]}`));
    if(event.kind==='issue')heading.append(materialButton('登記此筆實退料',async()=>movementForm('return',event)));
    entry.append(heading,node('p',dateTime(event.time),'material-result-count'),materialLineTable(event.lines,[['quantity','本次量'],['before','異動前'],['after','異動後']]));history.append(entry);
  }
  if(isPlan)for(const v of materialData.plan_versions.filter(v=>v.plan_id===id))history.append(node('p',`版本 ${v.version} · ${v.snapshot.quantity} 件 · ${materialStates[v.snapshot.state]}`));
  target.append(history);
}
function productionLink(label,path){const link=node('a',label,'button secondary');link.href='#'+path;return link;}
function productionForm(item,action,title,setup){
  let values;const op=operation();
  const f=materialForm(title,'確認保存',async()=>{
    await request(`/api/materials/plans/${item.id}/${action}`,{operation_id:op,expected_version:item.version,...values()});
    if(action==='receive')await refreshStock();
    materialSelection=item.id;showMaterialRecordAfterSave=item.id;
  });
  values=setup(f.fields);return f;
}
let showMaterialRecordAfterSave=null;
window.addEventListener('materials-refreshed',()=>{
  if(showMaterialRecordAfterSave){const id=showMaterialRecordAfterSave;showMaterialRecordAfterSave=null;showMaterialRecord(id);}
});
function showProductionRecord(item){
  const target=$('material-detail');target.replaceChildren();
  const header=node('header',undefined,'document-heading'),identity=node('div',undefined,'document-identity'),actions=node('div',undefined,'document-actions');
  const label={draft:'草稿',ordered:'已下單',partial:'部分成品到貨',received:'成品已收齊',cancelled:'已取消未交量'}[item.state];
  identity.append(node('h3',item.number),statusBadge(label));header.append(identity,actions);target.append(header);
  target.append(stockFacts([['產品工廠',item.factory_name],['生產商品',productName(item.product_id)],['訂購成品',`${item.quantity} 件`],['已收成品',`${item.received} 件`],['待收成品',`${item.pending} 件`],['取消未交量',`${item.cancelled_quantity||0} 件`],['預計到貨',item.expected_date||'未填'],['備註',item.comment||'—']]));
  if(item.cancel_reason)target.append(node('p','取消原因：'+item.cancel_reason));
  if(item.state==='draft')actions.append(materialButton('確認生產下單',async()=>{
    await request(`/api/materials/plans/${item.id}/confirm`,{operation_id:operation(),expected_version:item.version});await refreshMaterials();
  },false));
  if(['draft','ordered','partial'].includes(item.state)){
    actions.append(materialButton('依工廠回報叫料',async()=>newMaterialPurchase(item)));
    actions.append(materialButton('調整交期／備註',async()=>productionForm(item,'schedule','調整交期／備註',fields=>{
      const date=materialField(fields,'預計成品到貨日',item.expected_date||'',null,'date');date.required=false;
      const comment=materialField(fields,'備註',item.comment||'');comment.required=false;
      return ()=>({expected_date:date.value,comment:comment.value});
    })));
    actions.append(materialButton('修訂生產數量',async()=>productionForm(item,'revise','修訂生產數量',fields=>{
      const qty=materialField(fields,'成品總數量',String(item.quantity),null,'number');qty.min=String(Math.max(1,item.received));qty.step='1';
      return ()=>({quantity:Number(qty.value)});
    })));
    const cancel=materialButton('取消未交成品',async()=>productionForm(item,'cancel','取消未交成品',fields=>{
      const reason=materialField(fields,'取消原因');fields.append(node('p','保留已收成品與已用物料，不自動退料。'));return ()=>({reason:reason.value});
    }));cancel.classList.add('danger');actions.append(cancel);
  }
  if(['ordered','partial'].includes(item.state))actions.prepend(materialButton('登記本批成品實收',async()=>productionForm(item,'receive','成品實收入庫',fields=>{
    const number=materialField(fields,'本批入庫單號'),qty=materialField(fields,`本批實收（最多 ${item.pending} 件）`,'',null,'number');qty.min='1';qty.max=String(item.pending);qty.step='1';
    fields.append(node('p','保存後建立入庫單。費用可在入庫單補填，配櫃後才可出貨；不自動扣物料。'));
    return ()=>({number:number.value,quantity:Number(qty.value)});
  }),false));
  if(['ordered','partial','received'].includes(item.state))actions.append(materialButton('登記實際用料',async()=>{
    let number,lines;const op=operation();const f=materialForm('登記實際用料','確認扣除物料',async()=>{
      await request('/api/materials/movements',{operation_id:op,number:number.value,kind:'consume',plan_id:item.id,lines:lines()});
      showMaterialRecordAfterSave=item.id;
    });
    number=materialField(f.fields,'本次用料單號');const box=node('div');f.form.insertBefore(box,f.actions);lines=materialLines(box);
    f.fields.append(node('p','按工廠回報的實際用量填寫，扣除物料總庫存，保留未使用餘料。'));
  }));
  target.append(node('h3','關聯物料採購','document-section-title'));
  const purchases=materialData.purchases.filter(p=>p.plan_id===item.id);
  target.append(purchases.length?materialTable(['採購單號','供應商','狀態','操作'],purchases.map(p=>[p.number,supplierName(p.supplier_id),materialBadge(p.state),productionLink('查看採購','materials/purchases/'+p.id)])):node('p','尚無關聯採購，可依工廠回報叫料。'));
  target.append(node('h3','成品實收與入庫','document-section-title'));
  target.append(item.receipts?.length?materialTable(['入庫單號','本批實收','實收時間','操作'],item.receipts.map(r=>[r.number,String(r.lines.reduce((n,l)=>n+l.quantity,0)),dateTime(r.confirmed_at),productionLink('查看入庫／配櫃','stock/'+r.id)])):node('p','尚未收到成品。'));
  target.append(node('h3','實際用料紀錄','document-section-title'));
  const movements=materialData.movements.filter(m=>m.plan_id===item.id&&m.kind==='consume');
  if(!movements.length)target.append(node('p','尚無實際用料紀錄。'));
  for(const m of movements)target.append(node('h4',`${m.number} · ${dateTime(m.time)}`),materialLineTable(m.lines,[['quantity','實際用量'],['before','使用前'],['after','剩餘庫存']]));
  target.append(node('h3','生產單操作紀錄','document-section-title'));
  for(const v of materialData.plan_versions.filter(v=>v.plan_id===item.id))target.append(node('p',`版本 ${v.version} · ${dateTime(v.time)} · 訂購 ${v.snapshot.quantity} 件 · 已收 ${v.snapshot.received||0} 件 · ${{draft:'草稿',ordered:'已下單',partial:'部分成品到貨',received:'成品已收齊',cancelled:'已取消未交量'}[v.snapshot.state]||v.snapshot.state}`));
}
let materialSummaryIds=new Set();
let materialSummarySequence=0;
async function renderMaterialSummary(){
  const target=$('material-summary');target.replaceChildren();const filters=node('section',undefined,'content-section');filters.append(node('h3','篩選生產計畫'));
  const results=node('div');
  const choices=node('div',undefined,'material-plan-choices');
  for(const p of materialData.plans.filter(p=>p.state!=='cancelled')){const label=node('label',`${p.number} · ${productName(p.product_id)} · ${p.quantity} 件`,'checkbox');const check=node('input');check.type='checkbox';check.checked=materialSummaryIds.has(p.id);check.addEventListener('change',()=>{if(check.checked)materialSummaryIds.add(p.id);else materialSummaryIds.delete(p.id);updateMaterialSummary(results).catch(e=>stockMessage('materials',e.message,true));});label.prepend(check);choices.append(label);}filters.append(choices);target.append(filters,results);
  await updateMaterialSummary(results);
}
async function updateMaterialSummary(target){
  const sequence=++materialSummarySequence;
  const query=new URLSearchParams();for(const id of materialSummaryIds)query.append('plan_id',id);
  const data=await request('/api/materials/summary?'+query);
  if(sequence!==materialSummarySequence||!target.isConnected)return;
  target.replaceChildren();
  target.append(node('p',materialSummaryIds.size?`已選 ${data.plan_ids.length} 張計畫`:'全部有效計畫'),node('p','BOM 需求依既有配方計算；工廠回報叫料請到生產單建立關聯採購，不會自動產生 BOM 需求。'),materialTable(['物料','需求','本次已預留','可用','即時缺口','在途','單位'],data.materials.map(m=>[`${m.name} · ${m.code}`,m.demand,m.selected_reserved,m.available,m.shortage,m.incoming,m.unit])));
  const detail=node('section',undefined,'content-section');detail.append(node('h3','計算依據'),node('p','需求扣除已發料；本次預留不重扣。可用＝在庫－全部預留。在途尚未驗收，不算立即可用。'));target.append(detail);
}
$('material-new').addEventListener('click',()=>{materialSelection=null;if(['materials','suppliers'].includes(materialTab))editMaterialMaster();else if(materialTab==='boms')editBom();else if(materialTab==='plans')newMaterialPlan();else newMaterialPurchase();});
window.addEventListener('workspace-refreshed',()=>{materialsLoaded=false;if(location.hash.startsWith('#materials'))refreshMaterials().catch(e=>stockMessage('materials',e.message,true));});
