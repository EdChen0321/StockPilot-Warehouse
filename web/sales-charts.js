'use strict';

function chartCents(value){
  if(!/^-?\d+\.\d{2}$/.test(String(value)))throw new Error('金額格式無法核對。');
  const result=Number(String(value).replace('.',''));
  if(!Number.isSafeInteger(result))throw new Error('金額超出圖表可安全核對範圍。');
  return result;
}
function chartDay(value){
  const instant=new Date(value);if(!Number.isFinite(instant.getTime()))throw new Error('下單日期無法核對。');
  return new Date(instant.getTime()+8*3600000).toISOString().slice(0,10);
}
function salesChartModel(data,products=[],trendMetric='amount',rankingMetric='quantity'){
  const daily=new Map(),dailyQuantity=new Map(),dailyOrders=new Map(),goods=new Map(),orders=new Set(),identities=new Set();
  const skus=new Map(products.map(p=>[p.id,p.sku]));let amountCents=0,quantity=0,excludedQuantity=0;
  for(const line of data.lines){
    const identity=JSON.stringify([line.account_id,line.order_id,line.line_id]);
    if(identities.has(identity))throw new Error('銷售明細重複，停止繪圖以避免重複加總。');identities.add(identity);
    if(!Number.isSafeInteger(line.quantity)||line.quantity<0)throw new Error('銷量無法核對。');
    const cents=chartCents(line.amount),day=chartDay(line.ordered_at);
    amountCents+=cents;quantity+=line.quantity;orders.add(JSON.stringify([line.account_id,line.order_id]));
    daily.set(day,(daily.get(day)||0)+cents);
    dailyQuantity.set(day,(dailyQuantity.get(day)||0)+line.quantity);
    if(!dailyOrders.has(day))dailyOrders.set(day,new Set());
    dailyOrders.get(day).add(JSON.stringify([line.account_id,line.order_id]));
    if(line.line_type==='non_stock'){excludedQuantity+=line.quantity;continue;}
    const row=goods.get(line.product_id)||{id:line.product_id,name:line.product_name,quantity:0,amount:0};row.quantity+=line.quantity;row.amount+=cents;goods.set(line.product_id,row);
  }
  if(!Number.isSafeInteger(amountCents)||amountCents!==chartCents(data.amount)||quantity!==data.sales_quantity||orders.size!==data.order_count)throw new Error('圖表與銷售明細總計核對不符，請重新整理。');
  const dates=[...daily.keys()].sort(),labels=[],values=[];
  if(dates.length){
    const first=data.filters.start||dates[0],last=data.filters.end||dates.at(-1);
    const left=Date.parse(first+'T00:00:00Z'),right=Date.parse(last+'T00:00:00Z');
    if(!Number.isFinite(left)||!Number.isFinite(right)||right<left||(right-left)/86400000>3660)throw new Error('圖表日期範圍過大或不正確，請縮小至十年內。');
    for(let at=left;at<=right;at+=86400000){const day=new Date(at).toISOString().slice(0,10);labels.push(day);values.push(trendMetric==='orders'?(dailyOrders.get(day)?.size||0):trendMetric==='quantity'?(dailyQuantity.get(day)||0):(daily.get(day)||0)/100);}
  }
  const ranked=[...goods.values()].sort((a,b)=>b[rankingMetric]-a[rankingMetric]||String(a.id).localeCompare(String(b.id))).slice(0,10);
  return {amountCents,quantity,orderCount:orders.size,excludedQuantity,
    trend:{labels,values,unit:trendMetric==='orders'?'有效訂單數':trendMetric==='quantity'?'銷售品項數量':'TWD'},ranking:{labels:ranked.map(p=>`${p.name} · ${skus.get(p.id)||p.id}`),values:ranked.map(p=>rankingMetric==='amount'?p.amount/100:p.quantity),ids:ranked.map(p=>p.id),unit:rankingMetric==='amount'?'TWD':'銷售品項數量'}};
}
function inventoryChartModel(data,productId=''){
  const labels=['一般品可出貨','盒損可出貨','已占用','退貨限制','調撥中','歷史待寄','其他不可出貨'],values=labels.map(()=>0);
  const products=data.products.filter(p=>!productId||p.id===productId),ids=new Set(products.map(p=>p.id));
  for(const u of data.units){
    if(!ids.has(u.product_id)||!['warehouse','staging'].includes(u.location))continue;
    const index=u.location==='staging'?5:u.transfer_id?4:u.return_hold?3:u.shipment_id?2:
      u.quality==='good'&&u.place_eligible&&u.packaging==='good'?0:
      u.quality==='good'&&u.place_eligible&&u.packaging==='damaged'&&data.storage_enabled!==false?1:6;
    values[index]++;
  }
  const total=values.reduce((a,b)=>a+b,0),expected=products.reduce((n,p)=>n+p.on_hand,0);
  if(total!==expected)throw new Error('庫存圖與逐件在庫數核對不符，請重新整理。');
  const settings=new Map(data.alert_settings.map(s=>[s.product_id,s.stock_min]));
  const shortages=products.filter(p=>settings.has(p.id)&&p.available<settings.get(p.id))
    .map(p=>({...p,shortfall:settings.get(p.id)-p.available}))
    .sort((a,b)=>b.shortfall-a.shortfall||String(a.id).localeCompare(String(b.id))).slice(0,10);
  return {labels,values,total,productCount:products.length,
    shortage:{labels:shortages.map(p=>`${p.name} · ${p.sku||p.id}`),values:shortages.map(p=>p.shortfall),available:shortages.map(p=>p.available),ids:shortages.map(p=>p.id),unit:'件'},
    missingThresholds:products.filter(p=>!settings.has(p.id)).length,
    lowStock:products.filter(p=>settings.has(p.id)&&p.available<settings.get(p.id)).length};
}
function chartOption(kind,data,palette,width){
  const narrow=width<600,horizontal=kind!=='trend',unit=data.unit||(kind==='trend'?'TWD':kind==='ranking'?'銷售品項數量':'件');
  const option={animation:false,color:[palette.accent],backgroundColor:'transparent',
    textStyle:{fontFamily:palette.font,fontSize:14,color:palette.ink},
    aria:{enabled:true},tooltip:{trigger:'axis',renderMode:'richText',confine:true,valueFormatter:value=>`${Number(value).toLocaleString('zh-TW',{maximumFractionDigits:2})} ${unit}`},
    grid:{left:16,right:36,top:40,bottom:kind==='trend'&&data.labels.length>31?78:40,containLabel:true},
    xAxis:horizontal?{type:'value',name:unit,minInterval:1,min:0}:{type:'category',data:data.labels,boundaryGap:false,axisLabel:{hideOverlap:true,formatter:value=>value.slice(5)}},
    yAxis:horizontal?{type:'category',inverse:true,data:data.labels,axisLabel:{width:narrow?80:190,overflow:'truncate',fontSize:14}}:{type:'value',name:unit,min:0},
    series:[{name:unit==='TWD'?'商品折後金額':unit,type:kind==='trend'?'line':'bar',data:data.values,smooth:false,showSymbol:data.labels.length<=31,barMaxWidth:28,
      label:{show:horizontal,position:'right',color:palette.ink,fontSize:14}}]};
  const numberAxis=horizontal?option.xAxis:option.yAxis;
  numberAxis.splitLine={lineStyle:{color:palette.line}};
  option.tooltip.axisPointer={type:horizontal?'shadow':'line'};
  option.series[0].emphasis={focus:'series'};
  if(kind==='trend'){option.series[0].areaStyle={opacity:0.08};option.series[0].lineStyle={width:3};}
  if(kind==='stock')option.series[0].data=data.values.map((value,index)=>({value,itemStyle:{color:['#27675b','#947027','#547a91','#a35b21','#776795','#65717c','#985451'][index]}}));
  if(kind==='shortage'){
    option.color=[palette.accent,'#a35b21'];option.legend={top:0,textStyle:{color:palette.ink,fontSize:14}};option.grid.top=65;
    option.series=[{name:'一般品可用量',type:'bar',stack:'threshold',barMaxWidth:26,data:data.available},
      {name:'距門檻缺口',type:'bar',stack:'threshold',barMaxWidth:26,data:data.values,label:{show:true,position:'right',color:palette.ink}}];
  }
  if(kind==='trend'&&data.labels.length>31)option.dataZoom=[{type:'slider',bottom:4,height:24,start:0,end:100},{type:'inside'}];
  return option;
}

function previousPeriod(start,end){
  const parse=value=>{const n=Date.parse(value+'T00:00:00Z');if(!/^\d{4}-\d{2}-\d{2}$/.test(value)||!Number.isFinite(n)||new Date(n).toISOString().slice(0,10)!==value)throw new Error('請選擇有效日期。');return n;};
  const first=parse(start),last=parse(end),days=(last-first)/86400000+1;
  if(days<1||days>366)throw new Error('比較期間須為 1 至 366 天。');
  return {start:new Date(first-days*86400000).toISOString().slice(0,10),end:new Date(first-86400000).toISOString().slice(0,10),days};
}
function metricChange(current,previous){
  if(current==null||previous==null)return {difference:null,percent:null};
  const difference=current-previous;
  return {difference,percent:previous===0?null:difference/previous*100};
}
function dashboardQuality(data){
  const target=$('dashboard-quality');
  target.replaceChildren(node('h3','資料完整度與範圍'),node('p',`本期已確認有效訂單 ${data.order_count} 單；來源待確認 ${data.pending_count} 筆（全部日期，不屬於本期有效訂單）。`));
  const dates=data.lines.map(l=>chartDay(l.ordered_at)).sort();
  target.append(node('p',dates.length?`本期已載入訂單日期：${dates[0]} ～ ${dates.at(-1)}。`:'本期沒有已確認有效訂單；不代表來源平台沒有訂單。'));
  target.append(node('p',`報表產生時間：${dateTime(data.generated_at)}。來源檔案完整涵蓋期間尚未確認；報表產生時間不等於來源資料已更新至此刻。`,'statistics-note'),node('p','此頁僅含營運訂單；KOL 獨立匯入資料及其缺漏請至 KOL 統計查看。','statistics-note'));
}
async function dashboardComparison(data,token){
  const target=$('dashboard-comparison'),period=previousPeriod(data.filters.start,data.filters.end);
  target.replaceChildren(node('h3','與等長上一期比較'),node('p','正在讀取上一期…'));
  try{
    const query=new URLSearchParams({start:period.start,end:period.end});
    const previous=await request('/api/sales?'+query);if(token!==dashboardSequence)return;
    salesChartModel(previous,boot.products);
    const currentAverage=data.order_count?Number(data.amount)/data.order_count:null,previousAverage=previous.order_count?Number(previous.amount)/previous.order_count:null;
    const rows=[['商品銷售金額（TWD）',Number(data.amount),Number(previous.amount),true],['有效訂單數',data.order_count,previous.order_count,false],['銷售品項數量',data.sales_quantity,previous.sales_quantity,false],['平均每單商品金額（TWD）',currentAverage,previousAverage,true]];
    const display=(value,money)=>value==null?'不計算':money?formatAmount(value.toFixed(2)):String(value);
    target.replaceChildren(node('h3','與等長上一期比較'),node('p',`本期 ${data.filters.start} ～ ${data.filters.end}；上期 ${period.start} ～ ${period.end}，各 ${period.days} 天。`));
    target.append(materialTable(['指標','本期','上期','增減','增減率'],rows.map(([label,current,old,money])=>{
      const change=metricChange(current,old);
      return [label,display(current,money),display(old,money),change.difference==null?'不計算':(change.difference>0?'+':'')+display(change.difference,money),change.percent==null?(old===0?'上期為 0，不計增減率':'無有效訂單，不計算'):(change.percent>0?'+':'')+change.percent.toFixed(1)+'%'];
    })),node('p','僅比較已匯入且確認的資料，尚未證實兩期來源檔案完整；若本期含今天或未來日期，亦包含尚未結束的日期。上期無資料時不推論成長率。','statistics-note'));
  }catch(error){if(token===dashboardSequence)target.replaceChildren(node('h3','與等長上一期比較'),node('p','比較讀取失敗：'+error.message+'；請再次查詢。','notice error'));}
}
let dashboardSequence=0;
async function refreshDashboard(){
  const token=++dashboardSequence,notice=$('dashboard-notice');
  const start=$('dashboard-start').value,end=$('dashboard-end').value;
  notice.textContent='正在讀取銷售總覽…';
  for(const id of ['dashboard-comparison','dashboard-quality'])$(id).replaceChildren(node('p','載入中…'));
  for(const id of ['amount','orders','quantity','average','pending','shipping','stock-alert'])$('dashboard-'+id).textContent='—';
  $('dashboard-sales-link').hidden=true;
  window.dispatchEvent(new Event('dashboard-loading'));
  try{
    const days=(Date.parse(end)-Date.parse(start))/86400000;
    if(!start||!end||!Number.isFinite(days)||days<0||days>365)throw new Error('請選擇最多 366 天的有效日期區間。');
    const query=new URLSearchParams({start,end}),data=await request('/api/sales?'+query);
    if(token!==dashboardSequence)return;
    const model=salesChartModel(data,boot.products);
    $('dashboard-amount').textContent=formatAmount(data.amount);
    $('dashboard-orders').textContent=data.order_count+' 單';
    $('dashboard-quantity').textContent=data.sales_quantity+' 個';
    $('dashboard-average').textContent=data.order_count?formatAmount((model.amountCents/100/data.order_count).toFixed(2)):'無有效訂單';
    $('dashboard-pending').textContent=data.pending_count+' 筆';
    $('dashboard-sales-link').href='#sales/'+encodeURIComponent(query.toString());$('dashboard-sales-link').hidden=false;
    notice.textContent=`${start} ～ ${end} · 全部來源／全部品項 · 更新至 ${dateTime(data.generated_at)}`;
    window.dispatchEvent(new CustomEvent('dashboard-refreshed',{detail:{data}}));
    dashboardQuality(data);await dashboardComparison(data,token);
  }catch(error){if(token!==dashboardSequence)return;notice.textContent=error.message;for(const id of ['dashboard-comparison','dashboard-quality'])$(id).replaceChildren(node('p','讀取失敗，請再次查詢。'));window.dispatchEvent(new CustomEvent('dashboard-error',{detail:{message:error.message}}));}
}

// Only the browser owns the controller; pure data/options above also run in tests.
if(typeof document!=='undefined'){
  const today=chartDay(new Date());$('dashboard-start').value=today.slice(0,8)+'01';$('dashboard-end').value=today;
  $('dashboard-form').addEventListener('submit',event=>{event.preventDefault();refreshDashboard();});
  const root=node('section',undefined,'sales-charts');root.id='sales-charts';root.setAttribute('aria-label','銷售與目前庫存圖表');$('dashboard-metrics').after(root);
  const panels={};let sequence=0,lastSales=null;
  for(const [kind,title,description] of [
    ['trend','銷售趨勢','依台北下單日期統計，可切換商品金額、銷售數量或有效訂單數；同單多筆明細只計一單。'],
    ['ranking','商品銷售排行','前 10 名，可切換銷售數量或商品金額；非庫存明細不列入排行。'],
    ['shortage','低庫存缺口排行','目前一般品可用量與距低庫存門檻的缺口，依缺口取前 10 名。不是採購建議量，不受銷售日期影響。'],
    ['stock','目前庫存狀態','按逐件在庫狀態互斥歸類。只套商品篩選，不套日期或來源；這不是庫存歷史趨勢。']]){
    const section=node('section',undefined,'sales-chart-section chart-'+kind),message=node('p','等待查詢…','statistics-note'),canvas=node('div',undefined,'sales-chart-canvas'),table=node('div'),details=node('details',undefined,'chart-values');
    details.append(node('summary','查看數字明細'),table);
    message.setAttribute('role','status');canvas.setAttribute('role','img');canvas.setAttribute('aria-label',title);canvas.hidden=true;
    section.append(node('h3',title),node('p',description,'statistics-note'),message,canvas,details);
    if(kind==='stock'){const detail=node('details',undefined,'sales-disclosure');detail.append(node('summary','目前庫存分布（不受銷售期間影響）'),section);detail.addEventListener('toggle',()=>panels.stock.resize?.());root.append(detail);}else root.append(section);
    panels[kind]={section,message,canvas,table,details,instance:null,observer:null,resize:null};
    if(['trend','ranking'].includes(kind)){const label=node('label',kind==='trend'?'趨勢指標':'排行依據'),select=node('select');for(const [value,text] of [['amount','商品金額'],['quantity','銷售品項數量'],...(kind==='trend'?[['orders','有效訂單數']]:[])])select.append(new Option(text,value));select.value=kind==='trend'?'amount':'quantity';label.append(select);message.before(label);panels[kind].select=select;select.onchange=()=>{if(lastSales)drawSales(lastSales);};}
  }
  function clear(kind,message){const p=panels[kind];p.observer?.disconnect();p.observer=null;p.instance?.dispose();p.instance=null;p.resize=null;p.canvas.hidden=true;p.table.replaceChildren();p.table.className='';p.message.textContent=message;p.section.removeAttribute('aria-busy');}
  function palette(){const css=getComputedStyle(document.documentElement);return {ink:css.getPropertyValue('--ink').trim(),accent:css.getPropertyValue('--accent').trim(),line:css.getPropertyValue('--line').trim(),font:getComputedStyle(root).fontFamily};}
  function show(kind,data,note){
    const p=panels[kind];clear(kind,note);
    if(!data.values.length||(kind==='stock'&&!data.total)){p.message.textContent=kind==='stock'?`${note} 尚無在庫件。`:kind==='shortage'?`${note} 已設定門檻的商品沒有低庫存缺口。`:'此條件沒有已確認銷售資料。';p.details.hidden=true;return;}
    p.details.hidden=false;
    const header=kind==='trend'?['日期',data.unit]:kind==='shortage'?['商品／SKU','一般品可用量','距門檻缺口']:kind==='ranking'?['商品／SKU',data.unit]:['狀態','在庫件數'];
    p.table.append(materialTable(header,data.labels.map((label,i)=>{
      let name=label;
      if(kind==='shortage'){name=node('a',label);name.href='#quality/by-product/'+encodeURIComponent(data.ids[i]);}
      if(kind==='ranking'){const query=new URLSearchParams(lastSales?.filters||{});query.set('product_id',data.ids[i]);name=node('a',label);name.href='#sales/'+encodeURIComponent(query.toString());}
      return kind==='shortage'?[name,data.available[i],data.values[i]]:[name,data.unit==='TWD'?data.values[i].toFixed(2):String(data.values[i])];
    })));
    // Exact values remain available even when the chart library fails to load.
    if(!window.echarts){p.details.open=true;p.message.textContent=note+' 圖表套件未載入，請重新整理；以下保留數字明細。';return;}
    p.canvas.hidden=false;
    p.resize=()=>{
      if(!p.canvas.isConnected||p.canvas.clientWidth===0)return;
      if(!p.instance)p.instance=echarts.init(p.canvas,null,{renderer:'svg'});
      p.instance.resize();p.instance.setOption(chartOption(kind,data,palette(),p.canvas.clientWidth),true);
    };
    p.observer=new ResizeObserver(p.resize);p.observer.observe(p.canvas);p.resize();
  }
  async function refreshStockChart(productId,token){
    try{
      const data=await request('/api/stock?summary=1');if(token!==sequence)return;
      const model=inventoryChartModel(data,productId),at=dateTime(new Date().toISOString());
      $('dashboard-shipping').textContent=(data.shipments||[]).filter(s=>['draft','allocated','picked','staged'].includes(s.state)).length+' 張';
      $('dashboard-stock-alert').textContent=`${model.lowStock} 個 SKU 低於門檻；${model.missingThresholds} 個未設定門檻`;
      show('stock',model,`查詢時間 ${at}，共 ${model.total} 件在庫。${model.missingThresholds}／${model.productCount} 個 SKU 未設定低庫存門檻；已設定者中 ${model.lowStock} 個一般品可用量低於門檻。`);
      show('shortage',model.shortage,`目前共有 ${model.lowStock} 個 SKU 低於門檻；${model.missingThresholds} 個未設定門檻，未列入評估。查詢時間 ${at}。`);
    }catch(error){if(token===sequence){for(const kind of ['stock','shortage'])clear(kind,'庫存讀取失敗：'+error.message+' 請再次查詢。');$('dashboard-shipping').textContent='讀取失敗';$('dashboard-stock-alert').textContent='讀取失敗';}}
  }
  window.addEventListener('dashboard-loading',()=>{sequence++;lastSales=null;for(const kind of Object.keys(panels)){clear(kind,'載入中…');panels[kind].section.setAttribute('aria-busy','true');}});
  window.addEventListener('dashboard-error',event=>{sequence++;lastSales=null;for(const kind of Object.keys(panels))clear(kind,'讀取失敗：'+event.detail.message+' 請再次查詢。');});
  function drawSales(data){
    const model=salesChartModel(data,boot.products,panels.trend.select.value,panels.ranking.select.value);
    show('trend',model.trend,`${data.order_count} 張有效訂單，${data.lines.length} 筆明細。`);
    show('ranking',model.ranking,`共 ${model.quantity} 個銷售品項，其中 ${model.excludedQuantity} 個非庫存品項不列入排行。`);
  }
  window.addEventListener('dashboard-refreshed',event=>{
    if(!event.detail?.data)return;const token=++sequence,data=event.detail.data;
    try{
      lastSales=data;drawSales(data);
    }catch(error){clear('trend',error.message);clear('ranking',error.message);}
    clear('stock','載入目前庫存…');refreshStockChart(data.filters.product_id,token);
  });
  window.addEventListener('hashchange',()=>{for(const p of Object.values(panels))p.resize?.();});
  window.addEventListener('resize',()=>{for(const p of Object.values(panels))p.resize?.();});
}
if(typeof module!=='undefined')module.exports={salesChartModel,inventoryChartModel,chartOption,previousPeriod,metricChange};
