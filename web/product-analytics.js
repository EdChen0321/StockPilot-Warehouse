'use strict';
let productAnalyticsSequence=0;
function productAnalyticsPath(id,query,day=''){
  return 'product-analytics/'+encodeURIComponent(id)+'/'+encodeURIComponent(query.toString())+(day?'/'+day:'');
}
function productAnalyticsLink(label,path){const link=node('a',label,'button secondary');link.href='#'+path;return link;}
function productAnalyticsTable(headers,rows){
  const wrap=node('div',undefined,'table-scroll'),table=node('table'),head=node('thead'),line=node('tr'),body=node('tbody');
  for(const title of headers)line.append(node('th',title));head.append(line);
  for(const values of rows){const row=node('tr');for(const value of values){const cell=node('td');if(value instanceof Node)cell.append(value);else cell.textContent=String(value??'資料不足');row.append(cell);}body.append(row);}
  if(!rows.length)emptyRow(body,headers.length,'此條件沒有出貨或退貨紀錄。');
  table.append(head,body);wrap.append(table);return wrap;
}
let disposeProductSalesChart=()=>{};
function productSalesBuckets(days,counts,mode='auto'){
  const grain=mode==='auto'?(days.length<=31?'day':days.length<=180?'week':'month'):mode;
  const groups=new Map();
  for(const {day} of days){
    let key=day;
    if(grain==='month')key=day.slice(0,7);
    if(grain==='week'){const date=new Date(day+'T00:00:00Z');date.setUTCDate(date.getUTCDate()-(date.getUTCDay()+6)%7);key=date.toISOString().slice(0,10);}
    if(!groups.has(key))groups.set(key,{start:day,end:day,quantity:0});
    const bucket=groups.get(key);bucket.end=day;bucket.quantity+=counts.get(day)?.ship||0;
  }
  return {grain,buckets:[...groups.values()]};
}
function productSalesOption(buckets,palette={}){
  return {animation:false,aria:{enabled:true},color:[palette.accent||'#27675b'],textStyle:{fontFamily:palette.font,color:palette.ink||'#263238'},
    tooltip:{trigger:'axis',confine:true,renderMode:'richText',valueFormatter:value=>value+' 件'},
    grid:{left:12,right:16,top:45,bottom:15,containLabel:true},
    xAxis:{type:'category',data:buckets.map(b=>b.start===b.end?b.start:b.start+' ～ '+b.end),axisLabel:{hideOverlap:true,formatter:value=>value.slice(5,10).replace('-','/')}},
    yAxis:{type:'value',min:0,minInterval:1,name:'完成出貨（件）',splitLine:{lineStyle:{color:palette.line||'#e2e5e7'}}},
    series:[{name:'銷量（件）',type:'bar',barMaxWidth:36,data:buckets.map(b=>b.quantity)}]};
}
function productSalesChart(id,query,days,counts){
  const figure=node('figure',undefined,'sku-sales-chart');figure.setAttribute('aria-label','銷量圖，單位：件');
  const label=node('label',undefined,'analytics-chart-control'),select=node('select');label.append(node('span','彙總方式'));
  for(const [value,title] of [['auto','自動'],['day','每日'],['week','每週'],['month','每月']])select.append(new Option(title,value));
  select.value=['day','week','month'].includes(query.get('grain'))?query.get('grain'):'auto';label.append(select);
  const note=node('p',undefined,'statistics-note'),canvas=node('div',undefined,'sku-sales-canvas'),details=node('details'),links=node('div');
  details.append(node('summary','查看各期銷量與明細'),links);
  figure.append(node('figcaption','銷量（件）'),label,note,canvas,details);
  let chart,observer,buckets=[];
  const path=b=>productAnalyticsPath(id,query,b.start)+(b.end!==b.start?'/'+b.end:'');
  const draw=()=>{
    if(!canvas.isConnected||!canvas.clientWidth)return;
    if(!globalThis.echarts){canvas.textContent='圖表載入失敗，請重新整理；可展開下方各期銷量查看明細。';return;}
    const style=getComputedStyle(document.documentElement),palette={accent:style.getPropertyValue('--accent').trim(),ink:style.getPropertyValue('--ink').trim(),line:style.getPropertyValue('--line').trim(),font:getComputedStyle(figure).fontFamily};
    if(!chart){chart=echarts.init(canvas,null,{renderer:'svg'});chart.on('click',event=>{const bucket=buckets[event.dataIndex];if(bucket)navigatePage(path(bucket));});}
    chart.resize();chart.setOption(productSalesOption(buckets,palette),true);
  };
  const update=()=>{
    const result=productSalesBuckets(days,counts,select.value);buckets=result.buckets;
    note.textContent='以'+({day:'日',week:'週（一至日）',month:'月'}[result.grain])+'彙總，完整顯示所選期間；首尾僅計入篩選日期。點圖形查看該期明細。'+(buckets.some(b=>b.quantity)?'':' 此期間沒有符合條件的完成出貨。');
    links.replaceChildren(...buckets.map(b=>productAnalyticsLink((b.start===b.end?b.start:b.start+' ～ '+b.end)+'：'+b.quantity+' 件',path(b))));draw();
  };
  select.onchange=()=>{query.set('grain',select.value);update();};
  figure.mount=()=>{update();if(typeof ResizeObserver!=='undefined'){observer=new ResizeObserver(draw);observer.observe(canvas);}window.addEventListener('resize',draw);};
  disposeProductSalesChart=()=>{observer?.disconnect();chart?.dispose();window.removeEventListener('resize',draw);};
  return figure;
}
if(typeof window!=='undefined')window.addEventListener('hashchange',()=>{
  if(!window.location.hash.startsWith('#product-analytics/')){productAnalyticsSequence++;disposeProductSalesChart();}
});
if(typeof module!=='undefined')module.exports={productSalesBuckets,productSalesOption};
async function renderProductAnalytics(parts){
  disposeProductSalesChart();
  const sequence=++productAnalyticsSequence,root=$('product-analytics');
  root.replaceChildren(productAnalyticsLink('返回商品列表','catalogue'),node('h2','商品數據分析'));
  const notice=node('p','正在讀取商品數據…');notice.setAttribute('role','status');root.append(notice);
  try{
    const id=decodeURIComponent(parts[1]||'');if(!id)throw new Error('請由商品庫選擇 SKU。');
    const query=new URLSearchParams(decodeURIComponent(parts[2]||''));
    const today=statisticsDate(),yesterday=new Date(today+'T00:00:00Z');yesterday.setUTCDate(yesterday.getUTCDate()-1);
    if(!query.get('end'))query.set('end',yesterday.toISOString().slice(0,10));
    if(!query.get('start'))query.set('start',query.get('end').slice(0,8)+'01');
    const start=query.get('start'),end=query.get('end'),span=(Date.parse(end)-Date.parse(start))/86400000;
    if(!Number.isFinite(span)||span<0||span>365||end>today)throw new Error('請選擇截至今天、最多 366 天的有效日期區間。');
    const apiQuery=new URLSearchParams({product_id:id,start,end});
    const [product,data]=await Promise.all([request('/api/catalogue/'+encodeURIComponent(id)),request('/api/stock/reports?'+apiQuery)]);
    if(sequence!==productAnalyticsSequence)return;
    root.querySelector('h2').textContent=product.name+' · '+product.sku+' 數據分析';
    if(product.kind!=='product')throw new Error('套組沒有獨立庫存件，請由組成 SKU 查看出貨紀錄。');
    const form=node('form',undefined,'search-bar'),inputs={};
    for(const [name,label] of [['start','開始日期'],['end','結束日期']]){const field=node('label',label),input=node('input');input.type='date';input.required=true;input.max=today;input.value=query.get(name);field.append(input);form.append(field);inputs[name]=input;}
    const channelLabel=node('label','出貨通路'),channel=node('select');channel.append(new Option('全部通路',''));
    for(const value of [...new Set(data.unit_records.map(r=>r.channel))].sort())channel.append(new Option(value||'未填通路',JSON.stringify(value)));
    // Preserve a selected channel even if the next date range has no matching records.
    const selected=query.get('channel')||'';if(selected&&![...channel.options].some(o=>o.value===selected))channel.append(new Option(JSON.parse(selected)||'未填通路',selected));
    channel.value=selected;channelLabel.append(channel);const submit=node('button','查詢');submit.type='submit';form.append(channelLabel,submit);root.append(form);
    form.onsubmit=event=>{event.preventDefault();const next=new URLSearchParams({start:inputs.start.value,end:inputs.end.value});if(query.has('grain'))next.set('grain',query.get('grain'));if(channel.value)next.set('channel',channel.value);navigatePage(productAnalyticsPath(id,next));};
    const records=data.unit_records.filter(r=>!selected||r.channel===JSON.parse(selected));
    const inventory=data.inventory[0],shipped=records.filter(r=>r.kind==='ship').length,returned=records.filter(r=>r.kind==='return').length;
    const average=data.data_complete?shipped/data.days:null;
    // Coverage uses all-channel demand; a channel filter must not inflate coverage estimates.
    const coverage=inventory?.coverage_days;
    const number=value=>new Intl.NumberFormat('zh-TW',{maximumFractionDigits:2}).format(value);
    root.append(stockDetailSections([
      ['期間銷售', [['出貨通路',selected?(JSON.parse(selected)||'未填通路'):'全部通路'],['期間銷量（完成出貨）',shipped+' 件'],['期間退貨',returned+' 件'],['平均每日銷量',average===null?'資料不足':number(average)+' 件／天']]],
      ['目前庫存（全部通路）', [['目前在庫',inventory?.on_hand==null?'資料不足':inventory.on_hand+' 件'],['目前可出貨',inventory?.available==null?'資料不足':inventory.available+' 件'],['預計庫存天數',coverage==null?(inventory?.coverage_status||'資料不足'):number(coverage)+' 天'],['庫存截至',dateTime(data.as_of)]]]
    ]));
    notice.textContent=`${data.start} ～ ${data.end}，共 ${data.days} 天；更新至 ${dateTime(data.as_of)}`;
    root.append(node('p','銷量按完成出貨日期計算，退貨另列，撤銷完成不計銷量。庫存與庫存天數使用全部通路；天數以可出貨庫存及完整期間平均出貨量估算。'),node('p',data.data_complete?'期間資料完整。':'期間含尚未結束的日期或歷史資料不足；平均銷量與庫存天數不估算。','statistics-note'));
    const base=productAnalyticsPath(id,query),day=parts[3],last=parts[4]||day;
    if(day){
      if(!/^\d{4}-\d{2}-\d{2}$/.test(day)||day<start||day>end||!/^\d{4}-\d{2}-\d{2}$/.test(last)||last<day||last>end)throw new Error('日期不在查詢區間內。');
      root.append(productAnalyticsLink('返回每日數據',base),node('h3',(day===last?day:day+' ～ '+last)+' 逐件出退貨紀錄'),productAnalyticsTable(['時間','品號','動作','通路','出貨單'],records.filter(r=>r.day>=day&&r.day<=last).map(r=>[dateTime(r.time),productAnalyticsLink(r.unit_id,'quality/'+encodeURIComponent(r.unit_id)),r.kind==='ship'?'完成出貨':'退貨入庫',r.channel||'未填通路',productAnalyticsLink(r.number,'shipping/'+encodeURIComponent(r.shipment_id))])));
      return;
    }
    const days=new Map(data.daily.map(d=>[d.day,d])),counts=new Map();
    for(const r of records){if(!counts.has(r.day))counts.set(r.day,{ship:0,return:0});counts.get(r.day)[r.kind]++;}
    const salesChart=productSalesChart(id,query,data.daily_stock,counts);root.append(salesChart);salesChart.mount();
    root.append(node('h3','每日庫存明細'),node('p','點日期查看當日逐件紀錄。當日庫存為日終在庫；今天則截至查詢時間。'));
    root.append(productAnalyticsTable(['日期','退貨','入庫（全部通路）','庫存變動','在庫數量'],data.daily_stock.map(d=>{
      const count=counts.get(d.day)||{ship:0,return:0};
      return [productAnalyticsLink(d.day,productAnalyticsPath(id,query,d.day)),count.return,days.get(d.day)?.received||0,d.change,d.on_hand];
    })));
  }catch(error){if(sequence===productAnalyticsSequence){notice.textContent=error.message;notice.className='notice error';const retry=node('button','重新讀取');retry.onclick=()=>renderProductAnalytics(parts);root.append(retry);}}
}
