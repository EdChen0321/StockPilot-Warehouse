const assert=require('node:assert/strict');
const {productSalesBuckets,productSalesOption}=require('../web/product-analytics.js');
const echarts=require('../web/vendor/echarts-6.1.0/echarts.min.js');
const days=[];
for(let date=new Date('2026-07-01T00:00:00Z');date<=new Date('2026-10-29T00:00:00Z');date.setUTCDate(date.getUTCDate()+1))days.push({day:date.toISOString().slice(0,10)});
const counts=new Map([['2026-07-01',{ship:3,return:2}],['2026-08-01',{ship:5}],['2026-10-29',{ship:2}]]);
assert.equal(productSalesBuckets(days,counts).grain,'week');
for(const grain of ['day','week','month']){
 const {buckets}=productSalesBuckets(days,counts,grain);
 assert.equal(buckets.reduce((s,b)=>s+b.quantity,0),10);
 assert.equal(buckets[0].start,'2026-07-01');assert.equal(buckets.at(-1).end,'2026-10-29');
 if(grain==='month')assert.deepEqual(buckets.map(b=>b.quantity),[3,5,0,2]);
 if(grain==='week')assert.equal(buckets[0].end,'2026-07-05');
 const option=productSalesOption(buckets);assert.equal(option.dataZoom,undefined);
 const chart=echarts.init(null,null,{renderer:'svg',ssr:true,width:1280,height:360});
 for(const width of [1280,768,390]){chart.resize({width,height:360});chart.setOption(option,true);const svg=chart.renderToSVGString();assert.ok(svg.includes(`width="${width}"`));assert.ok(!/NaN|Infinity/.test(svg));assert.equal(chart.getOption().series[0].data.length,buckets.length);}
 chart.dispose();
}
assert.deepEqual(productSalesBuckets([],counts).buckets,[]);
console.log('SKU charts: partial weeks/months, totals, zero periods, full-range SVG at 1280/768/390 passed.');

