// DOM adapter verifies forms/payloads/routes, not real browser rendering.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag,text=''){this.tag=tag;this.textContent=String(text??'');this.children=[];this.value='';this.events={};this.isConnected=true;this.className='';this.dataset={};this.classList={add:v=>this.className+=' '+v};}
 append(...xs){for(const x of xs){x.parent=this;this.children.push(x);}}
 replaceChildren(...xs){this.children=[];this.append(...xs);}
 insertBefore(x,ref){x.parent=this;this.children.splice(this.children.indexOf(ref),0,x);}
 setAttribute(k,v){this[k]=v;}
 addEventListener(k,v){this.events[k]=v;}
 remove(){this.isConnected=false;this.parent.children=this.parent.children.filter(x=>x!==this);}
}
const node=(tag,text,cls)=>Object.assign(new Element(tag,text),{className:cls||''});
const all=e=>[e,...e.children.flatMap(all)],text=e=>all(e).map(x=>x.textContent).join(' ');
const field=(root,label)=>all(root).find(x=>x.tag==='label'&&x.textContent===label)?.children[0];
const ids={},get=id=>ids[id]??=node('div'),calls=[],routes=[],notices=[],pending=[];
const purchase={id:'po',number:'PO-100',supplier_id:'s',supplier_name:'合成工廠',state:'partial',version:3,expected_date:'2026-11-20',comment:'測試',ordered_at:'2026-10-30',
 lines:[{product_id:'p',sku:'SKU',name:'成品',quantity:100,received:40,pending:60,cancelled:0,quoted_price_cents:null}],
 receipts:[{id:'r',number:'IN-40',confirmed_at:'2026-10-30',freight_cents:100,lines:[{quantity:40}]}],history:[]};
const data={purchases:[purchase],products:[{id:'p',sku:'SKU',name:'成品',active:true}],suppliers:[{id:'s',name:'合成工廠',active:1}]};
let op=0,fail=false,stockRefreshes=0;
const location={hash:'#goods-purchases'},context=vm.createContext({node,$:get,Option:function(t,v){return Object.assign(node('option',t),{value:v});},
 document:{createTextNode:t=>node('text',t)},location,window:{},confirm:()=>true,refreshStock:async()=>{stockRefreshes++;},
 request:async(path,body)=>{calls.push({path,body});if(body&&fail){fail=false;throw new Error('network failed');}return body?purchase:data;},
 stockButton:(area,label,work)=>{const b=node('button',label);b.type='button';b.onclick=work;return b;},
 stockAction:(area,button,work)=>{const p=work().catch(e=>notices.push(e.message));pending.push(p);return p;},stockMessage:(area,message)=>{notices.push(message);return {scrollIntoView(){}};},
 stockFacts:entries=>{const dl=node('dl');for(const [k,v] of entries)dl.append(node('dt',k),node('dd',v));return dl;},
 centsText:c=>`${Math.floor(c/100)}.${String(c%100).padStart(2,'0')}`,dateTime:v=>v,operation:()=>`op-${++op}`,navigatePage:path=>routes.push(path)});
vm.runInContext(fs.readFileSync('web/status.js','utf8'),context);
// Reuse existing table helper without booting the material workspace.
const materials=fs.readFileSync('web/materials.js','utf8');
vm.runInContext(materials.slice(materials.indexOf('function materialTable('),materials.indexOf('function materialLineTable(')),context);
vm.runInContext(fs.readFileSync('web/goods-purchases.js','utf8'),context);
async function render(parts){location.hash='#'+parts.join('/');await context.renderGoodsPurchases(parts);return get('goods-purchases');}
async function submit(root){const form=all(root).find(x=>x.tag==='form');form.events.submit({preventDefault(){}});await Promise.all(pending.splice(0));}
(async()=>{
 let root=await render(['goods-purchases']);assert.match(text(root),/PO-100/);
 field(root,'搜尋單號、商品或 SKU').value='missing';await submit(root);assert.match(text(root),/尚無符合條件/);
 root=await render(['goods-purchases','po']);assert.match(text(root),/未定價/);assert.match(text(root),/60/);
 assert.ok(all(root).some(x=>x.href==='#stock/r'));assert.ok(all(root).some(x=>x.href==='#putaway/r'));
 assert.ok(all(root).some(x=>x.href==='#goods-purchases/po/receive'));
 root=await render(['goods-purchases','po','receive']);assert.match(text(root),/最多 60 件/);
 field(root,'本次入庫單號').value='IN-20';field(root,'本批實際運費（無運費請填 0）').value='3.01';
 const quantity=field(root,'本次實收數量');assert.equal(quantity.max,60);assert.equal(quantity.value,0);
 await submit(root);assert.match(notices.at(-1),/至少一項/);
 quantity.value='20';assert.equal(field(root,'本批實際單價').required,false);assert.equal(field(root,'本批實際運費（無運費請填 0）').required,false);assert.equal(field(root,'本批稅費（可待補）').value,'0.00');field(root,'本批實際單價').value='12.34';
 fail=true;await submit(root);const failed=calls.at(-1);await submit(root);const retry=calls.at(-1);
 assert.equal(retry.body.operation_id,failed.body.operation_id);assert.equal(retry.body.expected_version,3);
 assert.equal(retry.body.lines[0].quantity,20);assert.equal(retry.body.lines[0].unit_price,'12.34');assert.equal(retry.body.freight,'3.01');
 assert.equal(routes.at(-1),'goods-purchases/po');
 assert.equal(stockRefreshes,1,'new receipt must be available before opening stock detail');
 root=await render(['goods-purchases','po','cancel']);assert.match(text(root),/取消 60 件.*已收 40 件/);
 field(root,'取消原因').value='停供';await submit(root);assert.equal(calls.at(-1).body.reason,'停供');
 root=await render(['goods-purchases','new']);field(root,'採購單號').value='NEW';field(root,'供應商').value='s';field(root,'商品 SKU').value='p';
 field(root,'訂購數量').value='100';await submit(root);assert.equal(calls.at(-1).body.lines[0].quoted_price,'');
 purchase.state='received';root=await render(['goods-purchases','po']);assert.ok(!all(root).some(x=>x.href?.endsWith('/receive')));
 root=await render(['goods-purchases','po','receive']);assert.match(notices.at(-1),/不能收貨/);
 const html=fs.readFileSync('web/index.html','utf8');assert.match(html,/href="#goods-purchases"/);assert.match(html,/src="\/goods-purchases\.js(?:\?v=[^"\s]+)?"/);
 assert.equal(require('../web/operations-ui.js').operationsView('goods-purchases').title,'商品採購／叫貨');
 console.log('Goods procurement list, filters, receipt links, partial receive, form payloads, retry, cancellation and closed guards passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
