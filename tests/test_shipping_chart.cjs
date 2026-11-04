const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const echarts=require('../web/vendor/echarts-6.1.0/echarts.min.js');
const source=fs.readFileSync('web/shipping-stats.js','utf8');
const context=vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function shippingTrendModel'),source.indexOf('function renderShippingTrend')),context);
const {shippingTrendModel:model,shippingTrendOption:option}=context;
const data={start:'2026-09-30',end:'2026-10-02',totals:{shipped:5,returned:2},shipping_daily:[{day:'2026-09-30',shipped:5,returned:0},{day:'2026-10-02',shipped:0,returned:2}]};
const plain=value=>JSON.parse(JSON.stringify(value));
assert.deepEqual(plain(model(data)),[{label:'2026-09-30',shipped:5,returned:0},{label:'2026-10-01',shipped:0,returned:0},{label:'2026-10-02',shipped:0,returned:2}]);
assert.deepEqual(plain(model(data,'month')),[{label:'2026-09',shipped:5,returned:0},{label:'2026-10',shipped:0,returned:2}]);
assert.equal(model({...data,shipping_daily:[],totals:{shipped:0,returned:0}}).length,3);
assert.throws(()=>model({...data,totals:{shipped:6,returned:2}}));
assert.throws(()=>model({...data,shipping_daily:[...data.shipping_daily,data.shipping_daily[0]]}));
assert.throws(()=>model({...data,end:'2026-09-01'}));
const grouped={totals:{shipped:5,returned:8},channels:[{channel:'網店',shipped:5,returned:1},{channel:'',shipped:0,returned:7}]};
const compared=context.shippingBreakdownModel(grouped,'channels','returned');
assert.equal(compared[0].label,'未填通路');assert.equal(compared[0].returned,7);
assert.throws(()=>context.shippingBreakdownModel({...grouped,totals:{shipped:6,returned:8}},'channels'));
for(const width of [390,768,1280]){
 const chart=echarts.init(null,null,{renderer:'svg',ssr:true,width,height:520});
 chart.setOption(context.shippingBreakdownOption(compared,width));assert.ok(!/NaN|Infinity/.test(chart.renderToSVGString()));chart.dispose();
}
for(const mode of ['day','month']){
 const chart=echarts.init(null,null,{renderer:'svg',ssr:true,width:1280,height:360});
 for(const width of [1280,768,390]){
  chart.resize({width,height:360});chart.setOption(option(model(data,mode)),true);
  const svg=chart.renderToSVGString();assert.ok(svg.includes(`width="${width}"`));assert.ok(!/NaN|Infinity/.test(svg));
  assert.deepEqual(chart.getOption().series.map(s=>s.data.reduce((a,b)=>a+b,0)),[5,2]);
 }
 chart.dispose();
}
console.log('Shipping chart: missing dates, partial months, returns-only dates, totals, invalid data, and SVG widths passed.');
