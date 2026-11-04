const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
  constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.events={};this.dataset={};this.value='';this.hidden=false;this.classList={add(){},remove(){}};}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;if(this.tag==='select')this.value='';}
  add(item){this.children.push(item);if(this.children.length===1)this.value=item.value;}
  get options(){return this.children;}
  setAttribute(){}
  addEventListener(name,handler){this.events[name]=handler;}
  set innerHTML(value){throw Error('No untrusted HTML');}
}
const all=e=>[e,...e.children.flatMap(all)],text=e=>all(e).map(e=>e.textContent).join('\n');
const root=new Element('section');
const row={case_id:'O02',round:1,category:'訂單',variant:'fast',question:'<script>unsafe()</script>',expected:{order_ids:['ORD-101']},
  checks:{evidence:true,route:true,answer:false},reasons:['回答缺少實際編號'],elapsed_ms:1300,execution_success:true,answer:'訂單項目1',
  timings:{setup_seconds:1.2,planning_seconds:0,query_calls:[{seconds:.1}],answer_seconds:0},calls:[],model_calls:0,tool_calls:1,business_data_unchanged:true};
const run={id:'one',status:'completed',records:[row],expected_attempts:1,dataset:'synthetic',model:'fixture',calendar:{today:'2026-11-02'}};
run.acceptance_checks=[{name:'取消與全部出貨排除',passed:true,detail:'已核對'},
  {name:'實際單據點擊',passed:null,detail:'待瀏覽器驗收'}, {name:'錯誤案例',passed:false,detail:'不符合預期'}];
let options;
const context=vm.createContext({document:{getElementById:()=>root,createElement:t=>new Element(t)},location:{hash:'#ai-evaluation'},
  Option:class extends Element{constructor(text,value){super('option',text);this.value=value;}},
  window:{addEventListener(){},echarts:true},echarts:{init:()=>({setOption:value=>options=value,resize(){}})},
  fetch:async url=>({ok:true,json:async()=>url.endsWith('/evaluations')?{runs:[{id:'one',started_at:'2026-11-02T00:00:00Z',status:'completed',completed_attempts:1,expected_attempts:1}]}:run}),
  setTimeout:()=>0,clearTimeout(){},module:{exports:{}}});
vm.runInContext(fs.readFileSync('web/ai-evaluation.js','utf8'),context);
setImmediate(()=>{
  for(const expected of ['AI 評測','需修正','回答缺少實際編號','訂單項目1','<script>unsafe()</script>','ORD-101'])assert.ok(text(root).includes(expected),expected);
  assert.equal(options.series.length,4);
  for(const value of ['訂單流程驗收','取消與全部出貨排除','通過','待驗收','未通過'])assert.ok(text(root).includes(value),value);
  const filter=all(root).find(e=>e.id==='eval-category');filter.value='庫存';filter.events.change();
  assert.ok(text(root).includes('目前篩選沒有符合的案例'));
  console.log('Dashboard renders actual failures, safe text, evidence, chart and empty filters (DOM adapter only)');
});
