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
   return path==='/api/kols'?{kols:[k]}:{summary,filtered_summary:{...summary,total:'300.00',average:'300.00'},kol:k,status_counts:{all:1,active:1,cancelled:0},utm_counts:{sunny:1},product:{id:'p1',name:'合成杯',sku:'CUP',kind:'product'},orders:[order],products:[{id:'p1',account_id:'a1',kind:'product',name:'合成杯',sku:'CUP',quantity:1,order_count:1,amount:'1000.00'}],groups:many?Array.from({length:25},(_,i)=>({...k,...summary,id:`k${i}`,name:`合成夥伴${i}`,order_count:i})):[{...k,...summary}],generated_at:'2026-01-02T00:00:00+08:00',definition:'依訂單總金額'};},
 navigatePage:p=>routes.push(p),confirm:()=>true,dirtyForms:new Set(),pageDirty:false,
 location:{hash:'#kol/statistics'},window:{addEventListener:(name,fn)=>events[name]=fn},
 history:{replaceState:(_state,_title,url)=>{context.location.hash=url;}},
});
vm.runInContext(fs.readFileSync('web/status.js','utf8'),context);
vm.runInContext(fs.readFileSync('web/kol.js','utf8'),context);
(async()=>{
 await context.renderKol(['kol','statistics']);
 assert.ok(all(root).find(n=>n.className==='kol-row-actions').children.every(n=>n.tag==='button'||n.className==='button secondary'));
 assert.match(text(root),/960.00 元/);
 const filter=all(root).find(n=>n.tag==='form');
 const month=all(filter).find(n=>n.name==='month');month.value='2028-02';month.onchange();
 assert.equal(all(filter).find(n=>n.name==='end').value,'2028-02-29');
 all(filter).find(n=>n.name==='start').value='2026-01-01';
 await filter.onsubmit({preventDefault(){}});
 await new Promise(r=>setImmediate(r));
 assert.ok(calls.some(c=>c.path.includes('start=2026-01-01')));
 assert.match(context.location.hash,/start=2026-01-01/,'filter survives reload via URL');
 await context.renderKol(['kol','list']);
 const partnerList=all(root).find(n=>n.className==='kol-partner-list');
 assert.ok(partnerList);assert.equal(all(partnerList).filter(n=>n.tag==='th').length,9);
 assert.ok(!all(partnerList).some(n=>n.tag==='details'),'row actions must never be hidden in a disclosure');
 const actions=all(partnerList).find(n=>n.className==='kol-row-actions');
 assert.deepEqual(actions.children.map(n=>n.textContent),['檢視','修改','刪除']);
 assert.ok(all(actions).some(n=>n.href?.startsWith('#kol/edit/')));assert.ok(all(actions).some(n=>n.href?.startsWith('#kol/delete/')));
 const toolbar=all(partnerList).find(n=>n.className==='kol-pagination');assert.ok(!all(toolbar).some(n=>n.name==='sort'));assert.ok(all(partnerList).some(n=>n.name==='sort'));
 assert.ok(all(partnerList).some(n=>n.className==='table-scroll'&&n.tabindex==='0'));
 const filterDisclosure=all(root).find(n=>n.className==='kol-query-panel');
 assert.ok(filterDisclosure);assert.equal(filterDisclosure.tag,'section','date query stays visible');
 assert.match(text(filterDisclosure),/2026-01-01/,'applied date remains visible');
 assert.ok(all(filterDisclosure).some(n=>n.tag==='form'),'filters remain available');
 assert.ok(!all(filterDisclosure).some(n=>n.tag==='details'),'all filter fields stay visible');
 await context.renderKol(['kol','detail','k1']);
 assert.match(text(root),/期間有效業績/);
 const filteredSummary=all(root).find(n=>n.className==='kol-filtered-summary');
 assert.match(text(filteredSummary),/符合條件訂單/);
 assert.match(text(filteredSummary),/300.00 元/);
 assert.doesNotMatch(text(filteredSummary),/960.00 元/);
 const currentTab=all(root).find(n=>n.textContent==='訂單概況與明細');
 assert.equal(currentTab['aria-current'],'page');
 assert.ok(!all(root).some(n=>n['aria-label']==='KOL 分析功能'));
 assert.ok(!all(root).some(n=>['返回KOL列表','返回表現統計'].includes(n.textContent)));
 assert.ok(all(root).find(n=>n.className==='kol-breadcrumb').children.some(n=>n.href?.startsWith('#kol/list?')));
 assert.ok(!all(root).some(n=>n.tag==='details'),'cooperation information stays visible');
 const detailTop=all(root).find(n=>n.className==='kol-detail-top');
 assert.ok(detailTop.children.some(n=>n.className==='kol-info'));
 assert.ok(detailTop.children.some(n=>n.className==='kol-detail-statistics'));
 assert.ok(root.children.indexOf(detailTop)<root.children.findIndex(n=>n.className==='kol-query-panel'));
 const link=all(root).find(n=>n.tag==='a'&&n.textContent===order.order_id);
 assert.ok(link.href.startsWith('#kol/order/k1/'+encodeURIComponent(JSON.stringify(['a1',order.order_id]))+'?'));
 await context.renderKol(link.href.slice(1).split('/'));
 assert.match(text(root),/960.00 元/);assert.match(text(root),/1000.00 元/);
 assert.ok(all(root).some(n=>n.href?.startsWith('#kol/detail/k1?')));
 await context.renderKol(['kol','products','k1']);
 assert.equal(all(root).find(n=>n.textContent==='商品銷售統計')['aria-current'],'page');
 const productLink=all(root).find(n=>n.textContent==='訂單列表'&&n.tag==='a');
 assert.ok(productLink.href.startsWith('#kol/product-orders/k1/'));
 await context.renderKol(productLink.href.slice(1).split('/'));
 assert.match(text(root),/該產品數量/);
 const productOrder=all(root).find(n=>n.tag==='a'&&n.textContent===order.order_id);
 await context.renderKol(productOrder.href.slice(1).split('/'));
 assert.ok(all(root).some(n=>n.textContent==='返回相關訂單'&&n.href?.startsWith('#kol/product-orders/k1/')));
 await context.renderKol(['kol','edit','k1']);
 assert.equal(all(root).find(n=>n.textContent==='刪除此KOL').className,'danger');
 const editor=all(root).find(n=>n.tag==='form');
 all(editor).find(n=>n.name==='name').value='合成更新';
 await editor.onsubmit({preventDefault(){}});
 assert.equal(calls.at(-1).body.name,'合成更新');assert.equal(calls.at(-1).body.expected_version,1);
 assert.equal(calls.at(-1).body.started_at,new Date(k.started_at).toISOString(),'editing name must preserve attribution boundary seconds');
 assert.equal(routes.at(-1),'kol');
 fail=true;await context.renderKol(['kol']);assert.match(text(root),/測試讀取失敗/);fail=false;
 const previous=calls.length;
 await events['workspace-refreshed']();
 assert.ok(calls.length>previous,'workspace refresh must reload visible KOL analysis');
 context.location.hash='#kol/edit/k1';const count=calls.length;await events['workspace-refreshed']();
 assert.equal(calls.length,count,'refresh must not discard the editor');
 many=true;await context.renderKol(['kol','list']);
 assert.match(text(root),/共 25 筆/);
 all(root).find(n=>n.tag==='button'&&n.textContent==='下一頁').onclick();
 assert.match(text(root),/21–25/);
 await context.renderKol(['kol','detail','k1']);await context.renderKol(['kol','list']);
 assert.match(text(root),/21–25/,'return to list preserves its page');
 const sort=all(root).find(n=>n.name==='sort');sort.value='-order_count';sort.onchange();
 assert.match(text(root),/1–20/,'sorting resets pagination');
 assert.ok(text(root).indexOf('合成夥伴24')<text(root).indexOf('合成夥伴23'));
 console.log('KOL rendering, filters, drilldown, edit, error and refresh checks passed (DOM adapter, not a browser)');
})().catch(e=>{console.error(e);process.exitCode=1;});
