const assert=require('node:assert/strict');
const {salesChartModel,inventoryChartModel,chartOption}=require('../web/sales-charts.js');
const lines=[
 {account_id:'a',order_id:'one',line_id:'1',ordered_at:'2026-10-01T16:00:00Z',product_id:'a',product_name:'同名商品',quantity:2,amount:'100.10'},
 {account_id:'a',order_id:'one',line_id:'2',ordered_at:'2026-10-01T16:00:00Z',product_id:'b',product_name:'同名商品',quantity:1,amount:'200.20'},
 {account_id:'a',order_id:'two',line_id:'1',ordered_at:'2026-10-04T00:00:00Z',product_id:null,product_name:'非庫存',line_type:'non_stock',quantity:1,amount:'0.30'}];
const model=salesChartModel({lines,amount:'300.60',sales_quantity:4,order_count:2,filters:{start:'2026-10-01',end:'2026-10-04'}},[{id:'a',sku:'SKU-A'},{id:'b',sku:'SKU-B'}]);
assert.deepEqual(model.trend.values,[0,300.30,0,0.30]);assert.equal(model.amountCents,30060);assert.equal(model.orderCount,2);
assert.deepEqual(model.ranking.values,[2,1]);assert.match(model.ranking.labels[0],/SKU-A/);assert.equal(model.excludedQuantity,1);
const switched=salesChartModel({lines,amount:'300.60',sales_quantity:4,order_count:2,filters:{start:'2026-10-01',end:'2026-10-04'}},[],'quantity','amount');
assert.deepEqual(switched.trend.values,[0,3,0,1]);assert.deepEqual(switched.ranking.values,[200.20,100.10]);assert.equal(switched.ranking.unit,'TWD');assert.equal(switched.trend.unit,'銷售品項數量');
assert.throws(()=>salesChartModel({lines,amount:'300.61',sales_quantity:4,order_count:2,filters:{}}),/核對/);
assert.equal(salesChartModel({lines:[],amount:'0.00',sales_quantity:0,order_count:0,filters:{}}).trend.values.length,0);
const base={product_id:'p',location:'warehouse',quality:'good',packaging:'good',place_eligible:true};
const stock=inventoryChartModel({products:[{id:'p',on_hand:4,available:1,box_available:1}],alert_settings:[],units:[
 {...base,id:'1'},{...base,id:'2',packaging:'damaged'},{...base,id:'3',shipment_id:'s'},
 {...base,id:'4',return_hold:true,quality:'defective'},{...base,id:'5',location:'customer'}]},'p');
assert.equal(stock.total,4);assert.equal(stock.values.reduce((a,b)=>a+b,0),4);assert.equal(stock.missingThresholds,1);
assert.equal(stock.lowStock,0);assert.equal(stock.values[stock.labels.indexOf('退貨限制')],1);
assert.equal(inventoryChartModel({products:[],units:[],alert_settings:[]},'bundle').total,0);
for(const kind of ['trend','ranking','stock']){
 const option=chartOption(kind,kind==='stock'?stock:model[kind],{ink:'#203a36',accent:'#23634f',line:'#d8dfd7',font:'sans-serif'},320);
 assert.equal(option.animation,false);assert.ok(option.series.length);assert.equal(option.tooltip.renderMode,'richText');
}
console.log('Chart amounts, order grain, Taipei day, zero days, ranking identity, non-stock exclusion and mutually exclusive inventory passed.');
