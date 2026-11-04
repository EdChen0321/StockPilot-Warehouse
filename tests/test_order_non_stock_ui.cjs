const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('web/stock.js','utf8');
const fields={};const handlers={};const requests=[];let pending;
const field=id=>fields[id]??= {value:'',dataset:{},addEventListener:(_event,fn)=>handlers[id]=fn};
const ctx=vm.createContext({$:field,boot:{products:[{id:'p',name:'實體商品'}]},
  window:{addEventListener(){}},
  stockData:{groups:[],shipments:[],additions:[]},
  productName:id=>id==='p'?'實體商品':id,
  operation:()=> 'test',localInstant:v=>v,
  stockAction:(_area,_control,work)=>pending=work(),request:async(path,body)=>{requests.push({path,body});return {id:'shipment'};},
  refreshBootstrap:async()=>{},refreshStock:async()=>{},refreshSales:async()=>{},stockMessage:()=>{},
  selectedSourceOrder:()=>({account_id:'account',order_id:'ORIGINAL'}),
});
function include(from,to){vm.runInContext(source.slice(source.indexOf(from),source.indexOf(to)),ctx);}
include('function lineNames(', 'function documentSearchTerms(');
include('function documentLines(', 'function openReceiptPicker(');
include('function remainingLines(', 'function prepareShipment(');
const digital={line_type:'non_stock',item_name:'數位券',product_id:null,quantity:5,components:[]};
assert.match(ctx.lineNames([digital]),/數位券（非庫存） × 5/);
assert.equal(JSON.stringify(ctx.remainingLines({status:'active',lines:[digital]})),'[]');
assert.equal(JSON.stringify(ctx.remainingLines({status:'active',lines:[digital,{product_id:'p',quantity:2}]})),JSON.stringify([{product_id:'p',quantity:2}]));
field('order-lines').children=[{dataset:{lineType:'non_stock'},querySelector:sel=>({value:({'[data-field=item_name]':'數位券','[data-field=quantity]':'5','[data-field=price]':'0'})[sel]})}];
for(const [id,value] of Object.entries({'order-account':'account','order-number':'MANUAL','order-time':'2026-01-01T00:00:00+08:00','order-customer':'','order-kol':'','order-utm':'bobby017','order-total':'960','shipment-order-number':'DISPATCH'}))field(id).value=value;
for(const id of ['order-form','shipment-form'])vm.runInContext(source.split('\n').find(line=>line.startsWith(`$('${id}').addEventListener`)),ctx);
(async()=>{
  const event={preventDefault(){},submitter:{}};
  handlers['order-form'](event);await pending;
  assert.equal(requests[0].body.utm_medium,'bobby017');
  assert.equal(requests[0].body.order_total,'960');
  assert.equal(requests[0].body.lines[0].line_type,'non_stock');
  assert.equal(requests[0].body.lines[0].item_name,'數位券');
  assert.equal(requests[0].body.lines[0].quantity,5);
  assert(!('product_id' in requests[0].body.lines[0]));
  handlers['shipment-form'](event);await pending;
  assert.equal(requests[1].body.account_id,'account');
  assert.equal(requests[1].body.order_id,'ORIGINAL');
  assert.equal(requests[1].body.number,'DISPATCH');
  assert(!('entry_number' in requests[1].body));
  console.log('Non-stock display, fulfillment exclusion and form payload checks passed (not browser acceptance)');
})().catch(e=>{console.error(e);process.exitCode=1;});
