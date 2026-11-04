
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/stock.js','utf8');
function node(tag,text=''){return {tag,text,children:[],dataset:{},classList:{toggle(){}},append(...items){this.children.push(...items);},replaceChildren(...items){this.children=[...items];},focus(){}};}
const unit={id:'u1',product_id:'p',receipt_id:'r',cost_cents:1234,location:'customer',quality:'good',packaging:'good'};
const shipment={id:'s1',state:'shipped',number:'S1',shipped_at:'2026-10-29T00:00:00Z',reopen_count:0};
const fields={'shipment-rows':node('tbody'),'shipment-detail':node('section')},calls=[];let actions;
const data={units:[unit],receipts:[{id:'r',source:'原批次'}],groups:[],shipments:[shipment],events:[{id:'ship1',kind:'ship',unit_id:'u1',shipment_id:'s1',details:{cost_cents:1234}}]};
const context=vm.createContext({node,shipmentScanQueue:Promise.resolve(),stockData:data,boot:{products:[{id:'p',name:'杯',sku:'SKU'}]},$:id=>fields[id],document:{activeElement:null},selectedShipment:null,orderData:[],
 stockDocumentHeader:panel=>{panel.replaceChildren();actions=node('div');panel.append(actions);return actions;},stockButton:(_area,label,fn)=>Object.assign(node('button',label),{onclick:fn}),shipmentState:s=>s,stockDisplayState:u=>u.location==='warehouse'?'準備中':'出庫',centsText:String,formatAmount:x=>x,dateTime:x=>x,pageLink:()=>node('a'),emptyRow(){},operation:()=> 'op',request:async(path,body)=>{calls.push({path,body});},refreshStock:async()=>{},stockMessage(){}});
vm.runInContext(fs.readFileSync('web/status.js','utf8'),context);
vm.runInContext(source.slice(source.indexOf('function receiptCosts('),source.indexOf('function showReceipt(')),context);
function all(el){return [el,...el.children.flatMap(all)];}
(async()=>{
 shipment.first_completed_at='2026-10-28T01:00:00Z';shipment.cancelled_at='2026-10-29T02:00:00Z';
 context.showShipment('s1');assert.ok(all(fields['shipment-detail']).some(n=>n.text==='首次完成時間'));assert.ok(all(fields['shipment-detail']).some(n=>n.text==='最近取消時間'));assert.ok(all(fields['shipment-detail']).some(n=>n.text===shipment.first_completed_at));assert.ok(all(fields['shipment-detail']).some(n=>n.text===shipment.cancelled_at));
 context.showShipment('s1');const reopen=actions.children.find(n=>n.text==='回到準備中');assert.ok(reopen);assert.ok(!actions.children.some(n=>n.text==='取消'));await reopen.onclick();assert.equal(calls.at(-1).path,'/api/shipments/s1/reopen');assert.equal(calls.at(-1).body.expected_shipped_at,shipment.shipped_at);assert.equal(calls.at(-1).body.expected_reopen_count,0);
 shipment.state='allocated';shipment.shipped_at=null;shipment.reopen_count=1;unit.location='warehouse';unit.shipment_id='s1';data.events.unshift({id:'reopen1',kind:'ship_reopen',unit_id:'u1',shipment_id:'s1',details:{ship_event_id:'ship1'}});
 assert.match(context.shipmentTotals(shipment),/本單 1 件.*12\.34/);context.showShipment('s1');assert.ok(all(fields['shipment-detail']).some(n=>n.text==='回到準備中'));assert.ok(all(fields['shipment-detail']).some(n=>n.text==='完成'),'original ship stays history');
 await actions.children.find(n=>n.text==='完成').onclick();assert.equal(calls.at(-1).body.expected_reopen_count,1);await actions.children.find(n=>n.text==='取消').onclick();assert.equal(calls.at(-1).body.expected_reopen_count,1);
 shipment.state='staged';context.showShipment('s1');await actions.children.find(n=>n.text==='取消').onclick();assert.equal(calls.at(-1).path,'/api/shipments/s1/cancel-staged');assert.equal(calls.at(-1).body.expected_reopen_count,1);
 shipment.state='shipped';unit.location='customer';delete unit.shipment_id;data.events.unshift({id:'ship2',kind:'ship',unit_id:'u1',shipment_id:'s1',details:{cost_cents:1234}});assert.match(context.shipmentTotals(shipment),/原出貨 1 件.*未退貨 1 件.*12\.34/);context.showShipment('s1');const unitRows=all(fields['shipment-detail']).filter(n=>n.tag==='tr'&&n.children.some(c=>c.text==='u1')&&n.children.some(c=>c.text==='×1'));assert.equal(unitRows.length,1);
 data.events.unshift({id:'reopen2',kind:'ship_reopen',unit_id:'u1',shipment_id:'s1',details:{ship_event_id:'ship2'}},{id:'other',kind:'ship',unit_id:'u1',shipment_id:'s2',details:{}});context.showShipment('s1');assert.ok(!all(fields['shipment-detail']).some(n=>n.tag==='button'&&n.text==='退貨入庫'),'reversed ships cannot grant return actions in old shipment');

 shipment.state='cancelled';context.showShipment('s1');assert.deepEqual(actions.children.map(n=>n.text),['回到準備中']);await actions.children[0].onclick();assert.equal(calls.at(-1).body.expected_state,'cancelled');assert.ok(!('expected_shipped_at' in calls.at(-1).body));
 const previousUnit=JSON.stringify(unit);unit.shipment_id='s2';unit.location='customer';const otherUnit=JSON.stringify(unit);
 shipment.state='draft';shipment.reopen_count=2;data.events.unshift({id:'cycle2',kind:'cancel_reopen',shipment_id:'s1',unit_id:null,details:{before_lines:[{product_id:'p',quantity:1}]}});context.showShipment('s1');
 let currentTable=all(fields['shipment-detail']).find(n=>n.tag==='table'&&n.children.some(c=>c.tag==='tbody')&&all(n).some(c=>c.text==='商品／SKU'));assert.ok(!all(currentTable).some(n=>n.text==='u1'));assert.match(context.shipmentTotals(shipment),/本單 0 件/);assert.equal(JSON.stringify(unit),otherUnit);assert.ok(all(fields['shipment-detail']).some(n=>n.text==='先前已釋放紀錄'));assert.ok(all(fields['shipment-detail']).some(n=>n.text==='u1'));
 const newUnit={...unit,id:'u2',location:'warehouse',shipment_id:'s1'};data.units.push(newUnit);data.events.unshift({id:'alloc2',kind:'allocate',shipment_id:'s1',unit_id:'u2',details:{}});shipment.state='allocated';context.showShipment('s1');
 currentTable=all(fields['shipment-detail']).find(n=>n.tag==='table'&&all(n).some(c=>c.text==='商品／SKU'));assert.ok(all(currentTable).some(n=>n.text==='u2'));assert.ok(!all(currentTable).some(n=>n.text==='u1'));
 await all(currentTable).find(n=>n.tag==='button'&&n.text==='取消').onclick();assert.equal(calls.at(-1).body.expected_reopen_count,2);
 const scan=all(fields['shipment-detail']).find(n=>n.id==='shipment-scan');scan.value='BC';scan.onkeydown({key:'Enter',preventDefault(){}});await context.shipmentScanQueue;assert.equal(calls.at(-1).body.expected_reopen_count,2);
 assert.match(fs.readFileSync('web/stock-extras.js','utf8'),/ship_reopen:'回到準備中'/);
 console.log('Shipment reopen actions, generation guards, effective costs, history and unit membership passed (DOM adapter only)');
})().catch(e=>{console.error(e);process.exitCode=1;});
