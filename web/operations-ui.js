'use strict';

// Presentation scope only: business state and existing page routes remain owned by workspace.js.
function operationsView(area){
 const views={orders:['接收訂單','orders'],import:['匯入銷售訂單','import'],history:['匯入紀錄','import'],sources:['訂單來源管理','orders'],'goods-purchases':['商品採購／叫貨','goods-purchases'],dashboard:['銷售總覽','dashboard'],sales:['銷售查詢','sales'],locations:['櫃位管理','locations'],transfers:['調撥管理','transfers'],putaway:['配櫃','stock'],catalogue:['SKU 商品庫','catalogue'],stock:['入庫管理','stock'],shipping:['出貨管理','shipping'],
  'shipping-statistics':['出貨總覽','shipping-statistics'],'product-analytics':['商品數據分析','catalogue'],kol:['KOL 分析','kol'],materials:['物料與採購','materials'],
  inventory:['庫存總覽','catalogue'],quality:['庫存詳細','catalogue'],'stock-reports':['庫存與出貨報表','catalogue'],assistant:['聊天室','assistant'],'ai-settings':['AI 設定','ai-settings'],'ai-evaluation':['AI 評測','ai-evaluation']};
 return views[area]?{title:views[area][0],parent:views[area][1]}:null;
}
function applyOperationsView(area){
 const view=operationsView(area);
 document.body.classList.toggle('operations-ui',!!view);
 document.body.classList.remove('operations-menu-open');
 const toggle=document.getElementById('operations-menu-toggle');
 toggle.setAttribute('aria-expanded','false');
 const heading=document.querySelector('.page-heading h1');
 heading.textContent=view?`營運管理 / ${view.title}`:'營運工作台';
 const section=document.getElementById(area);
 if(view&&section)section.classList.add('operations-surface');
}
if(typeof document!=='undefined'){
 const toggle=document.getElementById('operations-menu-toggle');
 toggle.addEventListener('click',()=>{
  const open=document.body.classList.toggle('operations-menu-open');
  toggle.setAttribute('aria-expanded',String(open));
  if(open)document.querySelector('#workspace-navigation a[aria-current="page"]')?.focus();
 });
 document.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&document.body.classList.contains('operations-menu-open')){
   document.body.classList.remove('operations-menu-open');toggle.setAttribute('aria-expanded','false');toggle.focus();
  }
 });
}
if(typeof module!=='undefined')module.exports={operationsView};
