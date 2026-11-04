const assert=require('node:assert/strict');
const {findShipmentOrder}=require('../web/format.js');
const orders=Array.from({length:10000},(_,i)=>({order_id:String(20261028000000+i),account_id:'a',status:'active'}));
assert.equal(findShipmentOrder(orders,' 20261028009999 ').order_id,'20261028009999');
assert.throws(()=>findShipmentOrder(orders,'20261028'),/找不到/);
assert.throws(()=>findShipmentOrder(orders,''),/請輸入/);
assert.throws(()=>findShipmentOrder([{order_id:'C',status:'cancelled'}],'C'),/找不到/);
assert.throws(()=>findShipmentOrder([{order_id:'D',status:'active',account_id:'a'},{order_id:'D',status:'active',account_id:'b'}],'D'),/不同來源/);
console.log('5 exact order lookup assertions passed (10000 synthetic orders)');
