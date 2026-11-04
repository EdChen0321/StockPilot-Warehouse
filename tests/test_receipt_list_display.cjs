
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/stock.js','utf8');
class Element{constructor(tag,text=''){this.tag=tag;this.textContent=String(text);this.children=[];this.dataset={};this.value='';}append(...items){this.children.push(...items);}replaceChildren(...items){this.children=[...items];}}
const text=el=>[el.textContent,...el.children.map(text)].join(' '),fields={},routes=[];
const products=[{id:'p1',name:'同名杯',sku:'CUP-A'},{id:'p2',name:'同名杯',sku:'CUP-B'},{id:'p3',name:'組合×原名*保留',sku:'SET-X'}];
const receipt={id:'r/中文',number:'RCPT',state:'draft',lines:[{product_id:'p1',quantity:2},{product_id:'p2',quantity:3},{product_id:'p3',quantity:4},{product_id:'missing-id',quantity:1}]};
const units=[];
const context=vm.createContext({boot:{products,accounts:[]},$:id=>fields[id]??=new Element('div'),node:(tag,t)=>new Element(tag,t),productName:id=>products.find(p=>p.id===id)?.name||id,
 request:async path=>path==='/api/stock?summary=1'?{receipts:[receipt],units,shipments:[],groups:[]}:{orders:[]},loadedStockHistory:new Set(),loadStockHistory:async()=>{},pageItems:(_id,items)=>items,receiptPutaway:require('../web/format.js').receiptPutaway,options(){},renderInventory(){},documentMatches:()=>true,stockStates:{draft:'草稿',confirmed:'已入庫'},dateTime:v=>v||'—',refreshOrderList:async()=>{},emptyRow(){},stockButton:(_area,label,fn)=>Object.assign(new Element('button',label),{onclick:fn}),navigatePage:path=>routes.push(path),window:{dispatchEvent(){}},Event:class{},location:{hash:'#stock'}});
vm.runInContext(fs.readFileSync('web/status.js','utf8'),context);
vm.runInContext('let stockSequence=0,stockData,orderData,selectedReceipt=null,selectedShipment=null,selectedOrder=null;',context);
vm.runInContext(source.slice(source.indexOf('function receiptCosts('),source.indexOf('function costDisplay(')),context);
vm.runInContext(source.slice(source.indexOf('function lineNames('),source.indexOf('for(const [id,area]')),context);
vm.runInContext(source.slice(source.indexOf('async function refreshStock()'),source.indexOf('let orderSearchSequence=')),context);
(async()=>{await context.refreshStock();const row=fields['receipt-rows'].children[0],cell=row.children[2];
 assert.doesNotMatch(text(row),/CUP-A|CUP-B|missing-id/);assert.equal(row.children.length,8);assert.equal(cell.textContent,'10');await row.children[7].children[0].onclick();assert.equal(routes.at(-1),'stock/'+encodeURIComponent(receipt.id));
 assert.match(context.lineNames(receipt.lines),/同名杯 × 2/,'shared order summary remains unchanged');
 const html=fs.readFileSync('web/index.html','utf8');assert.match(html,/<th>進貨來源<\/th><th>總件數<\/th>[\s\S]*?id="receipt-rows"/);
 assert.match(text(row.children[4]),/尚未入庫/);assert.equal(row.children[7].children.length,1);
 receipt.state='confirmed';units.push({receipt_id:receipt.id,location:'warehouse',place_id:null},{receipt_id:receipt.id,location:'warehouse',place_id:null});
 await context.refreshStock();let updated=fields['receipt-rows'].children[0];assert.match(text(updated.children[4]),/未配櫃/);assert.equal(updated.children[7].children[1].textContent,'配櫃');
 units[0].place_id='shelf';await context.refreshStock();updated=fields['receipt-rows'].children[0];assert.match(text(updated.children[4]),/部分配櫃/);
 units[1].place_id='shelf';await context.refreshStock();updated=fields['receipt-rows'].children[0];assert.match(text(updated.children[4]),/已配櫃/);assert.equal(updated.children[7].children.length,1);
 fields['receipt-filter-putaway'].value='pending';await context.refreshStock();assert.equal(fields['receipt-rows'].children.length,0);
 fields['receipt-filter-putaway'].value='complete';await context.refreshStock();assert.equal(fields['receipt-rows'].children.length,1);
 console.log('Receipt list identifiers, putaway states, conditional action and filters passed (DOM adapter only)');
})().catch(e=>{console.error(e);process.exitCode=1;});
