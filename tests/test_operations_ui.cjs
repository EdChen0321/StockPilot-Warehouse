const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const {operationsView}=require('../web/operations-ui.js');
for(const area of ['dashboard','sales','catalogue','stock','shipping','shipping-statistics','kol','materials','inventory','quality','stock-reports'])assert.ok(operationsView(area));
for(const area of ['orders','import','history','sources'])assert.ok(operationsView(area));
assert.equal(operationsView('unknown'),null);
function classes(){const set=new Set();return {add:v=>set.add(v),remove:v=>set.delete(v),contains:v=>set.has(v),toggle(v,on){on=on??!set.has(v);on?set.add(v):set.delete(v);return on;}};}
const events={},button={setAttribute(k,v){this[k]=v;},addEventListener(k,v){events[k]=v;},focus(){this.focused=true;}};
const body={classList:classes()},heading={},section={classList:classes()},link={focus(){this.focused=true;}};
const context=vm.createContext({document:{body,getElementById:id=>id==='operations-menu-toggle'?button:section,querySelector:q=>q==='.page-heading h1'?heading:link,addEventListener:(k,v)=>events[k]=v}});
vm.runInContext(fs.readFileSync('web/operations-ui.js','utf8'),context);
vm.runInContext("applyOperationsView('stock')",context);
assert.equal(heading.textContent,'營運管理 / 入庫管理');assert.ok(body.classList.contains('operations-ui'));
events.click();assert.equal(button['aria-expanded'],'true');assert.equal(link.focused,true);
events.keydown({key:'Escape'});assert.equal(button['aria-expanded'],'false');assert.equal(button.focused,true);
events.click();vm.runInContext("applyOperationsView('materials')",context);
assert.equal(body.classList.contains('operations-ui'),true);assert.equal(body.classList.contains('operations-menu-open'),false);
assert.equal(button['aria-expanded'],'false');assert.equal(heading.textContent,'營運管理 / 物料與採購');
for(const area of ['orders','import','history','sources']){context.applyOperationsView(area);assert.ok(body.classList.contains('operations-ui'));assert.ok(section.classList.contains('operations-surface'));}
console.log('Operations scope, navigation toggle, Escape focus and route reset passed (DOM adapter, not a browser)');
