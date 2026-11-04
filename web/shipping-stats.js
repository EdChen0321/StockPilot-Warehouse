'use strict';
let shippingStatisticsSequence=0;
let disposeShippingTrend=()=>{};
function shippingTrendModel(data,mode='day'){
  const start=Date.parse(data.start+'T00:00:00Z'),end=Date.parse(data.end+'T00:00:00Z');
  if(!Number.isFinite(start)||!Number.isFinite(end)||end<start||(end-start)/86400000>3660)throw new Error('圖表日期範圍不正確或過長。');
  const days=new Map();let shipped=0,returned=0;
  for(const row of data.shipping_daily){
    if(days.has(row.day)||row.day<data.start||row.day>data.end||!Number.isSafeInteger(row.shipped)||row.shipped<0||!Number.isSafeInteger(row.returned)||row.returned<0)throw new Error('每日出退貨數據無法核對。');
    days.set(row.day,row);shipped+=row.shipped;returned+=row.returned;
  }
  if(shipped!==data.totals.shipped||returned!==data.totals.returned)throw new Error('圖表與期間出退貨總量不符。');
  const groups=new Map();
  for(let at=start;at<=end;at+=86400000){const day=new Date(at).toISOString().slice(0,10),key=mode==='month'?day.slice(0,7):day;
    if(!groups.has(key))groups.set(key,{label:key,shipped:0,returned:0});
    const group=groups.get(key),row=days.get(day);group.shipped+=row?.shipped||0;group.returned+=row?.returned||0;
  }
  return [...groups.values()];
}
function shippingTrendOption(rows){
  return {animation:false,aria:{enabled:true},color:['#1b6c80','#a35b21'],
    legend:{top:0,data:['完成出貨','實際退貨']},
    tooltip:{trigger:'axis',confine:true,renderMode:'richText',valueFormatter:value=>value+' 件'},
    grid:{left:12,right:20,top:64,bottom:16,containLabel:true},
    xAxis:{type:'category',data:rows.map(row=>row.label),axisLabel:{hideOverlap:true}},
    yAxis:{type:'value',name:'件數',min:0,minInterval:1},
    series:[{name:'完成出貨',type:'bar',barMaxWidth:32,data:rows.map(row=>row.shipped)},
      {name:'實際退貨',type:'line',symbol:'circle',symbolSize:7,data:rows.map(row=>row.returned)}]};
}
function shippingBreakdownModel(data,key,metric='shipped'){
  const rows=data[key];
  for(const field of ['shipped','returned']){
    if(rows.some(row=>!Number.isSafeInteger(row[field])||row[field]<0)||rows.reduce((n,row)=>n+row[field],0)!==data.totals[field])throw new Error('比較圖與出退貨總量不符。');
  }
  return [...rows].sort((a,b)=>b[metric]-a[metric]||String(a.product_id??a.channel).localeCompare(String(b.product_id??b.channel))).slice(0,10)
    .map(row=>({label:key==='channels'?(row.channel||'未填通路'):row.name+' · '+row.sku,shipped:row.shipped,returned:row.returned}));
}
function shippingBreakdownOption(rows,width){
  return {animation:false,aria:{enabled:true},color:['#27675b','#a35b21'],textStyle:{fontSize:14},
    legend:{top:0,data:['完成出貨','實際退貨']},
    tooltip:{trigger:'axis',axisPointer:{type:'shadow'},confine:true,renderMode:'richText',valueFormatter:value=>value+' 件'},
    grid:{left:12,right:48,top:56,bottom:20,containLabel:true},
    xAxis:{type:'value',min:0,minInterval:1,name:'件'},
    yAxis:{type:'category',inverse:true,data:rows.map(row=>row.label),axisLabel:{width:width<600?90:180,overflow:'truncate'}},
    series:['shipped','returned'].map((key,i)=>({name:i?'實際退貨':'完成出貨',type:'bar',barMaxWidth:18,data:rows.map(row=>row[key]),label:{show:true,position:'right'}}))};
}
function renderShippingTrend(target,data){
  const figure=node('figure',undefined,'shipping-trend'),label=node('label','彙總方式'),mode=node('select'),note=node('p',undefined,'statistics-note'),canvas=node('div',undefined,'shipping-trend-canvas');
  mode.append(new Option('每日','day'),new Option('每月','month'));mode.value=(Date.parse(data.end)-Date.parse(data.start))/86400000>90?'month':'day';label.append(mode);
  figure.append(node('figcaption','出貨與退貨趨勢'),label,note,canvas);target.append(figure);canvas.setAttribute('role','img');canvas.setAttribute('aria-label','完成出貨柱狀圖與實際退貨折線圖，單位為件');
  let chart,observer,rows=null;const comparisons=[];
  const draw=()=>{
    if(!rows||!canvas.isConnected||!canvas.clientWidth)return;
    if(!globalThis.echarts){note.textContent='圖表套件未載入，請查看下方每日／每月出貨統計表。';return;}
    try{if(!chart)chart=echarts.init(canvas,null,{renderer:'svg'});chart.resize();chart.setOption(shippingTrendOption(rows),true);}
    catch(error){chart?.dispose();chart=null;note.textContent='圖表無法顯示，請查看下方統計表。';}
  };
  const update=()=>{try{rows=shippingTrendModel(data,mode.value);note.textContent=(mode.value==='month'?'按月彙總，首尾月份僅計所選日期。':'按實際完成出貨與退貨日期，沒有異動的日期顯示 0。')+(!data.totals.shipped&&!data.totals.returned?' 此期間沒有符合條件的出退貨。':'');draw();}catch(error){rows=null;chart?.dispose();chart=null;canvas.replaceChildren();note.textContent=error.message+' 請查看下方統計表。';}};
  mode.onchange=update;update();
  const comparisonRoot=node('div',undefined,'shipping-comparisons');target.append(comparisonRoot);
  for(const [key,title] of [['channels','通路出退貨比較'],['shipping_products','商品出退貨排行']]){
    const panel=node('figure',undefined,'shipping-trend'),plot=node('div',undefined,'shipping-trend-canvas'),caption=node('p',undefined,'statistics-note'),label=node('label','排序依據'),select=node('select');
    select.append(new Option('完成出貨件數','shipped'),new Option('實際退貨件數','returned'));select.value='shipped';label.append(select);
    plot.setAttribute('role','img');plot.setAttribute('aria-label',title+'，單位：件');panel.append(node('figcaption',title),label,caption,plot);comparisonRoot.append(panel);
    const item={chart:null,observer:null};comparisons.push(item);
    const redraw=()=>{
      if(!plot.isConnected||!plot.clientWidth)return;
      try{
        const values=shippingBreakdownModel(data,key,select.value);
        caption.textContent=values.length?'依所選指標取前 10 名，出貨與退貨分開比較；完整數字與 CSV 見下方統計表。':'此期間沒有符合條件的出退貨紀錄。';
        if(!values.length){plot.hidden=true;return;}plot.hidden=false;
        if(!globalThis.echarts){caption.textContent='圖表套件未載入，請查看下方統計表。';return;}
        if(!item.chart)item.chart=echarts.init(plot,null,{renderer:'svg'});
        item.chart.resize();item.chart.setOption(shippingBreakdownOption(values,plot.clientWidth),true);
      }catch(error){item.chart?.dispose();item.chart=null;caption.textContent=error.message+' 請查看下方統計表。';}
    };
    select.onchange=redraw;item.redraw=redraw;redraw();
    if(typeof ResizeObserver!=='undefined'){item.observer=new ResizeObserver(redraw);item.observer.observe(plot);}window.addEventListener('resize',redraw);
  }
  if(typeof ResizeObserver!=='undefined'){observer=new ResizeObserver(draw);observer.observe(canvas);}window.addEventListener('resize',draw);
  disposeShippingTrend=()=>{observer?.disconnect();chart?.dispose();chart=null;window.removeEventListener('resize',draw);for(const item of comparisons){item.observer?.disconnect();item.chart?.dispose();window.removeEventListener('resize',item.redraw);}};
}
function statisticsDate(){return new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());}
$('shipping-statistics-end').value=statisticsDate();
$('shipping-statistics-start').value=statisticsDate().slice(0,8)+'01';

function shippingStatisticsTable(title,rows,firstHeading,label,query,kind){
  const section=node('section',undefined,'shipping-statistics-section'),heading=node('div',undefined,'section-heading');
  const download=node('a','匯出 CSV','button secondary');download.href='/api/stock/shipping-statistics/export?'+query+'&kind='+kind;download.download='shipping-'+kind+'.csv';
  heading.append(node('h3',title),download);section.append(heading);
  const wrap=node('div',undefined,'table-scroll'),table=node('table'),head=node('thead'),tr=node('tr'),body=node('tbody');
  for(const [index,name] of [firstHeading,'訂單數','出貨單數','出貨件數','退貨件數','期間淨出貨','出貨成本','退貨成本'].entries()){const th=node('th',name,index?'numeric':undefined);th.setAttribute('scope','col');tr.append(th);}head.append(tr);table.append(head,body);
  for(const row of rows){const line=node('tr'),name=node('td');if(kind==='shipping_details'){const a=node('a',label(row));a.href='#shipping/'+encodeURIComponent(row.shipment_id);name.append(a);}else name.textContent=label(row);line.append(name);
    for(const field of ['shipping_orders','shipment_count','shipped','returned','net_shipped','shipped_cost','returned_cost']){
      const cell=node('td',field.endsWith('_cost')?(row[field]==null?'待填費用':formatAmount(row[field])):String(row[field]),'numeric');
      line.append(cell);
    }body.append(line);
  }
  if(!rows.length)emptyRow(body,8,'此期間沒有符合條件的出貨或退貨。');wrap.append(table);section.append(wrap);return section;
}

async function refreshShippingCurrent(sequence){
  const target=$('shipping-current');target.replaceChildren(node('p','正在讀取未完成出貨單…'));
  try{
    const snapshot=await request('/api/stock?summary=1');if(sequence!==shippingStatisticsSequence)return;
    const counts=new Map(['draft','allocated','picked','staged'].map(state=>[state,0]));
    for(const shipment of snapshot.shipments)if(counts.has(shipment.state))counts.set(shipment.state,counts.get(shipment.state)+1);
    const total=[...counts.values()].reduce((sum,count)=>sum+count,0);
    target.replaceChildren(stockDetailGrid([['未完成出貨單合計',total+' 張'],['準備中',counts.get('draft')+' 張'],['已配貨',counts.get('allocated')+' 張'],['已揀貨',counts.get('picked')+' 張'],['歷史待寄',counts.get('staged')+' 張']]),node('p',`目前狀態查詢時間：${dateTime(new Date().toISOString())}`,'hint'));
  }catch(error){if(sequence===shippingStatisticsSequence)target.replaceChildren(node('p','未完成出貨單讀取失敗：'+error.message,'notice error'));}
}

async function refreshShippingStatistics(){
  disposeShippingTrend();
  const sequence=++shippingStatisticsSequence,notice=$('shipping-statistics-notice'),target=$('shipping-statistics-result');
  const currentRequest=refreshShippingCurrent(sequence);
  const query=new URLSearchParams({start:$('shipping-statistics-start').value,end:$('shipping-statistics-end').value,product_id:$('shipping-statistics-product').value});
  const selected=$('shipping-statistics-channel').value;if(selected!=='all')query.set('channel',JSON.parse(selected));
  notice.textContent='正在查詢…';target.replaceChildren();
  try{
    const data=await request('/api/stock/shipping-statistics?'+query);if(sequence!==shippingStatisticsSequence)return;
    options('shipping-statistics-channel',data.available_channels.map(channel=>({value:JSON.stringify(channel),label:channel||'未填通路'})),'全部通路');
    const selector=$('shipping-statistics-channel');selector.options[0].value='all';selector.value=[...selector.options].some(o=>o.value===selected)?selected:'all';
    query.set('as_of',data.as_of);query.set('start',data.start);query.set('end',data.end);
    target.append(stockDetailSections([
      ['期間出退貨數量', [['完成出貨件數',data.totals.shipped+' 件'],['實際退貨件數',data.totals.returned+' 件'],['期間淨出貨',data.totals.net_shipped+' 件']]],
      ['單據與成本', [['出貨訂單',data.totals.shipping_orders+' 單'],['出貨單',data.totals.shipment_count+' 張'],['出貨成本',data.totals.shipped_cost==null?'待填費用':formatAmount(data.totals.shipped_cost)+' 元'],['退貨成本',data.totals.returned_cost==null?'待填費用':formatAmount(data.totals.returned_cost)+' 元']]]
    ]));
    notice.textContent=`${data.start} ～ ${data.end} · 更新至 ${dateTime(data.as_of)}`;
    renderShippingTrend(target,data);
    target.append(node('p','按完成出貨與實際退貨日期統計，不按下單日期；期間淨出貨＝完成出貨件數－實際退貨件數，可能包含以前出貨、本期才退回的商品。主副單合計一筆訂單，出貨與退貨成本分開列示。','statistics-note'));
    target.append(node('p','同一訂單可能分屬多個通路、商品或日期，各列訂單數不能直接相加。','statistics-note'));
    for(const [kind,title,heading,label] of [
      ['channels','出貨通路統計','通路',r=>r.channel||'未填通路'],
      ['shipping_products','商品出貨統計','商品／SKU',r=>r.name+' · '+r.sku],
      ['shipping_daily','每日出貨統計','日期',r=>r.day],
      ['shipping_monthly','每月出貨統計','月份',r=>r.month],
      ['shipping_details','出貨單明細','訂單編號',r=>r.number],
    ])target.append(shippingStatisticsTable(title,data[kind],heading,label,query,kind));
  }catch(error){if(sequence===shippingStatisticsSequence){notice.textContent=error.message;target.replaceChildren();const retry=node('button','重新查詢','secondary');retry.onclick=refreshShippingStatistics;target.append(retry);}}
  await currentRequest;
}
$('shipping-statistics-form').addEventListener('submit',event=>{event.preventDefault();refreshShippingStatistics();});
window.addEventListener('stock-refreshed',()=>{options('shipping-statistics-product',stockData.products.map(p=>({value:p.id,label:p.name+' · '+p.sku})),'全部商品');});
window.addEventListener('hashchange',()=>{if(location.hash.split('/')[0]!=='#shipping-statistics'){shippingStatisticsSequence++;disposeShippingTrend();}});
