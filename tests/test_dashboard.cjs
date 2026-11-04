// Controller checks using a DOM adapter; visual browser acceptance is separate.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map(),events=[],calls=[];
const element=(tag='',text='')=>({tag,textContent:text,children:[],append(...c){this.children.push(...c);},replaceChildren(...c){this.children=c;}});
const get=id=>{if(!elements.has(id))elements.set(id,Object.assign(element(),{value:'',hidden:false}));return elements.get(id);};
const sample={amount:'10.00',order_count:1,sales_quantity:2,pending_count:3,generated_at:'2026-10-03',filters:{start:'2026-10-01',end:'2026-10-02'},lines:[{account_id:'a',order_id:'o',line_id:'l',ordered_at:'2026-10-01T00:00:00Z',product_id:'p',product_name:'測試商品',quantity:2,amount:'10.00'}]};
let response=sample,fail=false,pending=null;
const context=vm.createContext({URLSearchParams,Date,Intl,Map,Set,console,$:get,node:element,materialTable:(headers,rows)=>({headers,rows}),boot:{products:[]},formatAmount:v=>String(v),dateTime:v=>v,
 Event:class{constructor(type){this.type=type;}},CustomEvent:class{constructor(type,options){this.type=type;Object.assign(this,options);}},window:{dispatchEvent:e=>events.push(e)},
 request:async path=>{calls.push(path);if(pending)return pending;if(fail)throw Error('離線');return response;}});
vm.runInContext(fs.readFileSync('web/sales-charts.js','utf8'),context);
(async()=>{
 get('dashboard-start').value='2026-10-01';get('dashboard-end').value='2026-10-02';
 get('filter-start').value='2020-01-01';get('filter-product').value='unrelated';
 await context.refreshDashboard();
 assert.equal(calls[0],'/api/sales?start=2026-10-01&end=2026-10-02');
 assert.equal(get('filter-start').value,'2020-01-01');assert.equal(get('filter-product').value,'unrelated');
 assert.equal(get('dashboard-average').textContent,'10.00');assert.equal(get('dashboard-pending').textContent,'3 筆');
 assert.equal(get('dashboard-sales-link').href,'#sales/'+encodeURIComponent('start=2026-10-01&end=2026-10-02'));
 assert.equal(events.at(-1).type,'dashboard-refreshed');
 assert.ok(calls.includes('/api/sales?start=2026-09-29&end=2026-09-30'));
 assert.equal(get('dashboard-comparison').children.find(c=>c.rows).rows[0][4],'0.0%');
 response={...sample,amount:'0.00',order_count:0,sales_quantity:0,lines:[]};await context.refreshDashboard();assert.equal(get('dashboard-average').textContent,'無有效訂單');
 fail=true;await context.refreshDashboard();assert.equal(get('dashboard-amount').textContent,'—');assert.equal(get('dashboard-sales-link').hidden,true);assert.equal(events.at(-1).type,'dashboard-error');
 fail=false;get('dashboard-end').value='2026-09-01';const count=calls.length;await context.refreshDashboard();assert.equal(calls.length,count);assert.match(get('dashboard-notice').textContent,/有效日期/);
 get('dashboard-end').value='2026-10-02';let release;pending=new Promise(resolve=>release=resolve);const old=context.refreshDashboard();pending=null;response=sample;await context.refreshDashboard();const latest=get('dashboard-notice').textContent;release({...sample,amount:'999.00'});await old;assert.equal(get('dashboard-notice').textContent,latest);assert.equal(get('dashboard-amount').textContent,'10.00');
 console.log('Dashboard independent dates, detail link, zero orders, loading errors, invalid dates and stale responses passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
