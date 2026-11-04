const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
  constructor(tag,text='',cls=''){Object.assign(this,{tag,textContent:text,className:cls,children:[],handlers:{}});}
  append(...items){for(const item of items){item.parent=this;this.children.push(item);}}
  get firstChild(){return this.children[0];}
  addEventListener(name,fn){this.handlers[name]=fn;}
  querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...c.querySelectorAll(tag)]);}
  replaceWith(next){const p=this.parent;p.children[p.children.indexOf(this)]=next;next.parent=p;}
}
const filters={view:'unshipped',start:'2026-10-01',end:'2026-10-31',account_id:'shop/a',status:'active'};
const row=n=>({account_id:'shop/a',order_id:'ORDER-'+n,fulfillment_status:'尚未出貨',remaining_quantity:1,items:[]});
const result=(offset)=>({answer:'page '+offset,order_links_enabled:true,evidence:[{tool:'query_orders',filters,offset,limit:5,total_rows:7,truncated:offset===0,orders:(offset===0?[1,2,3,4,5]:[6,7]).map(row)}]});
let calls=[],fail=false;
const context=vm.createContext({make:(...args)=>new Element(...args),history:[],showStatus:()=>{},renderExecution:()=>{},
  run:async fn=>fn(),request:async(path,body)=>{calls.push({path,body});if(fail)throw Error('offline');return result(body.order_page.offset);}});
const source=fs.readFileSync('web/assistant-chat.js','utf8');
vm.runInContext(source.slice(source.indexOf('  function renderOrderLinks('),source.indexOf('  function renderImplementation(')),context);
(async()=>{
  const parent=new Element('article');context.renderOrderLinks(parent,result(0));
  let buttons=parent.querySelectorAll('button');assert.equal(buttons[0].disabled,true);assert.equal(buttons[1].disabled,false);
  await buttons[1].handlers.click();
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0].body)),{question:'訂單清單分頁',order_page:{...filters,offset:5,limit:5}});
  assert.equal(parent.querySelectorAll('a').length,2);
  assert.ok(parent.querySelectorAll('summary').some(e=>e.textContent.includes('ORDER-6')));
  buttons=parent.querySelectorAll('button');assert.equal(buttons[1].disabled,true);
  fail=true;await assert.rejects(buttons[0].handlers.click(),/offline/);
  assert.equal(buttons[0].disabled,false);assert.equal(buttons[1].disabled,true);
  fail=false;await buttons[0].handlers.click();assert.equal(parent.querySelectorAll('a').length,5);
  assert.equal(parent.children.length,1);assert.equal(context.history.length,4);
  console.log('Paging preserves filters, replaces rows, supports back, and restores buttons after failure (DOM adapter).');
})().catch(e=>{console.error(e);process.exitCode=1;});
