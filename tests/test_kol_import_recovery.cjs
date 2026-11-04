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


const stored=new Map(),key='enterprise-ops:kol-import:recovery:v1';
const storage={getItem:k=>stored.get(k)||null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)};
const batch={id:'b1',state:'preview',can_confirm:true,summary:{},orders:[],errors:[],warnings:[],exclusions:[],unmatched_utms:[],no_utm_count:0};
function app(handle,session=storage,timers={setTimeout,clearTimeout}){
 const root=new Element('section'),routes=[],events={},calls=[];
 const c=vm.createContext({sessionStorage:session,atob,Uint8Array,setTimeout:timers.setTimeout,clearTimeout:timers.clearTimeout,Node:Element,URLSearchParams,Date,JSON,crypto:require('node:crypto').webcrypto,
 node:(tag,t,cl)=>Object.assign(new Element(tag,t),{className:cl||''}),$:()=>root,formatAmount:v=>v,boot:{accounts:[{id:'a1',name:'店'}]},
 Option:function(t,v){return Object.assign(new Element('option',t),{value:v});},
 request:async(path,body)=>{calls.push({path,body});return path==='/api/kols'?{kols:[]}:path==='/api/kol-import/batches'?{batches:[]}:handle(path,body);},
 navigatePage:p=>routes.push(p),confirm:()=>true,dirtyForms:new Set(),pageDirty:false,location:{hash:'#kol/import-new'},
 window:{addEventListener:(name,fn)=>events[name]=fn},history:{replaceState(){}},FileReader:class{readAsDataURL(file){this.result='data:;base64,'+(file.bytes||'UEs=');this.onload();}}
 });
 vm.runInContext(fs.readFileSync('web/kol.js','utf8'),c);vm.runInContext(fs.readFileSync('web/kol-import.js','utf8'),c);
 return {c,root,routes,calls,events,async render(page='import-new'){c.location.hash='#kol/'+page;await c.renderKol(('kol/'+page).split('/'));},async upload(bytes='UEs=',name='same.xlsx'){const form=all(root).find(n=>n.tag==='form');all(form).find(n=>n.type==='file').files=[{name,size:2,bytes}];await form.onsubmit({preventDefault(){}});}};
}
(async()=>{
 let receipt=false;
 const first=app(async(path,body)=>{if(path.endsWith('/preview')){receipt=true;throw Error('回應遺失');}if(path.includes('/uploads/'))return {state:'saved',batch_id:'b1',batch_state:'preview'};return batch;});
 await first.render();await first.upload();assert.equal(first.routes.at(-1),'kol/import/b1');assert.ok(!stored.has(key));assert.ok(receipt);
 // Rebuild the entire VM, sharing only sessionStorage, as a refresh would.
 const record={kind:'upload',upload_id:require('node:crypto').randomUUID(),account_id:'a1',filename:'same.xlsx',file_hash:require('node:crypto').createHash('sha256').update(Buffer.from('UEs=','base64')).digest('hex')};
 stored.set(key,JSON.stringify(record));let checks=0;
 const reloaded=app(async(path,body)=>{if(path.includes('/uploads/')){checks++;return {state:'not_found'};}if(path.endsWith('/preview'))return batch;return batch;});
 await reloaded.render();assert.equal(checks,1);assert.match(text(reloaded.root),/結果待確認/);assert.ok(stored.has(key));
 await all(reloaded.root).find(n=>n.textContent==='再次查詢').onclick();assert.equal(checks,2);
 await reloaded.upload('QUJD');assert.match(text(reloaded.root),/重選相同/);assert.equal(JSON.parse(stored.get(key)).upload_id,record.upload_id);assert.ok(!reloaded.calls.some(c=>c.body));
 await reloaded.upload();assert.equal(reloaded.calls.find(c=>c.body).body.upload_id,record.upload_id);assert.ok(!stored.has(key));
 const secondSource={...record,account_id:'a2'};stored.set(key,JSON.stringify(secondSource));
 const sourceReload=app(async path=>path.includes('/uploads/')?{state:'not_found'}:batch);sourceReload.c.boot.accounts.push({id:'a2',name:'另一來源'});
 await sourceReload.render();assert.equal(all(sourceReload.root).find(n=>n.name==='account_id').value,'a2');await sourceReload.upload();assert.equal(sourceReload.calls.find(c=>c.body).body.account_id,'a2');assert.equal(sourceReload.calls.find(c=>c.body).body.upload_id,record.upload_id);
 stored.set(key,JSON.stringify(record));const recovered=app(async()=>({state:'saved',batch_id:'b1',batch_state:'preview'}));await recovered.render('imports');assert.equal(recovered.routes.at(-1),'kol/import/b1');
 stored.set(key,JSON.stringify(record));const cancelled=app(async()=>({state:'not_found'}));await cancelled.render();await all(cancelled.root).find(n=>n.textContent==='取消恢復追蹤並開始新檔').onclick();assert.ok(!stored.has(key));
 const confirming=app(async(path)=>{if(path.endsWith('/confirm')){batch.state='confirmed';throw Error('lost');}return batch;});batch.state='preview';await confirming.render('import/b1');await all(confirming.root).find(n=>n.textContent==='確認匯入').onclick();assert.equal(confirming.routes.at(-1),'kol/import/b1');assert.ok(!stored.has(key));
 batch.state='preview';stored.set(key,JSON.stringify({kind:'confirm',batch_id:'b1'}));const notConfirmed=app(async()=>batch);await notConfirmed.render();assert.match(text(notConfirmed.root),/尚未確認/);assert.ok(stored.has(key));assert.ok(!notConfirmed.calls.some(c=>c.body));
 const detail=app(async()=>{throw Error('查詢中斷');});await detail.render('import/b1');assert.match(text(detail.root),/查詢中斷/);assert.equal(all(detail.root).filter(n=>n.textContent==='再次查詢').length,1);
 detail.c.request=async()=>({...batch,state:'confirmed'});await all(detail.root).find(n=>n.textContent==='再次查詢').onclick();assert.equal(detail.routes.at(-1),'kol/import/b1');assert.ok(!stored.has(key));
 let detailReads=0;const lostConfirm=app(async path=>{if(path.endsWith('/confirm'))throw Error('lost confirm');if(++detailReads>1)throw Error('GET lost');return batch;});
 await lostConfirm.render('import/b1');await all(lostConfirm.root).find(n=>n.textContent==='確認匯入').onclick();assert.equal(all(lostConfirm.root).filter(n=>n.textContent==='再次查詢').length,1);assert.match(text(lostConfirm.root),/GET lost/);
 await all(lostConfirm.root).find(n=>n.textContent==='確認匯入').onclick();assert.equal(all(lostConfirm.root).filter(n=>n.textContent==='再次查詢').length,1,'repeated lost confirmation must reuse the recovery panel');
 lostConfirm.c.request=async()=>({...batch,state:'confirmed'});await all(lostConfirm.root).find(n=>n.textContent==='再次查詢').onclick();assert.ok(!stored.has(key));
 stored.clear();const broken=app(async()=>batch,{getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}});await broken.render();assert.match(text(broken.root),/不能自動恢復/);await broken.upload();assert.ok(broken.calls.some(c=>c.body?.upload_id));
 let release;const leaving=app(async(path)=>{if(path.endsWith('/preview'))return new Promise(r=>release=r);return batch;});await leaving.render();const pending=leaving.upload();for(let i=0;i<200&&!release;i++)await new Promise(r=>setImmediate(r));let warned=false;leaving.events.beforeunload({preventDefault(){warned=true;}});assert.ok(warned);leaving.c.location.hash='#orders';release(batch);await pending;assert.equal(leaving.routes.length,0);

 stored.clear();let expire,started=false,cleared=0;
 const timeout=app(async path=>{if(path.endsWith('/preview')){started=true;return new Promise(()=>{});}return {state:'not_found'};},storage,{setTimeout:fn=>{expire=fn;return 1;},clearTimeout:()=>cleared++});
 await timeout.render();const timed=timeout.upload();for(let i=0;i<200&&!started;i++)await new Promise(r=>setImmediate(r));expire();await timed;
 assert.ok(stored.has(key));assert.match(text(timeout.root),/結果待確認/);assert.equal(timeout.calls.filter(c=>c.path.includes('/uploads/')).length,1);assert.ok(cleared>=2);assert.ok(all(timeout.root).some(n=>n.textContent==='再次查詢'));
 assert.deepEqual(Object.keys(JSON.parse(stored.get(key))).sort(),['kind','upload_id','account_id','filename','file_hash'].sort());
 console.log('KOL refresh, lost response, same-file retry, pending protection, cancellation, confirm and unavailable storage recovery passed (VM only)');
})().catch(e=>{console.error(e);process.exitCode=1;});
