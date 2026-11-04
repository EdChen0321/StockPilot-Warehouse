// Small DOM adapter for rendering/interaction logic; this is NOT browser validation.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
class Element {
 constructor(tag,text=''){this.tag=tag;this.textContent=String(text);this.children=[];this.value='';}
 append(...children){for(const c of children){c.parent=this;this.children.push(c);}}
 prepend(...children){for(const c of children)c.parent=this;this.children.unshift(...children);}
 replaceChildren(...children){this.children=[];this.append(...children);}
 setAttribute(k,v){this[k]=v;}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this);}
 scrollIntoView(){}
}
function all(el){return [el,...el.children.flatMap(all)];}
function text(el){return all(el).map(n=>n.textContent).join(' ');}
const root=new Element('section');const routes=[];const events={};
const k={id:'k1',name:'合成小晴',platforms:['IG'],utms:['sunny'],started_at:'2026-01-01T00:00:59+08:00',version:1};
const summary={order_count:1,total:'960.00',average:'960.00',known_total:'960.00',missing_total_count:0};
const order={account_id:'a1',order_id:'ORDER/中文',status:'active',product_quantity:1,ordered_at:'2026-01-02T00:00:00+08:00',total:'960.00',kol_name:k.name,utm_medium:'sunny',reason:'UTM精確匹配',rule_version:1,revision:1,batch_id:'batch1',lines:[{product_id:'p1',name:'合成杯',sku:'CUP',quantity:1,amount:'1000.00'}]};
let calls=[],fail=false,slow,many=false;
const context=vm.createContext({Node:Element,URLSearchParams,Date,JSON,crypto:require('node:crypto').webcrypto,
 node:(tag,t,c)=>Object.assign(new Element(tag,t===undefined?'':t),{className:c||''}),
 $:()=>root,formatAmount:v=>v,boot:{accounts:[{id:'a1',name:'合成店'}]},
 Option:function(t,v){return Object.assign(new Element('option',t),{value:v});},
 FormData:class{constructor(form){this.values=all(form).filter(n=>n.name&&n.type!=='checkbox').map(n=>[n.name,n.value]);}[Symbol.iterator](){return this.values[Symbol.iterator]();}},
 request:async(path,body)=>{calls.push({path,body});if(fail)throw new Error('測試讀取失敗');if(slow)await slow;
   return path==='/api/kols'?{kols:[k]}:{summary,kol:k,status_counts:{all:1,active:1,cancelled:0},utm_counts:{sunny:1},product:{id:'p1',name:'合成杯',sku:'CUP',kind:'product'},orders:[order],products:[{id:'p1',account_id:'a1',kind:'product',name:'合成杯',sku:'CUP',quantity:1,order_count:1,amount:'1000.00'}],groups:many?Array.from({length:25},(_,i)=>({...k,...summary,id:`k${i}`,name:`合成夥伴${i}`,order_count:i})):[{...k,...summary}],generated_at:'2026-01-02T00:00:00+08:00',definition:'依訂單總金額'};},
 navigatePage:p=>routes.push(p),confirm:()=>true,dirtyForms:new Set(),pageDirty:false,
 location:{hash:'#kol/statistics'},window:{addEventListener:(name,fn)=>events[name]=fn},
 history:{replaceState:(_state,_title,url)=>{context.location.hash=url;}},
});
vm.runInContext(fs.readFileSync('web/status.js','utf8'),context);
vm.runInContext(fs.readFileSync('web/kol.js','utf8'),context);

const systemGroups=[{...k,...summary,id:'general',name:'一般訂單',system_group:true,platforms:[],utms:[],version:null},{...k,...summary,id:'unattributed',name:'待確認歸屬',system_group:true,platforms:[],utms:[],version:null}];
const oldRequest=context.request;context.request=async(path,body)=>{const result=await oldRequest(path,body);if(path.startsWith('/api/kols/report')){const id=new URLSearchParams(path.split('?')[1]).get('kol_id');return {...result,kol:systemGroups.find(g=>g.id===id)||k,groups:[...systemGroups,...result.groups],status_counts:{all:2,active:1,cancelled:0,excluded:1},orders:[{...order,status:'excluded',lines:[],batch_id:'b1'}]};}return result;};
(async()=>{
 await context.renderKol(['kol','list']);assert.match(text(root),/一般訂單/);assert.match(text(root),/待確認歸屬/);
 const classifications=all(root).find(n=>n.className==='kol-classifications');
 assert.ok(classifications);assert.equal(classifications.tag,'section');assert.match(text(classifications),/不是合作夥伴/);
 assert.ok(!all(classifications).some(n=>n.textContent===k.name),'KOL partners remain outside system categories');
 for(const id of ['general','unattributed']){
  assert.ok(all(root).some(n=>n.href?.startsWith('#kol/detail/'+id+'?')));assert.ok(!all(root).some(n=>n.href?.startsWith('#kol/edit/'+id)||n.href?.startsWith('#kol/delete/'+id)));
 }
 for(const id of ['general','unattributed']){
  await context.renderKol(['kol','detail',id]);assert.match(text(root),new RegExp(systemGroups.find(g=>g.id===id).name));assert.ok(!all(root).some(n=>n.textContent==='修改'||n.textContent==='刪除'));assert.ok(all(root).some(n=>n.tag==='option'&&n.value==='excluded'));
  assert.match(text(root),/已排除/);const orderLink=all(root).find(n=>n.tag==='a'&&n.textContent===order.order_id);await context.renderKol(orderLink.href.slice(1).split('/'));assert.match(text(root),/已排除/);assert.ok(all(root).some(n=>n.href?.startsWith('#kol/import/b1')));assert.ok(all(root).some(n=>n.href?.startsWith('#kol/detail/'+id+'?')));
  await context.renderKol(['kol','products',id]);const product=all(root).find(n=>n.textContent==='訂單列表');await context.renderKol(product.href.slice(1).split('/'));assert.ok(all(root).some(n=>n.href?.startsWith('#kol/products/'+id+'?')));
  for(const page of ['edit','delete']){await context.renderKol(['kol',page,id]);assert.match(text(root),/系統分類.*不能修改或刪除/);assert.ok(!all(root).some(n=>n.textContent==='保存KOL'||n.textContent==='確認刪除KOL'));}
 }
 many=true;await context.renderKol(['kol','list']);all(root).find(n=>n.textContent==='下一頁').onclick();assert.match(text(root),/一般訂單/);assert.match(text(root),/待確認歸屬/);
 await context.renderKol(['kol','statistics']);assert.match(text(root),/一般訂單/);assert.match(text(root),/待確認歸屬/);
 console.log('System groups fixed rows, view-only guards, excluded tracking and drilldown checks passed (DOM adapter only)');
})().catch(e=>{console.error(e);process.exitCode=1;});
