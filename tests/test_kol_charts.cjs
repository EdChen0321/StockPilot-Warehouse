const assert=require('node:assert/strict');
const {kolChartModel,kolCompare}=require('../web/kol.js');
const orders=[
 {status:'active',ordered_at:'2026-01-31T17:00:00Z',total:'100.00',kol_id:'k'},
 {status:'active',ordered_at:'2026-02-03T00:00:00+08:00',total:null,kol_id:'unattributed'},
 {status:'cancelled',ordered_at:'2026-02-03T00:00:00+08:00',total:'999.00',kol_id:'k'}];
const data={orders,filters:{start:'2026-02-01',end:'2026-02-03'},groups:[{id:'k',name:'A',order_count:1,total:'100.00',average:'100.00'},{id:'general',system_group:true,order_count:0,total:'0.00',average:'0.00'},{id:'unattributed',system_group:true,order_count:1,total:null,average:null}]};
let m=kolChartModel(data,'day','total');
assert.deepEqual(m.trend.labels,['2026-02-01','2026-02-02','2026-02-03']);
assert.deepEqual(m.trend.values,[100,0,null]);
assert.deepEqual(m.share.values,[1,0,1]);
assert.equal(m.ranking.labels.length,1);
assert.equal(m.quality.missingTotal,1);
assert.equal(m.quality.missingUpdated,2);
assert.deepEqual(kolChartModel(data,'month','total').trend.values,[null]);
assert.deepEqual(kolChartModel(data,'month','orders').trend.values,[2]);
assert.ok(kolCompare({id:'a',total:'100.00'},{id:'b',total:'20.00'},'-total')<0);
assert.ok(kolCompare({id:'a',total:null},{id:'b',total:'20.00'},'total')>0);
console.log('KOL charts: Taipei dates, cancelled exclusion, missing totals, zero gaps, numeric sorting passed.');
