const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag,text='',className=''){Object.assign(this,{tag,textContent:String(text),className,children:[],clientWidth:900,isConnected:true});}
 append(...c){this.children.push(...c);}after(c){this.next=c;}before(c){this.previous=c;}replaceChildren(...c){this.children=c;}
 addEventListener(name,callback){this[name]=callback;}
 setAttribute(k,v){this[k]=v;}removeAttribute(k){delete this[k];}
}
const events={},instances=[],observers=[],metrics=new Element('div'),stock={products:[],units:[],alert_settings:[]};
const sales={lines:[{account_id:'a',order_id:'o',line_id:'l',ordered_at:'2026-10-01T00:00:00Z',product_id:'p',product_name:'商品',quantity:1,amount:'10.00'}],amount:'10.00',order_count:1,sales_quantity:1,filters:{}};
let rejectStock=false,resolveStock;
const context=vm.createContext({URLSearchParams,Date,Intl,Map,Set,console,
 Option:function(text,value){return Object.assign(new Element('option',text),{value});},
 node:(t,s,c)=>new Element(t,s===undefined?'':s,c),$:()=>metrics,boot:{products:[]},dateTime:v=>v,
 document:{documentElement:{}},getComputedStyle:()=>({fontFamily:'sans-serif',getPropertyValue:()=> '#23634f'}),
 materialTable:()=>new Element('table'),
 ResizeObserver:class{constructor(cb){this.cb=cb;observers.push(this);}observe(el){this.el=el;}disconnect(){this.disconnected=true;}},
 echarts:{init:()=>{const i={resizeCalls:0,options:[],disposed:false,resize(){this.resizeCalls++;},setOption(o){this.options.push(o);},dispose(){this.disposed=true;}};instances.push(i);return i;}},
 window:{echarts:true,addEventListener:(key,cb)=>events[key]=cb},
 request:async()=>{if(resolveStock)return new Promise(r=>resolveStock=r);if(rejectStock)throw new Error('offline');return stock;}
});
vm.runInContext(fs.readFileSync('web/sales-charts.js','utf8'),context);
const text=el=>[el.textContent,...el.children.map(text)].join(' ');
(async()=>{
 events['dashboard-loading']();assert.match(text(metrics.next),/載入中/);
 events['dashboard-refreshed']({detail:{data:sales}});await new Promise(setImmediate);
 assert.equal(instances.length,2);assert.match(text(metrics.next),/尚無在庫件/);
 observers[0].el.clientWidth=390;observers[0].cb();assert.equal(instances[0].resizeCalls,2);assert.equal(instances[0].options.at(-1).textStyle.fontSize,14);
 events.resize();assert.ok(instances[0].resizeCalls>=3);
 events['dashboard-loading']();assert.ok(instances.every(i=>i.disposed));assert.ok(observers.every(o=>o.disconnected));
 events['dashboard-error']({detail:{message:'測試錯誤'}});assert.match(text(metrics.next),/測試錯誤/);
 rejectStock=true;events['dashboard-refreshed']({detail:{data:sales}});await new Promise(setImmediate);assert.match(text(metrics.next),/庫存讀取失敗/);
 // An old inventory response must not overwrite the state of a newer failed request.
 rejectStock=false;resolveStock=true;events['dashboard-refreshed']({detail:{data:sales}});
 events['dashboard-loading']();events['dashboard-error']({detail:{message:'新查詢錯誤'}});resolveStock(stock);resolveStock=null;await new Promise(setImmediate);
 assert.match(text(metrics.next),/新查詢錯誤/);assert.doesNotMatch(text(metrics.next),/尚無在庫件/);
 stock.products=[{id:'p',name:'測試杯',sku:'P',on_hand:0,available:0}];stock.alert_settings=[{product_id:'p',stock_min:5}];
 events['dashboard-refreshed']({detail:{data:sales}});await new Promise(setImmediate);
 assert.match(text(metrics.next),/目前共有 1 個 SKU 低於門檻/);
 const shortage=instances.at(-1);assert.equal(shortage.options.at(-1).series[1].name,'距門檻缺口');assert.equal(shortage.options.at(-1).series[1].data[0],5);
 events['dashboard-loading']();assert.ok(shortage.disposed);
 context.window.echarts=null;events['dashboard-refreshed']({detail:{data:sales}});await new Promise(setImmediate);assert.match(text(metrics.next),/圖表套件未載入/);
 console.log('Charts loading/error/empty, library fallback, stale requests, disposal and ResizeObserver/window resize passed (VM, not browser).');
})().catch(e=>{console.error(e);process.exitCode=1;});
