const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/workspace.js', 'utf8');
const wrapper = source.slice(source.indexOf('const originalRequest=request;'), source.indexOf('function navigatePage('));
(async () => {
  for (const [url, result, expected] of [
    ['/api/receipts', {id:'receipt-1'}, 'stock'],
    ['/api/shipments', {id:'shipment-1'}, 'shipping/shipment-1'],
    ['/api/orders', {order_id:'20261028000042'}, 'orders/'+encodeURIComponent(JSON.stringify(['source','20261028000042']))],
  ]) {
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    let loaded = false;
    const routes = [];
    const context = vm.createContext({
      request: async () => result, submittingForm:null, dirtyForms:new Set(), pageDirty:false,
      refreshStock:async () => { await gate; loaded = true; },
      navigatePage:path => { assert.equal(loaded,true,'detail must not open against stale snapshot'); routes.push(path); },
    });
    vm.runInContext(wrapper, context);
    const pending = context.request(url, {account_id:'source'});
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(routes, [], 'no navigation while refresh is pending');
    release();
    assert.equal(await pending, result);
    assert.deepEqual(routes, [expected]);
  }
  console.log('Saved receipt/order/shipment route sequencing: 3 passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
