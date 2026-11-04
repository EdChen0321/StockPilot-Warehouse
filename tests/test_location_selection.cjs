// Interaction logic with a small DOM adapter; not a real browser test.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag,text=''){this.tag=tag;this.textContent=String(text);this.children=[];this.value='';this.classList={add(){},remove(){}};}
 append(...items){for(const item of items){if(item.parentNode)item.parentNode.children=item.parentNode.children.filter(c=>c!==item);item.parentNode=this;item.parentElement=this;this.children.push(item);}}
 replaceChildren(...items){this.children=[];this.append(...items);}
 setAttribute(k,v){this[k]=v;}
}
const walk=e=>[e,...e.children.flatMap(walk)],root=new Element('section');
const units=Array.from({length:1200},(_,i)=>({id:'u'+String(i).padStart(4,'0'),product_id:'p',place_id:'a',location:'warehouse',quality:'good',packaging:'good',place_eligible:true,fifo_at:'2026-10-01'}));
units.push({...units[0],id:'box',place_id:'b',packaging:'damaged'},{...units[0],id:'unassigned',place_id:null,place_eligible:false},{...units[0],id:'shipped',location:'customer'});
const ctx=vm.createContext({node:(tag,text)=>new Element(tag,text??''),Option:function(text,value){return Object.assign(new Element('option',text),{value});},location:{hash:''},stockData:{products:[{id:'p',name:'帆布袋',sku:'BAG',barcode:'123'}],units},stockDisplayState:u=>u.quality,emptyRow(){}});
vm.runInContext(fs.readFileSync('web/status.js','utf8'),ctx);
vm.runInContext(fs.readFileSync('web/locations.js','utf8'),ctx);
const selected=new Set();let changes=0;ctx.renderTransferPicker(root,units.slice(0,1200),selected,()=>changes++);
const [product,state]=walk(root).filter(e=>e.tag==='select'),quantity=walk(root).find(e=>e.type==='number');
const button=name=>walk(root).find(e=>e.tag==='button'&&e.textContent.startsWith(name));
product.value='p';product.onchange();quantity.value='1001';button('按數量加入').onclick();assert.equal(selected.size,1001);
assert.equal(walk(root).filter(e=>e.type==='checkbox').length,50);
button('下一頁').onclick();assert(walk(root).filter(e=>e.type==='checkbox').every(e=>e.checked));
const check=walk(root).find(e=>e.type==='checkbox');check.checked=false;check.onchange();assert.equal(selected.size,1000);
quantity.value='201';button('按數量加入').onclick();assert.equal(selected.size,1000,'over-request must not partially select');
button('全選符合條件').onclick();assert.equal(selected.size,1200,'all includes off-page units');
button('清除全部').onclick();assert.equal(selected.size,0);
quantity.value='1000';button('按數量加入').onclick();assert.equal(selected.size,1000);assert.equal(quantity.value,'');
assert(changes>=5);
for(const query of ['帆布','bag','123']){const rows=ctx.locationProductRows(ctx.stockData,query);assert.equal(rows.length,3);assert.equal(rows.reduce((n,r)=>n+r.count,0),1202);assert.equal(rows.find(r=>r.place_id===null).available,0);assert.equal(rows.find(r=>r.place_id==='b').packaging,'damaged');}
assert.equal(ctx.locationProductRows(ctx.stockData,'unknown').length,0);
assert.equal(ctx.locationProductRows(ctx.stockData,'u0007')[0].count,1);
console.log('1000-piece selection, cross-page all, overflow, clear, and location search passed (DOM adapter only)');
