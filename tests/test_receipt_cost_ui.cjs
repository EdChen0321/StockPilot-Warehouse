// Cost presentation logic; DOM/browser acceptance is separate.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/stock.js','utf8');
function extract(name){const start=source.indexOf('function '+name+'(');assert(start>=0,`${name} exists`);const next=source.indexOf('\nfunction ',start+1);return source.slice(start,next<0?source.length:next);}
const ctx=vm.createContext({formatAmount:v=>v,centsText:v=>(v/100).toFixed(2)});
for(const name of ['receiptCosts','costDisplay','stockCostSummary'])vm.runInContext(extract(name),ctx);
const complete={lines:[{quantity:10,unit_price_cents:10000}],freight_cents:0,tax_cents:5000,cost_status:'complete'};
assert.equal(ctx.receiptCosts(complete).total,105000n);
assert.equal(ctx.receiptCosts({...complete,freight_cents:null,cost_status:'pending'}).total,null);
assert.equal(ctx.receiptCosts({...complete,lines:[{quantity:2,unit_price_cents:null}]}).goods,null);
assert.equal(ctx.receiptCosts({lines:[{quantity:1,unit_price_cents:0}],freight_cents:0,tax_cents:0}).total,0n);
assert.equal(ctx.costDisplay(null),'待填費用');
assert.equal(ctx.costDisplay(0),'0.00');
vm.runInContext(extract('shipmentAuditState'),ctx);
assert.equal(ctx.shipmentAuditState({kind:'cost_revision',details:{before_cost_cents:null,after_cost_cents:0,cost_version:2}}),'成本：待填費用 → 0.00（版本 2）');
assert.equal(ctx.shipmentAuditState({kind:'cost_revision',details:{before_cost_cents:10000,after_cost_cents:10500,cost_version:1}}),'成本：100.00 → 105.00（版本 1）');
assert.match(ctx.stockCostSummary([{cost_cents:10500},{cost_cents:null}]),/已知成本 105.00.*待填費用 1 件/);
ctx.stockData={units:[{id:'u',cost_cents:10500}]};ctx.shipmentCycleEvents=()=>({current:[{id:'e'}]});
ctx.shipmentEffectiveShips=()=>[{id:'e',shipment_id:'s',unit_id:'u',details:{cost_cents:10000}}];
vm.runInContext(extract('shipmentTotals'),ctx);
assert.match(ctx.shipmentTotals({id:'s'}),/105.00/);
ctx.stockData.units[0].cost_cents=null;assert.match(ctx.shipmentTotals({id:'s'}),/待填費用 1 件/);
console.log('Cost UI: pending versus zero, latest shipment costs and unknown totals passed.');

const locations=fs.readFileSync('web/locations.js','utf8'),prompts=[];let accept=false;
ctx.confirm=message=>{prompts.push(message);return accept;};
vm.runInContext(locations.slice(locations.indexOf('function confirmPutawayCost('),locations.indexOf('function renderPutaway(')),ctx);
assert.equal(ctx.confirmPutawayCost(complete),true);assert.equal(prompts.length,0);
assert.equal(ctx.confirmPutawayCost({...complete,cost_status:'pending'}),false);assert.match(prompts.at(-1),/尚未填寫費用/);
accept=true;assert.equal(ctx.confirmPutawayCost({lines:[{quantity:1,unit_price_cents:0}],freight_cents:0,tax_cents:0}),true);assert.match(prompts.at(-1),/0 元/);

class Element{
 constructor(tag,text=''){this.tag=tag;this.textContent=String(text??'');this.children=[];this.events={};this.value='';this.disabled=false;}
 append(...nodes){this.children.push(...nodes);}setAttribute(k,v){this[k]=v;}addEventListener(k,v){this.events[k]=v;}
 focus(){}showModal(){}close(){this.events.close?.();}remove(){}
}
const walk=e=>[e,...e.children.flatMap(walk)],body=new Element('body'),calls=[];let failed=true,refreshes=0,op=0;
Object.assign(ctx,{node:(tag,text)=>new Element(tag,text),document:{body,activeElement:null},window:{addEventListener(){},removeEventListener(){}},
 boot:{products:[{id:'p',name:'禮盒',sku:'GIFT'}]},Option:function(t,v){return Object.assign(new Element('option',t),{value:v});},operation:()=>`op${++op}`,
 request:async(url,payload)=>{calls.push({url,payload});if(failed){failed=false;throw Error('重試');}},refreshStock:async()=>refreshes++,stockMessage(){}});
vm.runInContext(extract('editReceiptCost'),ctx);
(async()=>{
 ctx.editReceiptCost({id:'r',number:'IN',state:'confirmed',cost_version:4,lines:[{product_id:'p',quantity:10,unit_price_cents:10000}],freight_cents:0,tax_cents:0,allocation:'quantity'});
 const dialog=body.children.at(-1),form=walk(dialog).find(e=>e.tag==='form'),inputs=walk(dialog).filter(e=>e.tag==='input'),reason=walk(dialog).find(e=>e.tag==='textarea');
 assert.equal(inputs.length,3,'only unit price, freight and tax inputs; quantity cannot change');assert.equal(reason.required,true);
 inputs[0].value='';inputs[1].value='0';inputs[2].value='50';reason.value='補稅';
 await form.events.submit({preventDefault(){}});assert.match(walk(dialog).find(e=>e.role==='alert').textContent,/重試/);
 await form.events.submit({preventDefault(){}});
 assert.equal(calls.length,2);assert.equal(calls[0].payload.operation_id,calls[1].payload.operation_id,'retry retains operation ID');
 assert.equal(calls[1].url,'/api/receipts/r/cost');assert.equal(calls[1].payload.expected_cost_version,4);assert.equal(calls[1].payload.lines[0].unit_price,'');assert.equal(calls[1].payload.freight,'0');assert.equal(calls[1].payload.tax,'50');assert.equal(calls[1].payload.reason,'補稅');assert.equal(refreshes,1);
 console.log('Cost UI form payload, retry, immutable quantity and zero/pending putaway confirmations passed (DOM adapter).');
})().catch(error=>{console.error(error);process.exitCode=1;});
