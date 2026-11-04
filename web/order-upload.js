'use strict';
async function operationalImportBody(file,accountId,completeSnapshot){
 if(!file||!(/\.(csv|xlsx)$/i).test(file.name))throw new Error('請選擇訂單 Excel（.xlsx）或標準 CSV 檔案。');
 if(file.size>2_000_000)throw new Error('目前每個檔案上限為 2 MB。');
 const body={account_id:accountId,filename:file.name,complete_snapshot:completeSnapshot};
 const bytes=new Uint8Array(await file.arrayBuffer());
 if(file.name.toLowerCase().endsWith('.xlsx')){
  let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
  body.content_base64=btoa(binary);
 }else body.csv_text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);
 return body;
}
if(typeof module!=='undefined'&&module.exports)module.exports={operationalImportBody};
