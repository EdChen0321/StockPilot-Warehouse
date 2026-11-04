const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/stock.js','utf8');
const fields={};
const field=id=>fields[id]||(fields[id]={dataset:{},value:'',disabled:true,hidden:false,replaceChildren(...items){this.items=items;},querySelector(){return field(id+'-heading');}});
const parent={id:'main',number:'MAIN',group_id:'g',channel:'門市'};
const ctx=vm.createContext({$:field,stockData:{shipments:[parent],groups:[{id:'g',manual:0}]},stockFacts:x=>x,shipmentState:s=>s,stockStates:{draft:'準備中',shipped:'完成',cancelled:'取消'}});
vm.runInContext(source.slice(source.indexOf('function prepareSubShipment('),source.indexOf('function showOrder(')),ctx);
for(const state of ['draft','allocated','shipped','cancelled']){
 parent.state=state;field('shipment-save').disabled=true;
 ctx.prepareSubShipment('main');
 assert.equal(field('shipment-save').disabled,false);
 assert.equal(field('shipment-order').dataset.parentId,'main');
 assert.equal(field('shipment-order-number').readOnly,false);
 assert.equal(field('shipment-order-number').value,'');
 assert.equal(field('shipment-empty').hidden,true);
}
assert.doesNotMatch(source,/sub\.disabled=.*remainingLines/);
console.log('Child form remains enabled across parent states and exhausted source-order quantities (DOM adapter).');
