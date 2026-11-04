const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag,text='',className=''){Object.assign(this,{tag,textContent:text||'',className,children:[],dataset:{},classList:{add(){},toggle(){}}});}
 append(...c){this.children.push(...c);}replaceChildren(...c){this.children=c;}addEventListener(k,v){this[k]=v;}setAttribute(k,v){this[k]=v;}
}
const all=e=>[e,...e.children.flatMap(all)],text=e=>all(e).map(x=>x.textContent).join(' '),panel=new Element('div');
const order={account_id:'a',order_id:'WB-202610-0005',status:'active',amount_cents:10000,order_total_cents:null,lines:[{product_id:'p',quantity:2,amount_cents:10000}]};
const context=vm.createContext({node:(...a)=>new Element(...a),$:id=>id==='order-detail'?panel:new Element('div'),selectedOrder:null,orderData:[order],orderKey:()=> 'key',statusBadge:t=>new Element('span',t),lineNames:()=> '水壺 × 2',formatAmount:v=>v,centsText:v=>(v/100).toFixed(2),dateTime:v=>v||'—',productName:()=> '水壺',boot:{accounts:[{id:'a',name:'我的商店'}],products:[{id:'p',sku:'BOTTLE'}]},stockData:{order_work:[],additions:[],groups:[],shipments:[]},
 materialTable:(h,r)=>new Element('table',JSON.stringify({h,r})),stockFacts:entries=>new Element('dl',JSON.stringify(entries)),stockButton:(_a,t,fn)=>Object.assign(new Element('button',t),{click:fn}),remainingLines:()=>[{quantity:2}],newLine(){},confirm:()=>false});
const source=fs.readFileSync('web/stock.js','utf8');vm.runInContext(source.slice(source.indexOf('function showOrder('),source.indexOf('function stockDocumentHeader(')),context);
context.showOrder('key');
assert.doesNotMatch(text(panel),/重新開啟|重開原因/);
assert.doesNotMatch(text(panel),/追加出貨|追加原因|追加編號|保存新增/);
assert.match(text(panel),/未提供/,'Null total is not zero');
assert.equal(all(panel).filter(e=>e.tag==='table').length,1);
assert.equal(all(panel).filter(e=>e.tag==='form'||e.tag==='details').length,0);
context.stockData.order_work=[{account_id:'a',order_id:order.order_id,state:'open',version:1}];context.showOrder('key');
assert.equal(all(panel).filter(e=>e.tag==='form'||e.tag==='details').length,0,'Previously enabled append has no editor');
assert.deepEqual(all(panel).filter(e=>e.tag==='button').map(e=>e.textContent),['建立出貨單','取消這張訂單']);
order.status='cancelled';context.showOrder('key');assert.equal(all(panel).filter(e=>e.tag==='button').length,0);
assert.match(text(panel),/已取消/);
console.log('Order detail: no append editor, only shipping/cancellation actions, null total, cancelled guards passed.');
