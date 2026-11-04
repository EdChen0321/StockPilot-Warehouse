const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/stock.js', 'utf8');
function node(tag, text='') { return {tag, text, children:[], append(...items){this.children.push(...items);}}; }
const unit={id:'unit',product_id:'p',receipt_id:'r',cost_cents:1234,location:'customer',quality:'good',packaging:'good'};
const context=vm.createContext({node,stockData:{units:[unit],receipts:[{id:'r',source:'原批次'}],events:[
  {id:'newship',kind:'ship',unit_id:'unit',shipment_id:'new',details:{}},
  {kind:'return',unit_id:'unit',shipment_id:'old',details:{ship_event_id:'oldship',reason:'原單退貨'}},
  {id:'oldship',kind:'ship',unit_id:'unit',shipment_id:'old',details:{}},
]},boot:{products:[{id:'p',name:'測試',sku:'SKU',barcode:'BC'}]},pageLink:()=>node('a'),stockButton:()=>node('button'),
stockDisplayState:()=>'',centsText:x=>String(x),formatAmount:x=>x,emptyRow:()=>{}});
vm.runInContext(fs.readFileSync('web/status.js','utf8'),context);
vm.runInContext(source.slice(source.indexOf('function receiptCosts('),source.indexOf('function showShipment(')),context);
const panel=node('section');context.renderShipmentItems(panel,{id:'old',state:'shipped'},node('div'));
const text=JSON.stringify(panel);
assert.match(text,/退貨入庫/,'original return must remain visible after another shipment');
assert.match(text,/原單退貨/,'return reason must stay in the original order');
assert.match(text,/原批次/);
assert.match(text,/1234/);
assert.match(text,/#quality\/unit/,'each shipment item links to its exact unit detail');
assert.match(text,/查看品項/);
assert.match(context.shipmentTotals({id:'old',state:'shipped'}),/原出貨 1 件.*已退貨 1 件.*未退貨 0 件/);
console.log('Shipment item history, source, cost and inline handling passed');
unit.location='warehouse';unit.return_hold=true;unit.return_ship_event_id='oldship';
context.stockData.events=context.stockData.events.filter(e=>e.id!=='newship');
context.stockButton=(area,label,handler)=>({...node('button',label),handler});
const returnedPanel=node('section');context.renderShipmentItems(returnedPanel,{id:'old',state:'shipped'},node('div'));
assert.match(JSON.stringify(returnedPanel),/轉回入庫/);
assert.doesNotMatch(JSON.stringify(returnedPanel),/待驗收|驗收放行/);
unit.return_ship_event_id='different-cycle';
const stalePanel=node('section');context.renderShipmentItems(stalePanel,{id:'old',state:'shipped'},node('div'));
assert.doesNotMatch(JSON.stringify(stalePanel),/轉回入庫/,'old shipment must not restock a newer return cycle');
