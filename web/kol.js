'use strict';

const kolFilters={account_id:'',start:'',end:'',platform:'',include_internal:'false'};
const kolOrderFilters={status:'all',utm:''};
const kolPageState=new Map();
let kolRenderSequence=0;
const kolMoney=value=>value===null?'資料未齊':`${formatAmount(value)} 元`;
const kolLineMoney=value=>value==null?'以完整訂單計':kolMoney(value);
const kolSystemId=id=>['general','unattributed'].includes(id);
const kolSystemGroup=g=>g.system_group||kolSystemId(g.id);
const kolOrderStatus=status=>({active:'有效',cancelled:'取消',excluded:'已排除'}[status]||status||'—');
function kolBatchCell(order){return order.status==='excluded'&&order.batch_id?kolLink(order.batch_id,'kol/import/'+encodeURIComponent(order.batch_id)):order.batch_id;}
function kolGroupTable(root,headers,groups,row,key){
 const fixed=groups.filter(kolSystemGroup).sort((a,b)=>Number(b.id==='general')-Number(a.id==='general'));
 const list=node('div',undefined,key==='list'?'kol-partner-list':'kol-statistics-list');root.append(list);
 kolSortedTable(list,headers,groups.filter(g=>!kolSystemGroup(g)),row,key);
 if(fixed.length){const section=node('section',undefined,'kol-classifications');section.append(node('h3','訂單歸屬分類（非 KOL）'),node('p','一般訂單：沒有 UTM 的訂單。待確認歸屬：有 UTM 但尚未匹配 KOL，或早於合作開始的訂單。這些是固定分類，不是合作夥伴。'));const classified=node('div',undefined,key==='list'?'kol-partner-list':'kol-statistics-list');classified.append(kolTable(headers,fixed.map(row)));section.append(classified);root.append(section);}
}
function kolBreadcrumb(root,name,id=''){
 const nav=node('nav',undefined,'kol-breadcrumb');nav.setAttribute('aria-label','目前位置');nav.append(kolLink('KOL 列表','kol'),node('span','›'));
 const current=id?kolLink(name,'kol/detail/'+id):node('span',name);if(!id)current.setAttribute('aria-current','page');nav.append(current);root.append(nav);
}
const kolTime=value=>value?new Date(value).toLocaleString('zh-TW',{timeZone:'Asia/Taipei'}):'—';
function kolLink(label,path){const a=node('a',label);if(path==='kol')path='kol/list';a.href='#'+path+'?'+new URLSearchParams({...kolFilters,...kolOrderFilters});return a;}
function kolAction(label,path){const a=kolLink(label,path);a.className='button '+(path.startsWith('kol/delete/')?'danger':'secondary');return a;}
const kolNumericColumn=title=>/金額|訂單.*數|數量|筆數/.test(title);
function kolTable(headers,rows){
 const frame=node('div',undefined,'table-scroll'),table=node('table'),head=node('thead'),tr=node('tr'),body=node('tbody');
 frame.setAttribute('tabindex','0');frame.setAttribute('role','region');frame.setAttribute('aria-label','資料表，可水平捲動');
 headers.forEach(h=>{const th=node('th',h);th.setAttribute('scope','col');if(kolNumericColumn(h))th.className='numeric';tr.append(th);});head.append(tr);table.append(head,body);frame.append(table);
 for(const cells of rows){const r=node('tr');cells.forEach((v,i)=>{const td=node('td');if(v instanceof Node)td.append(v);else if(headers[i]==='狀態'||headers[i]==='訂單狀態')td.append(statusBadge(v));else td.textContent=String(v??'—');if(kolNumericColumn(headers[i]))td.className='numeric';r.append(td);});body.append(r);}
 if(!rows.length){const r=node('tr'),td=node('td','沒有符合條件的資料。');td.colSpan=headers.length;r.append(td);body.append(r);}
 return frame;
}
function kolInput(form,label,name,value='',type='text',required=false){
 const l=node('label',label),input=node('input');input.name=name;input.type=type;input.value=value;input.required=required;
 if(type==='text')input.maxLength=['name','campaign_name'].includes(name)?200:2000;
 l.append(input);form.append(l);return input;
}
function kolQuery(id='',extra={}){const q=new URLSearchParams({...kolFilters,...extra});if(id)q.set('kol_id',id);return q;}
function kolRerender(parts){const path=parts.join('/');history.replaceState(null,'','#'+(path==='kol'?'kol/list':path)+'?'+new URLSearchParams({...kolFilters,...kolOrderFilters}));return renderKol(parts);}
function kolNavigation(root,page='list'){
 const nav=node('nav',undefined,'kol-tabs');nav.setAttribute('aria-label','KOL 分析功能');
 for(const [label,path,active] of [['KOL 名單','kol',!['statistics','new','imports','import-new','import'].includes(page)],['整體表現統計','kol/statistics',page==='statistics'],['Excel 訂單匯入','kol/imports',['imports','import-new','import'].includes(page)]]){
  const link=kolLink(label,path);if(active)link.setAttribute('aria-current',path==='kol'&&page!=='list'?'location':'page');nav.append(link);
 }root.append(nav);
}
function kolSelect(form,label,name,choices,value){const l=node('label',label),s=node('select');s.name=name;choices.forEach(([v,t])=>s.append(new Option(t,v)));s.value=value;l.append(s);form.append(l);return s;}
function kolFilterForm(root,refresh,kols=[],data=null,sourceLock=''){
 const form=node('form',undefined,'kol-filters'),actions=node('div',undefined,'form-actions');
 const month=kolInput(form,'快速選取月份','month','','month');
 const start=kolInput(form,'開始日期','start',kolFilters.start,'date'),end=kolInput(form,'結束日期','end',kolFilters.end,'date');
 const fields=node('div',undefined,'kol-filter-fields');
 month.onchange=()=>{if(!/^\d{4}-\d{2}$/.test(month.value))return;const [y,m]=month.value.split('-').map(Number);start.value=month.value+'-01';end.value=month.value+'-'+String(new Date(Date.UTC(y,m,0)).getUTCDate()).padStart(2,'0');};
 for(const [label,input] of [['清除開始日期',start],['清除結束日期',end]]){const clear=node('button',label,'secondary');clear.type='button';clear.onclick=()=>{input.value='';month.value='';};actions.append(clear);}
 const label=node('label','訂單來源'),select=node('select');select.name='account_id';select.append(new Option('全部來源',''));
 boot.accounts.forEach(a=>select.append(new Option(a.name,a.id)));select.value=kolFilters.account_id;label.append(select);fields.append(label);
 if(sourceLock){select.value=sourceLock;select.disabled=true;}
 if(!data)kolSelect(fields,'KOL平台','platform',[['','全部平台'],...[...new Set(kols.flatMap(k=>k.platforms))].sort().map(p=>[p,p])],kolFilters.platform);
 if(data){
  kolSelect(fields,'訂單狀態','status',[['all',`全部 (${data.status_counts.all})`],['active',`有效 (${data.status_counts.active})`],['cancelled',`取消 (${data.status_counts.cancelled})`],['excluded',`已排除 (${data.status_counts.excluded||0})`]],kolOrderFilters.status);
  kolSelect(fields,'UTM標籤','utm',[['','全部UTM'],...Object.entries(data.utm_counts).filter(([tag])=>tag).map(([tag,n])=>[tag,`${tag} (${n})`])],kolOrderFilters.utm);
 }
 kolSelect(fields,'內部訂單','include_internal',[['false','排除內部訂單'],['true','包含內部訂單']],kolFilters.include_internal);form.append(fields);
 const b=node('button','查詢');b.type='submit';actions.prepend(b);form.append(actions);root.append(form);
 form.onsubmit=e=>{e.preventDefault();for(const [k,v] of new FormData(form)){if(Object.hasOwn(kolFilters,k))kolFilters[k]=v;if(Object.hasOwn(kolOrderFilters,k))kolOrderFilters[k]=v;}kolPageState.clear();return refresh();};
}
function kolListFilters(root,refresh,kols,data=null,sourceLock=''){
 const details=node('section',undefined,'kol-query-panel');
 const conditions=[kolFilters.start||kolFilters.end?`${kolFilters.start||'不限'} ～ ${kolFilters.end||'不限'}`:'不限日期',
  boot.accounts.find(a=>a.id===kolFilters.account_id)?.name||kolFilters.account_id||'全部來源',kolFilters.platform||'全部平台',
  kolFilters.include_internal==='true'?'包含內部訂單':'排除內部訂單'];
 if(data){conditions.push('狀態：'+(kolOrderFilters.status==='all'?'全部':kolOrderStatus(kolOrderFilters.status)));if(kolOrderFilters.utm)conditions.push('UTM：'+kolOrderFilters.utm);}
 details.append(node('h3','查詢條件'),node('p','目前結果：'+conditions.join(' · '),'kol-applied-filters'));
 kolFilterForm(details,refresh,kols,data,sourceLock);root.append(details);
}
if(typeof window!=='undefined')window.addEventListener('workspace-refreshed',()=>{
 const parts=location.hash.slice(1).split('?')[0].split('/');
 if(parts[0]==='kol'&&!['new','edit','import-new'].includes(parts[1])&&!pageDirty&&!(typeof kolImportBusy!=='undefined'&&kolImportBusy))return renderKol(parts);
});
function kolSummary(root,data,countLabel='有效訂單'){
 const summary=node('dl',undefined,'kol-summary');
 for(const [label,value] of [[countLabel,`${data.order_count} 單`],['訂單總金額',kolMoney(data.total)],['平均訂單總金額',kolMoney(data.average)]]){
  const cell=node('div');cell.append(node('dt',label),node('dd',value));summary.append(cell);
 }
 root.append(summary);
 if(data.missing_total_count)root.append(node('p',`${data.missing_total_count} 筆訂單未提供總金額；已知金額 ${kolMoney(data.known_total)}。完整總額與平均值暫不計算。`,'kol-warning'));
}
function kolInfo(root,k,showTitle=true){
 if(!k)return;
 if(kolSystemGroup(k)){const section=node('section',undefined,'kol-info');if(showTitle)section.append(node('h3',k.name));section.append(node('p',k.id==='general'?'沒有 UTM 的訂單固定歸入一般訂單。':'有 UTM 但未匹配 KOL，或早於合作開始的訂單，列為待確認歸屬。'));root.append(section);return;}
 const block=node('section',undefined,'kol-info');block.append(node('h3','KOL資訊'));
 block.append(kolTable(['項目','內容'],[['夥伴名稱',k.name],['平台',k.platforms.join('、')||'—'],['活動名稱',k.campaign_name||'—'],['活動開始時間',kolTime(k.started_at)],['UTM',k.utms.join('、')],['備註',k.comment||'—'],['建立時間',kolTime(k.created_at)],['更新時間',kolTime(k.updated_at)]]));root.append(block);
}
function kolSubnav(root,id,page='detail'){
 const nav=node('nav',undefined,'kol-tabs kol-subtabs');nav.setAttribute('aria-label','目前分類或 KOL 的資料');
 for(const [label,target] of [['訂單概況與明細','detail'],['商品銷售統計','products']]){
  const link=kolLink(label,`kol/${target}/${id}`);if(page===target||target==='products'&&page==='product-orders')link.setAttribute('aria-current','page');nav.append(link);
 }root.append(nav);
}
function kolExport(root,id,kind,extra={}){const a=node('a',kind==='statistics'?'下載統計 CSV':kind==='list'?'下載列表 CSV':kind==='products'?'下載產品 CSV':'下載訂單 CSV','button secondary');const q=kolQuery(id,extra);q.set('kind',kind);a.href='/api/kols/export?'+q;root.append(a);}
function kolPaged(root,headers,rows,key,sortControl=null){
 const state=kolPageState.get(key)||{page:1,limit:20};kolPageState.set(key,state);
 const container=node('div');root.append(container);
 const paint=()=>{container.replaceChildren();const pages=Math.max(1,Math.ceil(rows.length/state.limit));state.page=Math.min(state.page,pages);const offset=(state.page-1)*state.limit;
  const controls=node('div',undefined,'kol-pagination');controls.append(node('span',`本頁 ${rows.length?offset+1:0}–${Math.min(offset+state.limit,rows.length)} 筆，共 ${rows.length} 筆`));
  if(sortControl)container.append(sortControl);
  const limit=kolSelect(controls,'每頁筆數','limit',[['20','20'],['50','50'],['100','100'],['200','200']],String(state.limit));limit.onchange=()=>{state.limit=Number(limit.value);state.page=1;paint();};
  if(pages>1){const back=node('button','上一頁','secondary'),next=node('button','下一頁','secondary');back.type=next.type='button';back.disabled=state.page===1;next.disabled=state.page===pages;back.onclick=()=>{state.page--;paint();};next.onclick=()=>{state.page++;paint();};controls.append(back,node('span',`${state.page} / ${pages}`),next);}
  container.append(kolTable(headers,rows.slice(offset,offset+state.limit)),controls);
 };paint();
}
function kolCompare(a,b,sort){
 const field=sort.replace('-',''),direction=sort.startsWith('-')?-1:1;
 const numeric=['order_count','total','average'].includes(field);
 const x=a[field],y=b[field];
 if(x==null||y==null)return x==null&&y==null?a.id.localeCompare(b.id):x==null?1:-1;
 return (numeric?Number(x)-Number(y):String(x).localeCompare(String(y)))*direction||a.id.localeCompare(b.id);
}
function kolChartModel(data,grain='day',metric='orders'){
 const orders=(data.orders||[]).filter(o=>o.status==='active'),groups=new Map();
 const day=value=>new Date(new Date(value).getTime()+8*3600000).toISOString().slice(0,10);
 const dates=orders.map(o=>day(o.ordered_at)).sort(),start=data.filters?.start||dates[0],end=data.filters?.end||dates.at(-1);
 if(start&&end){
  const left=Date.parse(start+'T00:00:00Z'),right=Date.parse(end+'T00:00:00Z');
  // Bound visual aggregation; an unbounded historical range remains available in the table.
  if((right-left)/86400000>3660)throw new Error('圖表期間請縮小至十年內。');
  for(let at=left;at<=right;at+=86400000){const date=new Date(at).toISOString().slice(0,10);groups.set(grain==='month'?date.slice(0,7):date,{count:0,cents:0,missing:0});}
 }
 for(const o of orders){const date=day(o.ordered_at),key=grain==='month'?date.slice(0,7):date;const group=groups.get(key)||{count:0,cents:0,missing:0};group.count++;if(o.total==null)group.missing++;else group.cents+=Math.round(Number(o.total)*100);groups.set(key,group);}
 const ranked=(data.groups||[]).filter(g=>!g.system_group&&!['general','unattributed'].includes(g.id)).sort((a,b)=>kolCompare(a,b,metric==='orders'?'-order_count':'-'+metric)).slice(0,10);
 const buckets=[...groups].sort(([a],[b])=>a.localeCompare(b));
 const value=g=>metric==='orders'?g.count:g.missing?null:metric==='average'?(g.count?g.cents/100/g.count:null):g.cents/100;
 const updated=orders.map(o=>o.source_updated_at).filter(t=>t&&Number.isFinite(Date.parse(t))).sort((a,b)=>Date.parse(a)-Date.parse(b));
 return {trend:{labels:buckets.map(([key])=>key),values:buckets.map(([,g])=>value(g))},
  ranking:{labels:ranked.map(g=>g.name),values:ranked.map(g=>metric==='orders'?g.order_count:g[metric]==null?null:Number(g[metric]))},
  share:{labels:['已歸屬 KOL','一般訂單','待確認歸屬'],values:[orders.filter(o=>!['general','unattributed'].includes(o.kol_id)).length,orders.filter(o=>o.kol_id==='general').length,orders.filter(o=>o.kol_id==='unattributed').length]},
  quality:{orders:orders.length,missingTotal:orders.filter(o=>o.total==null).length,missingUpdated:orders.length-updated.length,latestUpdated:updated.at(-1)||null,batches:new Set(orders.map(o=>o.batch_id).filter(Boolean)).size}};
}
function kolSortedTable(root,headers,data,row,key){
 const section=node('div',undefined,'kol-sort-controls'),sort=kolSelect(section,'排序','sort',[['-started_at','開始時間：新到舊'],['started_at','開始時間：舊到新'],['-order_count','訂單數：多到少'],['order_count','訂單數：少到多']],kolPageState.get(key+'-sort')||'-started_at');
 const target=node('div');root.append(target);
 if(key==='statistics')for(const [value,title] of [['-total','訂單金額：高到低'],['total','訂單金額：低到高'],['-average','平均訂單金額：高到低'],['average','平均訂單金額：低到高']])sort.append(new Option(title,value));
 sort.value=kolPageState.get(key+'-sort')||'-started_at';
 const paint=()=>{kolPageState.set(key+'-sort',sort.value);const sorted=[...data].sort((a,b)=>kolCompare(a,b,sort.value));target.replaceChildren();kolPaged(target,headers,sorted.map(row),key,section);};sort.onchange=()=>{kolPageState.delete(key);paint();};paint();
}
let disposeKolCharts=()=>{};
if(typeof window!=='undefined')window.addEventListener('hashchange',()=>{disposeKolCharts();});
function renderKolCharts(root,data){
 const section=node('section',undefined,'analytics-section'),quality=node('section',undefined,'analytics-quality');
 section.append(node('h3','KOL 表現圖表'));
 const controls=node('div',undefined,'analytics-controls');
 const grain=kolSelect(controls,'趨勢彙總','chart-grain',[['day','每日'],['month','每月']],'day');
 const metric=kolSelect(controls,'圖表指標','chart-metric',[['orders','有效訂單數'],['total','訂單總金額'],['average','平均訂單金額']],'orders');
 const panels=node('div',undefined,'kol-chart-grid');section.append(controls,panels);root.append(quality,section);
 let cleanups=[];
 const clear=()=>{for(const dispose of cleanups)dispose();cleanups=[];};disposeKolCharts=clear;
 const paint=()=>{
  clear();panels.replaceChildren();quality.replaceChildren();
  let model;try{model=kolChartModel(data,grain.value,metric.value);}catch(error){panels.append(node('p',error.message,'notice error'));return;}
  const q=model.quality;
  quality.append(node('h3','資料完整度與範圍'),node('p',`有效訂單 ${q.orders} 單；缺訂單總金額 ${q.missingTotal} 單；待確認歸屬 ${model.share.values[2]} 單；缺有效來源更新時間 ${q.missingUpdated} 單。`),node('p',`本期有效訂單來自 ${q.batches} 個批次。報表產生時間：${kolTime(data.generated_at)}。`),node('p',q.latestUpdated?`已載入有效訂單中，最新來源更新時間：${kolTime(q.latestUpdated)}；不代表全部來源已同步至此刻。`:'目前沒有可用的來源更新時間。'),node('p','本摘要沿用本頁日期、來源與平台條件，只檢查已載入有效訂單；來源檔案是否完整涵蓋期間尚未確認。缺金額的日期／月份不繪金額與平均值，未歸因不等於零業績。','statistics-note'));
  const unit=metric.value==='orders'?'單':'TWD';
  for(const [key,title] of [['trend','期間趨勢'],['ranking','KOL 前十名'],['share','訂單歸屬分布']]){
   const values=model[key],share=key==='share',trend=key==='trend',figure=node('figure',undefined,'analytics-chart'),canvas=node('div',undefined,'analytics-canvas');
   const caption=node('figcaption',title),note=node('p',share?'以有效訂單數為分母；一般訂單與待確認歸屬分開呈現。':trend?'依台北下單日期彙總，空日期表示目前未載入有效訂單。':'僅比較 KOL；缺金額者排在已知值之後，不當作 0。','statistics-note');
   const details=node('details',undefined,'chart-values');details.append(node('summary','查看圖表數字'));
   details.append(kolTable(share?['分類','有效訂單','占比']:['項目',unit],values.labels.map((label,i)=>share?[label,values.values[i],q.orders?(values.values[i]/q.orders*100).toFixed(1)+'%':'無有效訂單']:[label,values.values[i]==null?'資料未齊／無訂單':unit==='TWD'?kolMoney(values.values[i].toFixed(2)):values.values[i]])));
   canvas.setAttribute('role','img');canvas.setAttribute('aria-label',title+'；精確數字可展開下方表格');figure.append(caption,note,canvas,details);panels.append(figure);
   if(!globalThis.echarts){canvas.hidden=true;details.open=true;note.textContent+=' 圖表套件未載入，以下保留數字。';continue;}
   if(!values.labels.length){canvas.hidden=true;details.open=true;continue;}
   let chart,observer;
   const draw=()=>{
    if(!canvas.isConnected||!canvas.clientWidth)return;
    const css=getComputedStyle(document.documentElement),ink=css.getPropertyValue('--ink').trim(),accent=css.getPropertyValue('--accent').trim();
    if(!chart)chart=echarts.init(canvas,null,{renderer:'svg'});
    chart.resize();chart.setOption({animation:false,aria:{enabled:true},color:[accent],textStyle:{fontFamily:getComputedStyle(figure).fontFamily,fontSize:14,color:ink},tooltip:{trigger:'axis',confine:true,renderMode:'richText'},grid:{left:12,right:24,top:36,bottom:trend?64:20,containLabel:true},
     xAxis:trend?{type:'category',data:values.labels,axisLabel:{hideOverlap:true}}:{type:'value',min:0,name:share?'單':unit},
     yAxis:trend?{type:'value',min:0,name:unit}:{type:'category',inverse:true,data:values.labels,axisLabel:{width:canvas.clientWidth<600?90:160,overflow:'truncate'}},
     dataZoom:trend&&values.labels.length>31?[{type:'slider',height:22,bottom:4},{type:'inside'}]:[],
     series:[{type:trend?'line':'bar',name:share?'有效訂單':unit,data:values.values,connectNulls:false,showSymbol:values.labels.length<32,barMaxWidth:28}]},true);
   };
   if(typeof ResizeObserver!=='undefined'){observer=new ResizeObserver(draw);observer.observe(canvas);}window.addEventListener('resize',draw);draw();
   cleanups.push(()=>{observer?.disconnect();chart?.dispose();window.removeEventListener('resize',draw);});
  }
 };
 grain.onchange=metric.onchange=paint;paint();
}
async function renderKol(parts){
 disposeKolCharts();
 const [pathname,query]=parts.join('/').split('?');parts=pathname.split('/');
 if(query){const values=new URLSearchParams(query);for(const key of Object.keys(kolFilters))if(values.has(key))kolFilters[key]=values.get(key);for(const key of Object.keys(kolOrderFilters))if(values.has(key))kolOrderFilters[key]=values.get(key);}
 const sequence=++kolRenderSequence,initialHash=location.hash,root=$('kol');
 const detailPage=['detail','products','product-orders','order','edit','delete'].includes(parts[1]);
 root.replaceChildren();if(!detailPage){root.append(node('h2','KOL 分析'));kolNavigation(root,parts[1]||'list');}
 const loading=node('p','讀取KOL資料中…');root.append(loading);
 try{
  const {kols}=await request('/api/kols');if(sequence!==kolRenderSequence||location.hash!==initialHash)return;
  loading.remove();const page=parts[1]||'list';
  if(['edit','delete'].includes(page)&&kolSystemId(decodeURIComponent(parts[2]||''))){root.append(kolAction('返回分類檢視','kol/detail/'+decodeURIComponent(parts[2])));throw new Error('系統分類只有檢視，不能修改或刪除。');}
  if(['imports','import-new','import'].includes(page))return await renderKolImport(root,parts,kols,()=>sequence===kolRenderSequence&&location.hash===initialHash); 
  if(page==='list'){
   const data=await request('/api/kols/report?'+kolQuery());if(sequence!==kolRenderSequence||location.hash!==initialHash)return;
   kolListFilters(root,()=>kolRerender(parts),kols);
   const heading=node('div',undefined,'section-heading'),tools=node('div',undefined,'kol-list-actions');heading.append(node('h3','合作夥伴'));tools.append(kolAction('新增 KOL','kol/new'));kolExport(tools,'','list');heading.append(tools);root.append(heading);
   kolGroupTable(root,['操作','夥伴名稱','活動名稱','有效訂單數','平台','開始時間','備註','建立時間','更新時間'],data.groups,k=>{
    const actions=node('div',undefined,'kol-row-actions');actions.append(kolAction('檢視',`kol/detail/${k.id}`));if(!kolSystemGroup(k))actions.append(kolAction('修改',`kol/edit/${k.id}`),kolAction('刪除',`kol/delete/${k.id}`));
    return [actions,kolLink(k.name,`kol/detail/${k.id}`),k.campaign_name||'—',k.order_count,k.platforms.join('、')||'—',kolTime(k.started_at),k.comment||'—',kolTime(k.created_at),kolTime(k.updated_at)];
   },'list');
   if(!kols.length)root.append(kolAction('建立第一位KOL','kol/new'));
   return;
  }
  if(page==='new'||page==='edit'){
   const editorQuery=new URLSearchParams(query||''),returnBatch=editorQuery.get('return_batch'),appendUtm=editorQuery.get('append_utm');
   const k=page==='edit'?kols.find(k=>k.id===decodeURIComponent(parts[2])):null;if(page==='edit'&&!k)throw new Error('找不到此KOL，請返回列表。');
   if(k)kolBreadcrumb(root,k.name,k.id);
   root.append(node('h3',k?'編輯KOL':'新增KOL'));
   const wrapper=node('div',undefined,'record-detail-page'),form=node('form',undefined,'kol-editor');wrapper.append(form);root.append(wrapper);
   kolInput(form,'KOL名稱','name',k?.name||'','text',true);
   kolInput(form,'活動名稱','campaign_name',k?.campaign_name||'');
   kolInput(form,'平台分類（逗號分隔）','platforms',k?.platforms.join(', ')||'');
   const taipei=k?.started_at?new Date(new Date(k.started_at).getTime()+8*3600000).toISOString().slice(0,23):'';
   kolInput(form,'合作開始時間（台北）','started_at',taipei,'datetime-local',true).step='0.001';
   kolInput(form,'UTM medium（多個以逗號分隔）','utms',[...(k?.utms||[]),...(appendUtm&&!(k?.utms||[]).includes(appendUtm)?[appendUtm]:[])].join(', '),'text',true);
   const checkLabel=node('label','保留代碼大小寫'),check=node('input');check.type='checkbox';check.name='keep_case';check.checked=k?.keep_case||false;checkLabel.prepend(check);form.append(checkLabel);
   form.append(node('p','未勾選時，保存的代碼轉為小寫；訂單 UTM 依原始大小寫精確比對。合作開始前的訂單不歸入此KOL。','muted'));
   const commentLabel=node('label','備註'),comment=node('textarea');comment.name='comment';comment.maxLength=2000;comment.rows=4;comment.value=k?.comment||'';commentLabel.append(comment);form.append(commentLabel);
   if(k)form.append(kolTable(['項目','內容'],[['建立時間',kolTime(k.created_at)],['更新時間',kolTime(k.updated_at)]]));
   form.append(node('p','保存後，統計會依最新設定重新計算；原始訂單保留。','muted'));
   const actions=node('div',undefined,'form-actions'),save=node('button','保存KOL');save.type='submit';actions.append(save,kolAction(returnBatch?'返回匯入批次':'返回列表',returnBatch?kolImportRoute(returnBatch):'kol'));form.append(actions);
   const notice=node('p');notice.setAttribute('role','status');form.append(notice);
   const operationId=crypto.randomUUID();
   form.onsubmit=async e=>{e.preventDefault();if(save.disabled)return;save.disabled=true;try{
    const values=Object.fromEntries(new FormData(form));values.keep_case=check.checked;values.started_at=new Date(values.started_at+'+08:00').toISOString();
    await request('/api/kols',{...values,operation_id:operationId,...(k?{id:k.id,expected_version:k.version}:{})});if(sequence!==kolRenderSequence||location.hash!==initialHash)return;dirtyForms.delete(form);pageDirty=dirtyForms.size>0;navigatePage(returnBatch?kolImportRoute(returnBatch):'kol');
   }catch(error){if(sequence===kolRenderSequence)notice.textContent=error.message;}finally{save.disabled=false;}};
   if(k){const del=node('button','刪除此KOL','danger');del.type='button';actions.append(del);del.onclick=async()=>{
    if(!confirm('刪除KOL設定後，原先匹配的訂單將重新列為待確認歸屬。原始訂單仍保留。確定刪除？'))return;
    del.disabled=true;try{await request('/api/kols/delete',{operation_id:crypto.randomUUID(),id:k.id,expected_version:k.version});dirtyForms.delete(form);pageDirty=dirtyForms.size>0;navigatePage('kol');}catch(error){notice.textContent=error.message;}finally{del.disabled=false;}
   };}
   return;
  }
  const id=page==='statistics'?'':parts[2];
  if(!['statistics','detail','order','products','product-orders','delete'].includes(page)||(!id&&page!=='statistics'))throw new Error('找不到KOL頁面。');
  const productKey=page==='product-orders'?parts[3]:page==='order'?parts[4]:null;
  const productPair=productKey?JSON.parse(decodeURIComponent(productKey)):null;
  const extra=page==='statistics'?{}:{...kolOrderFilters,...(productPair?{account_id:productPair[0],product_id:productPair[1]}:{})};
  const data=await request('/api/kols/report?'+kolQuery(id,extra));if(sequence!==kolRenderSequence||location.hash!==initialHash)return;
  const refresh=()=>kolRerender(parts);
  if(page==='delete'){
   if(!data.kol)throw new Error('找不到此KOL');if(kolSystemGroup(data.kol))throw new Error('系統分類只有檢視，不能修改或刪除。');kolBreadcrumb(root,data.kol.name,id);kolInfo(root,data.kol);
   root.append(node('p','刪除這筆KOL設定後，原先匹配的訂單會重新列為待確認歸屬；原始訂單保留。'));
   const del=node('button','確認刪除KOL','danger'),actions=node('div',undefined,'form-actions');actions.append(del,kolAction('取消，返回檢視',`kol/detail/${id}`));root.append(actions);
   del.onclick=async()=>{if(!confirm('確定刪除此KOL設定？'))return;del.disabled=true;try{await request('/api/kols/delete',{operation_id:crypto.randomUUID(),id,expected_version:data.kol.version});navigatePage('kol');}catch(error){root.append(node('p',error.message,'notice error'));}finally{del.disabled=false;}};return;
  }
  if(page==='order'){
   const key=JSON.parse(decodeURIComponent(parts[3]||'')),order=data.orders.find(o=>o.account_id===key[0]&&o.order_id===key[1]);
   root.append(kolAction('返回相關訂單',productKey?`kol/product-orders/${id}/${productKey}`:`kol/detail/${id}`));
   if(!order)throw new Error('這筆訂單已不在目前條件內，請返回統計重新查詢。');
   root.append(node('h3',`訂單 ${order.order_id}`));
   root.append(kolTable(['項目','內容'],[['訂單狀態',statusBadge(kolOrderStatus(order.status))],['訂單來源',boot.accounts.find(a=>a.id===order.account_id)?.name||order.account_id],['下單時間',new Date(order.ordered_at).toLocaleString('zh-TW',{timeZone:'Asia/Taipei'})],['訂單總金額',kolMoney(order.total)],['歸因結果',order.kol_name],['UTM medium',order.utm_medium||'—'],['歸因依據',order.reason],['設定版本',order.rule_version??'—'],['訂單版本',order.revision],['來源批次',kolBatchCell(order)]]));
   root.append(node('h3','商品明細'),node('p','訂單總金額包含運費／折扣，每張訂單只計一次；顯示「以完整訂單計」的商品僅統計數量，不另計商品金額。','muted'));
   root.append(kolTable(['商品／明細','類型','SKU','數量','明細金額','原始貨號','來源列'],order.lines.map(l=>[l.name,l.line_type==='non_stock'?'非庫存':'商品',l.sku||'—',l.quantity,kolLineMoney(l.amount),l.original_sku,l.row_index])));return;
  }
  if(page==='statistics')root.append(node('h3','表現統計'));
  if(page!=='statistics'){
   const name=data.kol?.name||'待確認歸屬';kolBreadcrumb(root,name);
   const heading=node('div',undefined,'section-heading');heading.append(node('h2',name));if(!kolSystemId(id)){const actions=node('div',undefined,'kol-list-actions');actions.append(kolAction('修改','kol/edit/'+id),kolAction('刪除','kol/delete/'+id));heading.append(actions);}root.append(heading);
   if(kolSystemId(id))kolInfo(root,data.kol,false);
   kolSubnav(root,id,page);
   const top=node('div',undefined,'kol-detail-top');
   if(!kolSystemId(id))kolInfo(top,data.kol,false);
   if(page==='detail'){
    const statistics=node('section',undefined,'kol-detail-statistics');statistics.append(node('h3','期間有效業績'),node('p','依日期、來源與分類統計有效訂單；下方訂單狀態、UTM 與商品篩選的合計另列於訂單列表。','muted'));
    statistics.append(kolTable(['項目','內容'],[['開始日期',kolFilters.start||'不限'],['結束日期',kolFilters.end||'不限'],['有效訂單',data.summary.order_count],['訂單總金額',kolMoney(data.summary.total)],['平均訂單金額',kolMoney(data.summary.average)],['統計訂單狀態','有效']]));
    if(data.summary.missing_total_count)statistics.append(node('p',`${data.summary.missing_total_count} 筆訂單未提供總金額；完整總額與平均值暫不計算。`,'kol-warning'));
    top.append(statistics);
   }
   root.append(top);
   if(productPair&&data.product){const info=node('section',undefined,'kol-info');info.append(node('h3','產品資訊'),kolTable(['項目','內容'],[['來源',boot.accounts.find(a=>a.id===productPair[0])?.name||productPair[0]],['類型',data.product.kind==='bundle'?'套組':'單品'],['SKU',data.product.sku],['產品名稱',data.product.name],['產品識別',data.product.id]]));root.append(info);}

  }
  if(page==='statistics')kolListFilters(root,refresh,kols);
  else kolListFilters(root,refresh,kols,['detail','product-orders'].includes(page)?data:null,productPair?.[0]||'');
  root.append(node('p',`${data.definition} 報表產生時間：${kolTime(data.generated_at)}`,'muted'));
  if(data.duplicate_source_count)root.append(node('p',`已排除重複來源訂單 ${data.duplicate_source_count} 筆。`,'muted'));
  if(page==='statistics'){
   kolSummary(root,data.summary);root.append(node('p','總計包含 KOL、一般訂單與待確認歸屬；非 KOL 分類列在名單下方。','statistics-note'));
   renderKolCharts(root,data);
   const heading=node('div',undefined,'section-heading');heading.append(node('h3','夥伴表現'));kolExport(heading,'','statistics');root.append(heading);
   kolGroupTable(root,['操作','夥伴名稱','訂單總數','訂單總金額','平均訂單金額','缺總額筆數'],data.groups,g=>{const controls=node('div',undefined,'kol-row-actions');controls.append(kolAction('檢視',`kol/detail/${g.id}`));return [controls,g.name,g.order_count,kolMoney(g.total),kolMoney(g.average),g.missing_total_count];},'statistics');return;
  }
  if(page==='products'){
   const heading=node('div',undefined,'section-heading');heading.append(node('h3','產品統計'));kolExport(heading,id,'products');root.append(heading);
   kolPaged(root,['操作','通路／類型','SKU','產品名稱／識別','數量','有效訂單數','商品明細金額'],data.products.map(p=>[
    kolAction('訂單列表',`kol/product-orders/${id}/${encodeURIComponent(JSON.stringify([p.account_id,p.id]))}`),`${boot.accounts.find(a=>a.id===p.account_id)?.name||p.account_id} / ${p.kind==='bundle'?'套組':'單品'}`,p.sku,`${p.name} / ${p.id}`,p.quantity,p.order_count,kolLineMoney(p.amount)
   ]),'products/'+id);return;
  }
  if(productKey)root.append(kolAction('返回產品統計',`kol/products/${id}`));
  const orderHeading=node('div',undefined,'section-heading');orderHeading.append(node('h3',page==='product-orders'?'該產品相關訂單':'相關訂單'));kolExport(orderHeading,id,'orders',extra);root.append(orderHeading);
  if(data.filtered_summary){
   const filtered=node('section',undefined,'kol-filtered-summary');filtered.append(node('h3','目前篩選結果'));
   kolSummary(filtered,data.filtered_summary,'符合條件訂單');
   filtered.append(node('p','合計對應下方全部篩選結果，不受分頁影響；包含所選狀態的訂單金額，不代表有效業績。商品篩選仍按每張完整訂單金額計一次。','muted'));root.append(filtered);
  }else root.append(node('p','目前服務尚未提供篩選合計，請更新後端後再查詢。','kol-warning'));
  const ordered=[...data.orders].sort((a,b)=>b.ordered_at.localeCompare(a.ordered_at)||a.order_id.localeCompare(b.order_id));
  kolPaged(root,['訂單狀態','UTM標籤','通路','訂單編號','訂單總金額',...(productKey?['該產品數量']:[]),'下單時間','來源更新時間','來源批次'],ordered.map(o=>[
   statusBadge(kolOrderStatus(o.status)),o.utm_medium||'—',boot.accounts.find(a=>a.id===o.account_id)?.name||o.account_id,
   kolLink(o.order_id,`kol/order/${id}/${encodeURIComponent(JSON.stringify([o.account_id,o.order_id]))}${productKey?'/'+productKey:''}`),kolMoney(o.total),...(productKey?[o.product_quantity]:[]),kolTime(o.ordered_at),kolTime(o.source_updated_at),kolBatchCell(o)
  ]),page+'/'+id+'/'+(productKey||''));
 }catch(error){if(sequence===kolRenderSequence&&location.hash===initialHash){loading.remove();const message=node('p',error.message,'notice error');message.setAttribute('role','alert');const retry=node('button','重試','secondary');retry.onclick=()=>renderKol(parts);root.append(message,retry);}}
}

if(typeof module!=='undefined')module.exports={kolChartModel,kolCompare};
