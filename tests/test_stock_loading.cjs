'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/stock.js','utf8');
const requests=[],listeners={},pending=new Map();let stockReads=0;
const context=vm.createContext({Set,Map,Promise,decodeURIComponent,encodeURIComponent,
 location:{hash:'#assistant'},stockLoaded:false,refreshStock:async()=>{stockReads++;},renderSkuStock(){},catalogueMessage(){},stockMessage(){},
 window:{addEventListener:(name,fn)=>listeners[name]=fn},
 request:async path=>{requests.push(path);if(path==='/api/stock/products')return {products:[]};return new Promise(resolve=>pending.set(path,resolve));}
});
vm.runInContext(source.slice(source.indexOf('let stockData'),source.indexOf('let selectedShipment')),context);
const start=source.indexOf("window.addEventListener('workspace-refreshed',()=>{");
vm.runInContext(source.slice(start,source.indexOf('\n});',start)+4),context);
(async()=>{
 listeners['workspace-refreshed']();await Promise.resolve();
 assert.equal(requests.length,0,'AI page must not preload operational stock');
 context.location.hash='#catalogue';listeners['workspace-refreshed']();await context.loadStockProducts();
 assert.deepEqual(requests,['/api/stock/products']);
 context.location.hash='#shipping';await Promise.all([context.ensureStockLoaded(),context.ensureStockLoaded()]);
 assert.equal(stockReads,1,'concurrent routes share the initial request');
 context.location.hash='#shipping/a';const a=context.loadStockHistory();
 context.location.hash='#shipping/b';const b=context.loadStockHistory();
 const data={events:[{id:'newest'},{id:'older'}],unit_cost_revisions:[],cost_revisions:[]};
 pending.get('/api/stock/history/shipment/b')(data);await b;
 pending.get('/api/stock/history/shipment/a')({...data,events:[{id:'stale'}]});await a;
 assert.equal(vm.runInContext('stockData.events[0].id',context),'newest','stale route cannot replace newer history');
 assert.equal(vm.runInContext('stockData.events[1].id',context),'older','source event ordering retained');
 const before=requests.length;await context.loadStockHistory();assert.equal(requests.length,before,'same detail reuses history until stock refresh');
 console.log('lazy pages, deduplicated loading, scoped history, ordering and stale response guards passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
