'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const elements=new Map();
function element(tag,text){return {tag,text,children:[],append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},closest(){return {after(item){elements.set(item.id,item);}};}};}
elements.set('rows',element('tbody'));
const context=vm.createContext({Map,Math,$:id=>elements.get(id),node:element,button:(text,handler)=>({...element('button',text),handler})});
const source=fs.readFileSync('web/app.js','utf8');
vm.runInContext(source.slice(source.indexOf('const listPages ='),source.indexOf('\nfunction button(')),context);
const all=Array.from({length:5124},(_,i)=>i);
context.all=all;
vm.runInContext("var result;function draw(){result=pageItems('rows',all,draw,'all');}draw();",context);
assert.equal(context.result.length,50);
assert.equal(context.result[0],0);
(async()=>{
 await elements.get('rows-pager').children[2].handler();
 assert.equal(context.result[0],50);
 vm.runInContext("result=pageItems('rows',[1,2,3],draw,'filtered');",context);
 assert.equal(context.result.length,3);
 assert.equal(elements.get('rows-pager').children[1].disabled,true);
 assert.equal(elements.get('rows-pager').children[2].disabled,true);
 assert.equal(all.length,5124);
 console.log('paging: 5124 records, next page and filter reset passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
