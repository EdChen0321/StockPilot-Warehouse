const assert=require('node:assert/strict');
const {operationalImportBody}=require('../web/order-upload.js');
function file(name,bytes){return {name,size:bytes.length,arrayBuffer:async()=>Uint8Array.from(bytes).buffer};}
(async()=>{
 const body=await operationalImportBody(file('orders.XLSX',[0x50,0x4b,0,255]),'source',true);
 assert.equal(body.content_base64,'UEsA/w==');assert.equal(body.csv_text,undefined);
 assert.equal(body.account_id,'source');assert.equal(body.complete_snapshot,true);
 const csv=await operationalImportBody(file('orders.csv',Buffer.from('order_id\n001')),'source',true);
 assert.equal(csv.csv_text,'order_id\n001');assert.equal(csv.content_base64,undefined);
 await assert.rejects(()=>operationalImportBody(file('bad.xls',[1]),'source',true));
 await assert.rejects(()=>operationalImportBody({name:'big.xlsx',size:2000001},'source',true));
 await assert.rejects(()=>operationalImportBody(file('bad.csv',[255]),'source',true));
 console.log('Operational CSV/XLSX binary payload and unchanged size guard passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
