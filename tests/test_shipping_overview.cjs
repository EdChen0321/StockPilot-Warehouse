const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map(),node=(tag,text='')=>({tag,text,children:[],append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},addEventListener(){}});
const get=id=>{if(!elements.has(id))elements.set(id,node('div'));return elements.get(id);};
const text=el=>[el.text,...el.children.map(text)].join(' ');
let pending=null,fail=false;
const context=vm.createContext({Date,Intl,Map,URLSearchParams,node,$:get,window:{addEventListener(){}},dateTime:v=>v,
 stockDetailGrid:rows=>node('div',rows.map(row=>row.join(':')).join(' ')),
 request:async path=>{assert.equal(path,'/api/stock?summary=1','current backlog uses the summary without period, channel or product filters');if(fail)throw Error('offline');if(pending)return pending;return {shipments:['draft','draft','allocated','picked','staged','shipped','cancelled'].map(state=>({state}))};}});
vm.runInContext(fs.readFileSync('web/shipping-stats.js','utf8'),context);
(async()=>{
 await context.refreshShippingCurrent(0);assert.match(text(get('shipping-current')),/未完成出貨單合計:5 張/);assert.match(text(get('shipping-current')),/準備中:2 張/);
 fail=true;await context.refreshShippingCurrent(0);assert.match(text(get('shipping-current')),/讀取失敗/);assert.doesNotMatch(text(get('shipping-current')),/合計:5/);
 fail=false;let resolve;pending=new Promise(r=>resolve=r);const old=context.refreshShippingCurrent(0);pending=null;vm.runInContext('shippingStatisticsSequence=1',context);await context.refreshShippingCurrent(1);const latest=text(get('shipping-current'));resolve({shipments:[]});await old;assert.equal(text(get('shipping-current')),latest);
 console.log('Shipping backlog excludes completed/cancelled orders, ignores period filters, and handles errors/stale responses.');
})().catch(error=>{console.error(error);process.exitCode=1;});
