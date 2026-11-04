const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
class Element{
 constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 focus(){} scrollIntoView(){}
}
const panel=new Element('section');
const node=(tag,text='',cls='')=>Object.assign(new Element(tag,text),{className:cls});
const context=vm.createContext({location:{},labels:{draft:'預覽',confirmed:'完成'},$:()=>panel,node,describe:x=>x,
 button:(label,click)=>Object.assign(node('button',label),{click}),emptyRow(){},openBatch(){},
 request:async()=>{},refreshBootstrap:async()=>{},refreshSales:async()=>{},announce(){}});
const app=fs.readFileSync('web/app.js','utf8');
vm.runInContext(app.slice(app.indexOf('function renderBatch('),app.indexOf('function filters(')),context);
const all=n=>[n,...n.children.flatMap(all)];
const batch={id:'b1',filename:'orders.xlsx',account_id:'s1',format:'xlsx',state:'draft',can_confirm:true,
 new_products:[{sku:'WB-001',name:'水壺',group_name:'一般水壺'}],results:[{order_id:'O1',revision:1,status:'active',outcome:'ready',errors:[],warnings:[]}],raw_rows:[]};
context.renderBatch(batch);
assert.ok(all(panel).some(n=>n.tag==='li'&&n.textContent.includes('WB-001')));
assert.equal(all(panel).find(n=>n.tag==='button'&&n.textContent==='確認建立商品並匯入訂單').disabled,false);
context.renderBatch({...batch,can_confirm:false});
assert.equal(all(panel).find(n=>n.tag==='button'&&n.textContent==='確認建立商品並匯入訂單').disabled,true);
context.renderBatch({...batch,state:'confirmed'});
assert.equal(all(panel).find(n=>n.tag==='a'&&n.textContent==='查看接收訂單').href,'#orders');
const html=fs.readFileSync('web/index.html','utf8');
assert.match(html, /<input id="import-account" type="hidden" value="">/);
assert.doesNotMatch(app, /options\('import-account'/);
assert.match(html,/<input id="import-file"[^>]*accept="[^"]*\.xlsx/);
assert.ok(html.indexOf('/order-upload.js')<html.indexOf('/app.js'));
assert.ok(app.includes('await operationalImportBody('));
console.log('Operational Excel product preview, blocked confirmation and orders handoff passed (DOM adapter)');
