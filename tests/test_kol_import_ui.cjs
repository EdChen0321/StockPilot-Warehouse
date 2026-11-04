// Small DOM adapter for rendering/interaction logic; this is NOT browser validation.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
class Element {
 constructor(tag,text=''){this.tag=tag;this.textContent=String(text);this.children=[];this.value='';}
 append(...children){for(const c of children){c.parent=this;this.children.push(c);}}
 prepend(...children){for(const c of children)c.parent=this;this.children.unshift(...children);}
 replaceChildren(...children){this.children=[];this.append(...children);}
 setAttribute(k,v){this[k]=v;}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this);}
 scrollIntoView(){}
}
function all(el){return [el,...el.children.flatMap(all)];}
function text(el){return all(el).map(n=>n.textContent).join(' ');}
const root=new Element('section');const routes=[];const events={};
const k={id:'k1',name:'合成小晴',platforms:['IG'],utms:['sunny'],started_at:'2026-01-01T00:00:59+08:00',version:1};
const summary={order_count:1,total:'960.00',average:'960.00',known_total:'960.00',missing_total_count:0};
const order={account_id:'a1',order_id:'ORDER/中文',status:'active',product_quantity:1,ordered_at:'2026-01-02T00:00:00+08:00',total:'960.00',kol_name:k.name,utm_medium:'sunny',reason:'UTM精確匹配',rule_version:1,revision:1,batch_id:'batch1',lines:[{product_id:'p1',name:'合成杯',sku:'CUP',quantity:1,amount:'1000.00'}]};
let calls=[],fail=false,slow,many=false;
const sessionValues=new Map();
const context=vm.createContext({sessionStorage:{getItem:k=>sessionValues.get(k)||null,setItem:(k,v)=>sessionValues.set(k,v),removeItem:k=>sessionValues.delete(k)},atob,Uint8Array,setTimeout,clearTimeout,Node:Element,URLSearchParams,Date,JSON,crypto:require('node:crypto').webcrypto,
 node:(tag,t,c)=>Object.assign(new Element(tag,t===undefined?'':t),{className:c||''}),
 $:()=>root,formatAmount:v=>v,boot:{accounts:[{id:'a1',name:'合成店'}]},
 Option:function(t,v){return Object.assign(new Element('option',t),{value:v});},
 FormData:class{constructor(form){this.values=all(form).filter(n=>n.name&&n.type!=='checkbox').map(n=>[n.name,n.value]);}[Symbol.iterator](){return this.values[Symbol.iterator]();}},
 request:async(path,body)=>{calls.push({path,body});if(fail)throw new Error('測試讀取失敗');if(slow)await slow;
   return path==='/api/kols'?{kols:[k]}:{summary,kol:k,status_counts:{all:1,active:1,cancelled:0},utm_counts:{sunny:1},product:{id:'p1',name:'合成杯',sku:'CUP',kind:'product'},orders:[order],products:[{id:'p1',account_id:'a1',kind:'product',name:'合成杯',sku:'CUP',quantity:1,order_count:1,amount:'1000.00'}],groups:many?Array.from({length:25},(_,i)=>({...k,...summary,id:`k${i}`,name:`合成夥伴${i}`,order_count:i})):[{...k,...summary}],generated_at:'2026-01-02T00:00:00+08:00',definition:'依訂單總金額'};},
 navigatePage:p=>routes.push(p),confirm:()=>true,dirtyForms:new Set(),pageDirty:false,
 location:{hash:'#kol/statistics'},window:{addEventListener:(name,fn)=>events[name]=fn},
 history:{replaceState:(_state,_title,url)=>{context.location.hash=url;}},
});
vm.runInContext(fs.readFileSync('web/status.js','utf8'),context);
vm.runInContext(fs.readFileSync('web/kol.js','utf8'),context);

vm.runInContext(fs.readFileSync('web/kol-import.js','utf8'),context);

const batch={id:'batch1',account_id:'a1',filename:'<unsafe>.xlsx',state:'preview',can_confirm:true,summary:{raw_row_count:2,clean_line_count:1,order_count:1,product_count:1,excluded_count:1,order_total:'960'},orders:[{...order,lines:[{name:'杯',sku:'CUP',original_sku:'CUP+X',quantity:1,amount:null,row_index:2}]}],errors:[],warnings:[],exclusions:[{row_index:3,order_id:'O2',original_sku:'X',reason:'取消'}],unmatched_utms:[{utm_medium:'unknown',order_count:1}],no_utm_count:2};
const originalRequest=context.request;
context.request=async(path,body)=>{if(path.startsWith('/api/kol-import')){calls.push({path,body});return path==='/api/kol-import/batches'?{batches:[batch]}:path==='/api/kol-import/confirm'?{...batch,state:'confirmed'}:batch;}return originalRequest(path,body);};
(async()=>{
 await context.renderKol(['kol','imports']);assert.match(text(root),/<unsafe>.xlsx/);
 batch.changes={new:1,updated:2,unchanged:3,excluded:4};
 await context.renderKol(['kol','import','batch1']);assert.match(text(root),/以完整訂單計/);assert.ok(text(root).includes('CUP+X'));assert.match(text(root),/無 UTM.*2/);assert.match(text(root),/unknown/);assert.match(text(root),/將新增/);assert.match(text(root),/將更新/);assert.match(text(root),/相同略過/);assert.match(text(root),/全單替換/);
 const confirm=all(root).find(n=>n.tag==='button'&&n.textContent==='確認匯入');assert.ok(confirm&&!confirm.disabled);await confirm.onclick();assert.deepEqual(Object.keys(calls.find(c=>c.path==='/api/kol-import/confirm').body),['batch_id']);
 assert.ok(all(root).some(n=>n.textContent==='查看一般訂單'&&n.href?.startsWith('#kol/detail/general?')));
 batch.errors=[{row_index:2,message:'金額衝突'}];await context.renderKol(['kol','import','batch1']);assert.match(text(root),/金額衝突/);assert.ok(all(root).filter(n=>n.textContent==='確認匯入').every(n=>n.disabled));
 await context.renderKol(['kol','import-new']);assert.ok(all(root).some(n=>n.type==='file'&&n.accept==='.xlsx'));context.boot.accounts=[];await context.renderKol(['kol','import-new']);assert.ok(all(root).some(n=>n.href==='#sources'));
 context.boot.accounts=[{id:'a1',name:'合成店'}];
 await context.renderKol(['kol','import','batch1']);
 const choice=all(root).find(n=>n.name==='kol_id');choice.value='k1';choice.onchange();
 const editLink=all(root).find(n=>n.textContent==='編輯所選 KOL 並追加 UTM');
 await context.renderKol(editLink.href.slice(1).split('/'));
 const editor=all(root).find(n=>n.tag==='form');
 assert.equal(all(editor).find(n=>n.name==='utms').value,'sunny, unknown');
 await editor.onsubmit({preventDefault(){}});
 const saved=calls.findLast(c=>c.path==='/api/kols'&&c.body);
 assert.equal(saved.body.expected_version,1);assert.equal(saved.body.id,'k1');assert.equal(saved.body.platforms,'IG');assert.equal(saved.body.started_at,new Date(k.started_at).toISOString());assert.ok(saved.body.operation_id);
 assert.equal(routes.at(-1),'kol/import/batch1');
 context.FileReader=class{readAsDataURL(){this.result='data:application/octet-stream;base64,UEs=';this.onload();}};
 await context.renderKol(['kol','import-new']);
 const upload=all(root).find(n=>n.tag==='form'),file=all(upload).find(n=>n.type==='file');file.files=[{name:'合成.xlsx',size:30}];
 await upload.onsubmit({preventDefault(){}});
 const preview=calls.findLast(c=>c.path==='/api/kol-import/preview');assert.ok(preview.body.upload_id);assert.equal(preview.body.account_id,'a1');assert.equal(preview.body.content_base64,'UEs=');assert.equal(routes.at(-1),'kol/import/batch1');
 let release;const previousRequest=context.request;let pendingUploads=0;
 context.request=async(path,body)=>{if(path==='/api/kol-import/preview'){pendingUploads++;await new Promise(r=>release=r);return batch;}return previousRequest(path,body);};
 const pending=upload.onsubmit({preventDefault(){}});for(let i=0;i<100&&!pendingUploads;i++)await new Promise(r=>setImmediate(r));
 await upload.onsubmit({preventDefault(){}});assert.equal(pendingUploads,1,'busy upload must reject resubmission');
 const routeCount=routes.length,previousHash=context.location.hash;context.location.hash='#kol/list';release();await pending;assert.equal(routes.length,routeCount,'departed upload cannot navigate to stale result');context.request=previousRequest;context.location.hash=previousHash;
 file.files=[{name:'wrong.csv',size:30}];const before=calls.length;await upload.onsubmit({preventDefault(){}});assert.equal(calls.length,before);assert.match(text(root),/請選擇 .xlsx/);
 batch.errors=[];batch.state='confirmed';await context.renderKol(['kol','import','batch1']);assert.match(text(root),/匯入完成/);assert.ok(!all(root).some(n=>n.textContent==='確認匯入'));
 await context.renderKol(['kol','statistics']);assert.ok(calls.some(c=>c.path.includes('include_internal=false')));
 const internal=all(root).find(n=>n.name==='include_internal');internal.value='true';await all(root).find(n=>n.tag==='form').onsubmit({preventDefault(){}});assert.ok(calls.some(c=>c.path.includes('include_internal=true')));assert.ok(all(root).some(n=>n.href?.startsWith('/api/kols/export?')&&n.href.includes('include_internal=true')));
 console.log('KOL import list, preview, validation, confirmation, source and filters passed (DOM adapter, not a browser)');
})().catch(e=>{console.error(e);process.exitCode=1;});
