const assert=require('node:assert/strict');
const {salesChartModel,inventoryChartModel,chartOption}=require('../web/sales-charts.js');
const lines=[1,2].map(n=>({account_id:'a',order_id:'one',line_id:String(n),ordered_at:'2026-10-01T00:00:00Z',product_id:'p',product_name:'杯',quantity:1,amount:'10.00'}));
const sales=salesChartModel({lines,amount:'20.00',sales_quantity:2,order_count:1,filters:{start:'2026-10-01',end:'2026-10-02'}},[],'orders');
assert.deepEqual(sales.trend.values,[1,0],'One order with two lines counts once each day');
const stock=inventoryChartModel({products:[{id:'p',name:'杯',sku:'P',on_hand:0,available:0},{id:'q',name:'盤',sku:'Q',on_hand:0,available:0}],units:[],alert_settings:[{product_id:'p',stock_min:5}]});
assert.deepEqual(stock.shortage.values,[5]);
assert.deepEqual(stock.shortage.available,[0]);
assert.equal(stock.missingThresholds,1,'Unknown thresholds must not become zero');
assert.equal(stock.shortage.ids[0],'p');
const palette={ink:'#263238',accent:'#27675b',line:'#ddd',font:'sans-serif'};
const option=chartOption('shortage',stock.shortage,palette,390);
assert.equal(option.series.length,2);
assert.deepEqual(option.series[1].data,[5]);
const echarts=require('../web/vendor/echarts-6.1.0/echarts.min.js');
for(const width of [390,768,1280])for(const [kind,data] of [['shortage',stock.shortage],['trend',sales.trend],['stock',stock]]){
 const chart=echarts.init(null,null,{renderer:'svg',ssr:true,width,height:420});
 chart.setOption(chartOption(kind,data,palette,width));
 const svg=chart.renderToSVGString();assert.ok(!/NaN|Infinity/.test(svg));chart.dispose();
}
console.log('Analytics: distinct daily orders, shortage/unknown threshold boundary, ECharts SVG at three widths passed.');
