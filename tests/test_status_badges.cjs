const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const ctx=vm.createContext({node:(tag,text,className)=>({tag,text,className,children:[],append(...items){this.children.push(...items);}})});
vm.runInContext(fs.readFileSync('web/status.js','utf8'),ctx);
for(const label of ['草稿','準備中','完成','取消','有效','待履約','已入庫','出庫','未配櫃','調撥中','退貨入庫','良品','一般品','盒損','瑕疵','毀損','正常','啟用','停用','其他']){
 const cell=ctx.statusCell(label);assert.equal(cell.tag,'td');assert.equal(cell.children[0].text,label);assert.match(cell.children[0].className,/status-label status-(success|info|warning|danger|muted|neutral)/);
}
assert.equal(ctx.statusTone('退貨入庫'),'warning');assert.equal(ctx.statusTone('瑕疵／退貨入庫'),'danger');assert.equal(ctx.statusTone('入庫／盒損'),'warning');assert.equal(ctx.statusTone('未配櫃'),'warning');
const badge=ctx.statusBadge('<img src=x>');assert.equal(badge.text,'<img src=x>');
console.log('Product and document status badges retain labels and semantic backgrounds');
