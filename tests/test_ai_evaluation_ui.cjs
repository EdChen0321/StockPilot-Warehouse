const assert=require('node:assert/strict');
const model=require('../web/ai-evaluation.js');
const rows=[{category:'庫存',variant:'fast',elapsed_ms:1000,execution_success:true,checks:{evidence:true,route:true,answer:null}},
{category:'訂單',variant:'model',elapsed_ms:500,execution_success:false,checks:{evidence:false,route:false,answer:null}},
{category:'庫存',variant:'fast',elapsed_ms:2000,execution_success:true,checks:{evidence:true,route:true,answer:true}}];
assert.equal(model.status(rows[0]),'review');
assert.equal(model.status(rows[1]),'fail');
assert.equal(model.select(rows,'庫存','fast','review').length,1);
assert.equal(model.aggregate(rows).reviewed,1);
assert.equal(model.aggregate(rows).p95,2000);
assert.equal(model.aggregate([]).mean,null);
console.log('Evaluation filtering, unknown reviews, latency and failure states passed');
