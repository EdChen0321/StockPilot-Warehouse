const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{constructor(tag,text='',cls=''){Object.assign(this,{tag,textContent:text,className:cls,children:[]});}append(...items){this.children.push(...items);}set innerHTML(v){throw Error('unsafe HTML');}}
const source=fs.readFileSync('web/assistant-chat.js','utf8');
const context=vm.createContext({make:(...args)=>new Element(...args)});
vm.runInContext(source.slice(source.indexOf('  function renderOrderLinks('),source.indexOf('  function renderImplementation(')),context);
const row={account_id:'shop/a',order_id:'<ORDER|1>',fulfillment_status:'部分出貨',remaining_quantity:1,items:[{name:'杯',sku:'CUP',required_quantity:2,shipped_quantity:1,remaining_quantity:1}]};
const result={order_links_enabled:true,evidence:[{tool:'query_orders',filters:{view:'unshipped'},orders:[row]}]};
const all=e=>[e,...e.children.flatMap(all)];
const parent=new Element('article');context.renderOrderLinks(parent,result);
const link=all(parent).find(e=>e.tag==='a');
assert.deepEqual(JSON.parse(decodeURIComponent(link.href.slice('#orders/'.length))),['shop/a','<ORDER|1>']);
assert.ok(all(parent).some(e=>e.textContent.includes('需求 2、已出 1、未出 1')));
const mismatch=new Element('article');context.renderOrderLinks(mismatch,{...result,order_links_enabled:false});
assert.ok(!all(mismatch).some(e=>e.tag==='a'));
assert.ok(!source.includes('可查詢的資料'));assert.ok(!source.includes('ai-tool-option'));
console.log('Order links use exact composite identity, source guard and safe text; technical switches removed.');
