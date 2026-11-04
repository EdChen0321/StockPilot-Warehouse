const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element {
  constructor(tag,text='',className=''){Object.assign(this,{tag,textContent:text,children:[],className});}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  set innerHTML(value){throw new Error('Untrusted content must not be interpreted as HTML');}
}
const all=e=>[e,...e.children.flatMap(all)];
const text=e=>all(e).map(x=>x.textContent).join('\n');
const implementation=new Element('section');
const context=vm.createContext({make:(...args)=>new Element(...args),implementation});
const source=fs.readFileSync('web/assistant-chat.js','utf8');
vm.runInContext(source.slice(source.indexOf('  function disclosure('),source.indexOf('  function updateEffort(')),context);
context.renderImplementation({implementation:[{name:'System prompt',content:'兩階段',prompts:[{name:'自訂',text:'<script>bad()</script>'}]},{name:'RAG／企業知識庫',content:''}]});
assert.match(text(implementation),/<script>bad\(\)<\/script>/);
const rows=all(implementation).filter(e=>e.className==='ai-capability');
assert.equal(rows[1].children[1].children.length,0);
const parent=new Element('article');
context.renderExecution(parent,{request_id:'request-one',execution:{model_call_count:2,tool_call_count:1,total_seconds:1.25,cache:{hits:1,misses:0},
  model_requests:[{stage:'planning',model:'fixture',effort:'low',system_prompt:'規劃規則',input:'問題甲',output_schema:{type:'object'},output:'計畫甲'},
                  {stage:'answer',model:'fixture',effort:'low',system_prompt:'回答規則',input:'證據甲',output:'答案甲'}],
  tool_calls:[{tool:'orders',seconds:0.001,parameters:{view:'summary'},result:{amount:null}}]}});
for(const expected of ['2 份 system prompt','1 次工具查詢','規劃規則','回答規則','問題甲','證據甲','計畫甲','答案甲','null','request-one'])assert.ok(text(parent).includes(expected),expected);
const headings=all(parent).filter(e=>e.tag==='h4').map(e=>e.textContent);
assert.deepEqual(headings,['你的問題','AI 判斷要怎麼處理','系統執行查詢','AI 整理回答']);
const rejected=new Element('article');
context.renderExecution(rejected,{request_id:'r2',scope:'rejected',execution:{model_call_count:1,tool_call_count:0,total_seconds:0.5,
  model_requests:[{stage:'planning',system_prompt:'範圍',input:'教 SQL',output:'reject',model:'fixture',effort:'low'}],tool_calls:[]}});
assert.match(text(rejected),/固定拒答，未執行工具/);
const fixed=new Element('article');
context.renderExecution(fixed,{request_id:'r3',execution:{model_call_count:0,tool_call_count:1,total_seconds:1.3,
  fixed_query:{question:'目前庫存總量是多少？'},model_requests:[],
  tool_calls:[{parameters:{view:'summary'},result:{totals:{on_hand:118}}}]}});
for(const expected of ['目前庫存總量是多少？','未呼叫模型','118','查看完整查詢結果'])assert.ok(text(fixed).includes(expected));
assert.ok(!text(fixed).includes('AI 整理回答'));
console.log('AI execution rendering: exact content, sequence, blank RAG, rejection and text-only safety passed.');
