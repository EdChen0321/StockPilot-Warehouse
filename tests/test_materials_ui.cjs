// Rendering and actions only; this DOM adapter does not validate browser layout.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag,text=''){this.tag=tag;this.textContent=String(text??'');this.children=[];this.value='';this.dataset={};this.isConnected=true;this.events={};this.className='';this.classList={add:(v)=>this.className+=' '+v};}
 append(...children){for(const c of children){c.parent=this;this.children.push(c);}}
 prepend(...children){for(const c of children)c.parent=this;this.children.unshift(...children);}
 replaceChildren(...children){this.children=[];this.append(...children);}
 insertBefore(child,ref){child.parent=this;this.children.splice(this.children.indexOf(ref),0,child);}
 setAttribute(k,v){this[k]=v;}
 addEventListener(k,fn){this.events[k]=fn;}
 get options(){return this.children;}
}
const node=(tag,text,cls)=>Object.assign(new Element(tag,text),{className:cls||''});
const all=e=>[e,...e.children.flatMap(all)],text=e=>all(e).map(x=>x.textContent).join(' ');
const ids={},get=id=>ids[id]??=node('div');
const line={material_id:'m',quantity:'1.5',unit:'公尺',ordered:'10',received:'4',required:'6',reserved:'5',issued:'1',returned:'0'};
const data={materials:[{id:'m',name:'合成布',code:'CLOTH',supplier_id:'s',on_hand:'8',unit:'公尺',active:1}],
 suppliers:[{id:'s',name:'合成廠',active:1}],boms:[{product_id:'p',version:1,lines:[line]}],
 plans:[{id:'plan',number:'PLAN-1',product_id:'p',quantity:4,bom_version:1,version:1,state:'confirmed',lines:[line]}],
 purchases:[{id:'po',number:'PO-1',supplier_id:'s',state:'partial',lines:[line]}],
 movements:[{id:'issue',number:'ISSUE-1',plan_id:'plan',kind:'issue',time:'2026-10-30',lines:[{...line,quantity:'1',before:'9',after:'8'}]}],plan_versions:[]};
const calls=[],routes=[];
const context=vm.createContext({node,$:get,Option:function(t,v){return Object.assign(node('option',t),{value:v});},
 document:{createTextNode:t=>node('text',t)},URLSearchParams,Event:class{},
 location:{hash:'#materials/materials'},window:{addEventListener(){},dispatchEvent(){}},
 request:async(path,body)=>{calls.push({path,body});return path.startsWith('/api/materials/summary')?{plan_ids:['plan'],materials:[{name:'合成布',code:'CLOTH',demand:'6',selected_reserved:'5',available:'3',shortage:'0',incoming:'6',unit:'公尺'}]}:data;},
 stockButton:(area,label,work)=>{const b=node('button',label);b.type='button';b.onclick=work;return b;},
 stockFacts:entries=>{const dl=node('dl',undefined,'document-facts');for(const [k,v] of entries){const d=node('div');d.append(node('dt',k),node('dd',v));dl.append(d);}return dl;},
 stockAction:async(area,button,work)=>work(),stockMessage(){},operation:()=> 'test-operation',
 navigatePage:path=>routes.push(path),productName:()=> '合成提袋',dateTime:v=>v,documentMatches:()=>true,
 boot:{products:[{id:'p',name:'合成提袋',sku:'BAG',active:true,kind:'product'}]}});
vm.runInContext(fs.readFileSync('web/status.js','utf8'),context);
vm.runInContext(fs.readFileSync('web/materials.js','utf8'),context);
(async()=>{
 await context.refreshMaterials();
 const tabs=all(get('material-tabs')).filter(n=>n.tag==='a');
 assert.equal(tabs.length,6);assert.equal(tabs.filter(n=>n['aria-current']==='page').length,1);
 assert.equal(tabs[0].href,'#materials/materials');assert.match(text(get('material-list')),/合成布/);
 vm.runInContext("materialTab='purchases';showMaterialRecord('po')",context);
 const detail=get('material-detail'),actions=all(detail).find(n=>n.className==='document-actions');
 assert.deepEqual(actions.children.map(n=>n.textContent),['登記本次收貨','取消未收部分']);
 assert.match(actions.children[1].className,/danger/);
 assert.ok(all(detail).some(n=>n.tag==='td'&&text(n).trim()==='6'),'remaining quantity 10 - 4');
 assert.ok(all(detail).some(n=>n.tag==='th'&&n.textContent==='待收'&&n.className==='numeric'));
 assert.match(text(detail),/合成廠/);assert.match(text(detail),/尚無實物異動/);
 await actions.children[0].onclick();assert.match(text(detail),/最多 6 公尺/);
 vm.runInContext("materialTab='plans';showMaterialRecord('plan')",context);
 assert.match(text(detail),/異動前/);assert.match(text(detail),/異動後/);
 const planActions=all(detail).find(n=>n.className==='document-actions');
 assert.deepEqual(planActions.children.map(n=>n.textContent),['登記發料','取消未發需求']);
 await planActions.children[1].onclick();assert.ok(calls.some(c=>c.path==='/api/materials/plans/plan/cancel'));
 await context.renderMaterialSummary();assert.match(text(get('material-summary')),/PLAN-1.*合成提袋/);
 const check=all(get('material-summary')).find(n=>n.type==='checkbox');check.checked=true;await check.events.change();
 assert.ok(calls.some(c=>c.path.includes('plan_id=plan')));
 vm.runInContext("materialTab='boms';editBom('p')",context);assert.match(text(detail),/每件用量/);
 data.plans.push({id:'factory-plan',number:'FACTORY-1',mode:'reported',factory_id:'s',factory_name:'產品工廠',
   product_id:'p',quantity:100,received:40,pending:60,state:'partial',version:2,lines:[],receipts:[],purchase_ids:[]});
 vm.runInContext("materialTab='plans';showMaterialRecord('factory-plan')",context);
 assert.match(text(detail),/產品工廠/);assert.match(text(detail),/已收成品/);assert.match(text(detail),/60/);
 const productionActions=all(detail).find(n=>n.className==='document-actions');
 assert.ok(productionActions.children.some(n=>n.textContent==='登記本批成品實收'));
 assert.ok(productionActions.children.some(n=>n.textContent==='登記實際用料'));
 assert.ok(!productionActions.children.some(n=>n.textContent==='確認並預留'));
 await productionActions.children.find(n=>n.textContent==='登記本批成品實收').onclick();
 assert.match(text(detail),/最多 60 件/);assert.match(text(detail),/入庫單/);
 // Submit through the actual form, including a committed request whose stock refresh fails.
 let lastAction,refreshFails=true;
 context.stockAction=(area,button,work)=>(lastAction=work());
 context.refreshStock=async()=>{if(refreshFails){refreshFails=false;throw new Error('refresh failed');}};
 const originalRequest=context.request;
 context.request=async(path,body)=>{
   if(path.endsWith('/receive')){calls.push({path,body});return {...data.plans.at(-1),received:50,pending:50,version:3};}
   return originalRequest(path,body);
 };
 const form=all(detail).find(n=>n.tag==='form'),inputs=all(form).filter(n=>n.tag==='input');
 inputs[0].value='IN-EDGE';inputs[1].value='10';
 form.events.submit({preventDefault(){}});await assert.rejects(lastAction,/refresh failed/);
 form.events.submit({preventDefault(){}});await lastAction;
 const attempts=calls.filter(c=>c.path.endsWith('/receive'));
 assert.equal(attempts.length,2);assert.deepEqual(attempts[0].body,attempts[1].body);
 assert.equal(attempts[0].body.quantity,10);assert.equal(attempts[0].body.expected_version,2);
 assert.equal(attempts[0].body.number,'IN-EDGE');
 const factoryPlan=data.plans.at(-1);
 for(const state of ['draft','cancelled','received']){
   factoryPlan.state=state;vm.runInContext("showMaterialRecord('factory-plan')",context);
   const labels=all(detail).filter(n=>n.tag==='button').map(n=>n.textContent);
   assert.ok(!labels.includes('登記本批成品實收'));
   assert.equal(labels.includes('登記實際用料'),state==='received');
   assert.equal(labels.includes('確認生產下單'),state==='draft');
 }
 console.log('Material tabs, remaining quantities, grouped actions, history, summary filter and BOM rendering passed (DOM adapter).');
})().catch(error=>{console.error(error);process.exitCode=1;});
