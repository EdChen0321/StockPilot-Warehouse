'use strict';
let overviewMode=false,overviewSequence=0;
function showOverviewMode(){
  $('product-form').hidden=overviewMode;$('product-history').hidden=overviewMode;
  $('bundle-editor').hidden=overviewMode||!editingProduct||editingProduct.kind!=='bundle';
  $('product-overview').hidden=!overviewMode;
  $('product-overview-toggle').textContent=overviewMode?'編輯商品':'查看營運資料';
}
async function loadProductOverview(){
  if(!editingProduct)return;const id=editingProduct.id,sequence=++overviewSequence;
  const target=$('product-overview');target.replaceChildren(node('p','讀取營運資料…'));
  const data=await request(`/api/products/${encodeURIComponent(id)}/overview`);
  if(sequence!==overviewSequence||editingProduct?.id!==id)return;
  target.replaceChildren();
  const metrics=node('div',undefined,'overview-metrics');
  for(const [label,value] of [['銷售額',formatAmount(data.sales.amount)],['有效訂單',data.sales.order_count],['可用庫存',data.stock?data.stock.available:'依組成商品']]){const cell=node('div');cell.append(node('span',label),node('strong',value));metrics.append(cell);}target.append(metrics);
  if(data.stock)target.append(materialTable(['在庫','已占用','可用'],[[String(data.stock.on_hand),String(data.stock.allocated),String(data.stock.available)]]));
  target.append(node('h3','生產 BOM'));
  if(data.bom)target.append(materialLineTable(data.bom.lines,[['quantity','每件用量']]));else target.append(node('p','尚未建立生產 BOM。'));
  if(data.materials.length){target.append(node('h3','相關物料'),materialTable(['物料','計畫需求','可用','缺口','在途','單位'],data.materials.map(m=>[m.name,m.demand,m.available,m.shortage,m.incoming,m.unit])));}
  const sources=node('section',undefined,'content-section');sources.append(node('h3','期間與資料來源'),node('p','銷售：全部期間、全部來源；庫存為目前狀態。物料需求只計此商品有效生產計畫，物料可用量已扣全工作區預留。'));
  for(const [key,info] of Object.entries(data.sources))sources.append(node('p',`${{sales:'銷售',stock:'庫存',materials:'物料'}[key]} · ${dateTime(info.at)} · ${info.rule}`));
  sources.append(node('p',`查詢 ${data.query_id}`));target.append(sources);
  const assistant=node('section',undefined,'content-section');assistant.append(node('h3','AI 唯讀工具預覽（未接模型）'));
  const output=node('div'),previewButton=node('button','預覽此商品查詢');previewButton.type='button';
  previewButton.addEventListener('click',()=>stockAction('catalogue',previewButton,async()=>{
    const result=await request('/api/assistant/preview',{calls:[{tool:'product_overview',arguments:{product_id:id}}]});
    const view=result.results[0].data;output.replaceChildren(node('p',result.notice),node('p',`${view.product.name}：銷售 ${formatAmount(view.sales.amount)}／${view.sales.order_count} 單，可用庫存 ${view.stock?view.stock.available:'依組成商品'}。`),node('p',`來源查詢 ${view.query_id}；${dateTime(view.sources.sales.at)}`));
  }));assistant.append(previewButton,output);target.append(assistant);
}
$('product-overview-toggle').addEventListener('click',()=>stockAction('catalogue',$('product-overview-toggle'),async()=>{overviewMode=!overviewMode;showOverviewMode();if(overviewMode)await loadProductOverview();}));
window.addEventListener('product-selected',event=>{overviewMode=false;showOverviewMode();$('product-overview-toggle').hidden=true;});

const salesAnalysis=node('details',undefined,'sales-disclosure');salesAnalysis.append(node('summary','進階分析：趨勢、通路、商品與歸因'));
const salesAnalysisForm=node('div',undefined,'search-bar');
const salesDimension=materialField(salesAnalysisForm,'分析維度','daily',[{value:'daily',label:'每日趨勢'},{value:'channels',label:'來源通路'},{value:'products',label:'商品'},{value:'attribution',label:'KOL 來源歸因'}]);
const insightsExport=node('a','下載分析 CSV','button secondary');salesAnalysisForm.append(insightsExport);
const salesAnalysisResult=node('div');salesAnalysis.append(salesAnalysisForm,salesAnalysisResult);$('sales-rows').closest('.table-scroll').after(salesAnalysis);
let insightSequence=0;
async function refreshInsights(){
  const sequence=++insightSequence;const params=filters();
  const data=await request('/api/insights?'+params);if(sequence!==insightSequence)return;
  insightsExport.href='/api/insights/export?'+params;
  const dimension=salesDimension.value;const rows=data[dimension]||[];
  salesAnalysisResult.replaceChildren(materialTable(['項目','商品金額','訂單數','商品數量'],rows.map(r=>[dimension==='channels'?boot.accounts.find(a=>a.id===r.key)?.name||r.label:r.label,formatAmount(r.amount),String(r.order_count),String(r.quantity)])));
  if(!rows.length)salesAnalysisResult.append(node('p','此條件沒有已確認銷售。'));
  if(dimension==='attribution')salesAnalysisResult.append(node('p',data.attribution_note));
}
salesDimension.addEventListener('change',()=>refreshInsights().catch(e=>announce(e.message,true)));

window.addEventListener('sales-refreshed',()=>refreshInsights().catch(e=>announce(e.message,true)));

const crossbuyPanel=node('section',undefined,'content-section');crossbuyPanel.append(node('h3','商品跨購分析'));
const crossbuyForm=node('form',undefined,'editor-grid');const crossbuyAccount=materialField(crossbuyForm,'跨購來源','',[]);crossbuyAccount.required=false;
const crossbuyStart=materialField(crossbuyForm,'觀察開始日期','',null,'date'),crossbuyEnd=materialField(crossbuyForm,'觀察結束日期','',null,'date');crossbuyStart.required=crossbuyEnd.required=false;
const crossbuySubmit=node('button','查詢跨購');crossbuySubmit.type='submit';crossbuyForm.append(crossbuySubmit);
const crossbuyResult=node('div');crossbuyPanel.append(crossbuyForm,crossbuyResult);$('catalogue').append(crossbuyPanel);
function crossbuyOptions(){const previous=crossbuyAccount.value;crossbuyAccount.replaceChildren(new Option('全部來源（各帳戶獨立認人）',''));for(const a of boot.accounts)crossbuyAccount.append(new Option(a.name,a.id));crossbuyAccount.value=previous;}
window.addEventListener('workspace-refreshed',crossbuyOptions);crossbuyOptions();
crossbuyForm.addEventListener('submit',e=>{e.preventDefault();action(crossbuySubmit,async()=>{
  const params=new URLSearchParams({account_id:crossbuyAccount.value,start:crossbuyStart.value,end:crossbuyEnd.value});const data=await request('/api/crossbuy?'+params);
  crossbuyResult.replaceChildren(node('p',`可識別顧客的訂單 ${data.known_customer_orders}／${data.eligible_orders}；缺顧客識別 ${data.missing_customer_orders} 單。`),materialTable(['來源商品 → 目標商品','來源買家','同單買家','同單比例','後購買家','後購比例'],data.pairs.map(p=>[`${p.source_name} → ${p.target_name}`,String(p.eligible_buyers),String(p.same_order_buyers),p.same_order_rate+'%',String(p.later_buyers),p.later_rate+'%'])));
  if(!data.pairs.length)crossbuyResult.append(node('p','目前沒有符合口徑的跨購紀錄；不把缺資料當成零購買傾向。'));
  const detail=node('section',undefined,'content-section');detail.append(node('h3','分析口徑與觀察限制'),node('p',data.definition),node('p',`排除純套組訂單 ${data.excluded_bundle_only_orders}、純非庫存訂單 ${data.excluded_non_stock_only_orders??0}；${data.rule_version} · ${dateTime(data.generated_at)}。後購為此觀察區間內的提案口徑，非固定回購窗口。`));crossbuyResult.append(detail);
});});
