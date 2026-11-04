'use strict';

const kolRecoveryKey='enterprise-ops:kol-import:recovery:v1';
let kolImportBusy=0;
window.addEventListener('beforeunload',event=>{if(kolImportBusy){event.preventDefault();event.returnValue='';}});
function kolRecoveryRead(){try{const value=JSON.parse(sessionStorage.getItem(kolRecoveryKey)||'null');return {record:value};}catch{return {record:null,unavailable:true};}}
function kolRecoverySave(record){try{sessionStorage.setItem(kolRecoveryKey,JSON.stringify(record));return true;}catch{return false;}}
function kolRecoveryClear(record){try{const state=kolRecoveryRead();if(state.unavailable)return false;const current=state.record;if(current&&((record.upload_id&&current.upload_id===record.upload_id)||(record.batch_id&&current.batch_id===record.batch_id)))sessionStorage.removeItem(kolRecoveryKey);return true;}catch{return false;}}
async function kolImportRequest(path,body){let timer;try{return await Promise.race([request(path,body),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('20 秒未收到回覆，結果未知。')),20000);})]);}finally{clearTimeout(timer);}}
async function kolFileHash(content){const bytes=Uint8Array.from(atob(content),c=>c.charCodeAt(0));const digest=await crypto.subtle.digest('SHA-256',bytes);return Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,'0')).join('');}
function kolRecoveryUnavailable(root){root.append(node('p','瀏覽器無法保存恢復記錄，不能自動恢復；請至匯入批次列表人工查詢。','kol-warning'),kolAction('查看匯入批次列表','kol/imports'));}
async function kolRecoveryCheck(record,notice,active){
 try{
  if(record.kind==='upload'){
   const result=await kolImportRequest('/api/kol-import/uploads/'+encodeURIComponent(record.upload_id));if(!active())return;
   if(result.state==='saved'){notice.textContent=result.batch_state==='confirmed'?'已完成匯入。':'上傳已保存，請查看預覽；尚未確認匯入。';kolRecoveryClear(record);navigatePage(kolImportRoute(result.batch_id));}
   else notice.textContent='結果待確認：目前尚未查到已保存結果，原請求仍可能處理中。可再次查詢，或重選相同檔案以同識別碼重試。';
  }else{
   const batch=await kolImportRequest('/api/kol-import/batches/'+encodeURIComponent(record.batch_id));if(!active())return;
   if(batch.state==='confirmed'){kolRecoveryClear(record);notice.textContent='已完成匯入。';navigatePage(kolImportRoute(batch.id));}
   else notice.textContent='上傳已保存，但尚未確認匯入；請開啟原批次查看並手動重試確認。';
  }
 }catch(error){if(active())notice.textContent=`結果未知：${error.message} 請再次查詢或至批次列表追查。`;}
}
async function kolRecoveryPanel(root,active,automatic=true){
 const state=kolRecoveryRead();if(state.unavailable){kolRecoveryUnavailable(root);return;}
 const record=state.record;if(!record)return;
 const section=node('section',undefined,'content-section'),notice=node('p','正在查詢前次操作結果…');notice.setAttribute('role','status');section.append(node('h3','恢復前次匯入操作'),node('p',record.kind==='upload'?`待追蹤檔案：${record.filename}`:`待確認批次：${record.batch_id}`),notice);
 const check=node('button','再次查詢'),cancel=node('button','取消恢復追蹤並開始新檔','secondary');check.type=cancel.type='button';
 check.onclick=async()=>{if(check.disabled)return;check.disabled=true;try{await kolRecoveryCheck(record,notice,active);}finally{check.disabled=false;}};
 cancel.onclick=()=>{if(!confirm('取消只停止恢復追蹤；原請求仍可能保存，且不會自動確認匯入。確定開始新檔？'))return;if(kolRecoveryClear(record)){if(active())renderKol(location.hash.slice(1).split('/'));}else notice.textContent='無法清除恢復追蹤，請至匯入批次列表追查。';};
 section.append(check,cancel);if(record.kind==='confirm')section.append(kolAction('查看待確認批次',kolImportRoute(record.batch_id)));root.append(section);
 if(automatic){check.disabled=true;try{await kolRecoveryCheck(record,notice,active);}finally{check.disabled=false;}}else notice.textContent=record.kind==='confirm'?'確認結果待確認，可再次查詢；不會自動確認匯入。':'結果待確認，可再次查詢或重選相同檔案重試；原請求仍可能保存。';
}

function kolImportRoute(id){return `kol/import/${encodeURIComponent(id)}`;}
function kolImportState(batch){return batch.state==='confirmed'?'已完成匯入':batch.errors?.length?'預覽有錯誤，尚未匯入':'上傳已保存，預覽尚未確認匯入';}
function kolImportSource(id){return boot.accounts.find(a=>a.id===id)?.name||id;}
function kolFileBase64(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('無法讀取檔案，請重新選取。'));reader.readAsDataURL(file);});}
async function renderKolImport(root,parts,kols,active){
 const page=parts[1];
 if(page==='imports'){
  await kolRecoveryPanel(root,active);if(!active())return;
  const {batches}=await request('/api/kol-import/batches');if(!active())return;
  root.append(node('h3','Excel 匯入批次'),kolAction('新增 Excel 匯入','kol/import-new'));
  kolPaged(root,['批次','來源','檔名','狀態','訂單數','建立時間'],batches.map(b=>[kolLink(b.id,kolImportRoute(b.id)),kolImportSource(b.account_id),b.filename,kolImportState(b),b.summary.order_count,kolTime(b.created_at)]),'imports');return;
 }
 if(page==='import-new'){
  await kolRecoveryPanel(root,active);if(!active())return;
  root.append(node('h3','新增 Excel 匯入'),kolAction('返回匯入列表','kol/imports'));
  if(!boot.accounts.length){const a=node('a','先新增訂單來源');a.href='#sources';root.append(node('p','尚無訂單來源，請先至來源管理建立。'),a);return;}
  const form=node('form',undefined,'kol-editor');root.append(form);
  const pendingUpload=kolRecoveryRead().record;
  const defaultSource=pendingUpload?.kind==='upload'&&boot.accounts.some(a=>a.id===pendingUpload.account_id)?pendingUpload.account_id:kolFilters.account_id||boot.accounts[0].id;
  const source=kolSelect(form,'訂單來源','account_id',boot.accounts.map(a=>[a.id,a.name]),defaultSource);
  const label=node('label','訂單 Excel（.xlsx，最多 2 MB）'),file=node('input');file.type='file';file.accept='.xlsx';file.required=true;label.append(file);form.append(label,node('p','一般匯入只需一份訂單 Excel，直接讀取商品貨號、名稱與類型，不需另外提供商品表。先預覽再確認；本入口只建立分析資料，不建立庫存、出貨單或營運商品主檔。','muted'));
  const submit=node('button','上傳並預覽');submit.type='submit';const notice=node('p');notice.setAttribute('role','status');form.append(submit,notice);
  form.onsubmit=async e=>{e.preventDefault();if(submit.disabled)return;submit.disabled=true;kolImportBusy++;notice.textContent='讀取並驗證檔案中…';try{
   const selected=file.files?.[0];if(!selected||!selected.name.toLowerCase().endsWith('.xlsx'))throw new Error('請選擇 .xlsx 檔案。');if(selected.size>2000000)throw new Error('檔案超過 2 MB。');
   const content_base64=await kolFileBase64(selected),file_hash=await kolFileHash(content_base64);if(!active())return;
   const previous=kolRecoveryRead().record;
   if(previous&&(previous.kind!=='upload'||previous.file_hash!==file_hash||previous.account_id!==source.value||previous.filename!==selected.name))throw new Error('另有待追蹤操作；請重選相同來源及檔案，或先明確取消恢復追蹤。');
   const record=previous||{kind:'upload',upload_id:crypto.randomUUID(),account_id:source.value,filename:selected.name,file_hash};
   if(!kolRecoverySave(record))kolRecoveryUnavailable(form);
   try{const batch=await kolImportRequest('/api/kol-import/preview',{account_id:source.value,filename:selected.name,content_base64,upload_id:record.upload_id});kolRecoveryClear(record);if(active()){dirtyForms.delete(form);pageDirty=dirtyForms.size>0;navigatePage(kolImportRoute(batch.id));}}
   catch(error){if(active()){notice.textContent=`結果未知：${error.message}`;await kolRecoveryCheck(record,notice,active);if(active()&&kolRecoveryRead().record)await kolRecoveryPanel(form,active,false);}}
  }catch(error){if(active())notice.textContent=error.message;}finally{kolImportBusy=Math.max(0,kolImportBusy-1);submit.disabled=false;}};return;
 }
 const recoveryRoot=node('div');root.append(recoveryRoot);
 const showConfirmRecovery=async()=>{recoveryRoot.replaceChildren();await kolRecoveryPanel(recoveryRoot,active,false);};
 const beforeRead=kolRecoveryRead().record;
 if(beforeRead?.kind==='confirm'&&beforeRead.batch_id===decodeURIComponent(parts[2]||''))await showConfirmRecovery();
 const batch=await request('/api/kol-import/batches/'+encodeURIComponent(decodeURIComponent(parts[2]||'')));if(!active())return;
 const pending=kolRecoveryRead().record;if(batch.state==='confirmed'&&pending?.kind==='confirm'&&pending.batch_id===batch.id)kolRecoveryClear(pending);
 if(batch.state==='confirmed')recoveryRoot.remove();
 root.append(node('h3',`匯入批次 ${batch.id}`),kolAction('返回匯入列表','kol/imports'),kolAction('重新上傳','kol/import-new'));
 root.append(kolTable(['項目','內容'],[['檔名',batch.filename],['來源',kolImportSource(batch.account_id)],['狀態',kolImportState(batch)],['建立時間',kolTime(batch.created_at)],['商品辨識',batch.product_source==='order_file'?'直接使用本份訂單的 SKU、名稱與類型':'使用另行設定的商品對照表']]));
 const s=batch.summary;root.append(kolTable(['原始列','清洗明細數','訂單數','商品數','排除數','訂單總金額'],[[s.raw_row_count,s.clean_line_count,s.order_count,s.product_count,s.excluded_count,kolMoney(s.order_total)]]));
 const changes=batch.changes||s.changes;
 if(changes){root.append(kolTable(['將新增','將更新','相同略過','將排除'],[[changes.new,changes.updated,changes.unchanged,changes.excluded]]),node('p','同一來源及訂單號的內容更新會以本次 Excel 全單替換，舊匯入批次保留供追查。','muted'));}
 for(const warning of batch.warnings||[])root.append(node('p',warning,'kol-warning'));
 if(batch.errors?.length){root.append(node('h3','必須處理的錯誤'),node('p','本批含錯誤，無法確認；請修正檔案後重新上傳。','notice error'));kolPaged(root,['來源列','訂單','錯誤'],batch.errors.map(e=>[e.row_index,e.order_id,e.message]),'import-errors/'+batch.id);}
 if(batch.state==='confirmed')root.append(node('p','匯入完成，可查看 KOL 分析。','notice'),kolAction('查看表現統計','kol/statistics'));
 else{
  const confirm=node('button','確認匯入'),notice=node('p');notice.setAttribute('role','status');confirm.disabled=!batch.can_confirm||!!batch.errors?.length;root.append(confirm,notice);
  confirm.onclick=async()=>{if(confirm.disabled)return;confirm.disabled=true;const record={kind:'confirm',batch_id:batch.id},previous=kolRecoveryRead().record;
   if(previous&&(previous.kind!=='confirm'||previous.batch_id!==batch.id)){notice.textContent='另有待追蹤操作，請先至匯入列表查詢或取消恢復追蹤。';confirm.disabled=false;return;}
   if(!kolRecoverySave(record))kolRecoveryUnavailable(root);kolImportBusy++;
   try{const result=await kolImportRequest('/api/kol-import/confirm',{batch_id:batch.id});if(result.state==='confirmed')kolRecoveryClear(record);if(active())return renderKol(parts);}
   catch(error){if(active()){notice.textContent=`確認結果未知：${error.message}`;await kolRecoveryCheck(record,notice,active);if(active()&&kolRecoveryRead().record)await showConfirmRecovery();}}
   finally{kolImportBusy--;confirm.disabled=!batch.can_confirm||!!batch.errors?.length;}};
 }
 root.append(node('h3','UTM 對應'),node('p',`無 UTM：${batch.no_utm_count} 筆訂單。確認匯入後固定歸入一般訂單，與 KOL 並列。`),kolAction('查看一般訂單','kol/detail/general'));
 for(const unmatched of batch.unmatched_utms||[]){
  const section=node('section',undefined,'content-section');section.append(node('p',`未對應 UTM：${unmatched.utm_medium}（${unmatched.order_count} 筆訂單）`));
  const extra=new URLSearchParams({return_batch:batch.id,append_utm:unmatched.utm_medium});
  const select=kolSelect(section,'追加至既有 KOL','kol_id',[['','請選擇 KOL'],...kols.map(k=>[k.id,k.name])],'');
  const edit=node('a','編輯所選 KOL 並追加 UTM');edit.hidden=true;select.onchange=()=>{edit.hidden=!select.value;edit.href='#kol/edit/'+encodeURIComponent(select.value)+'?'+extra;};
  const create=node('a','建立新 KOL 並對應');create.href='#kol/new?'+extra;section.append(edit,create);root.append(section);
 }
 if(!batch.unmatched_utms?.length)root.append(node('p','沒有待對應的 UTM。'));
 root.append(node('h3','訂單與清洗明細'),node('p','商品統計以數量為主；金額依原始完整訂單計算，每張訂單只計一次。','muted'));
 kolPaged(root,['訂單','下單時間','狀態','訂單總金額','UTM','內部訂單','SKU','商品','數量','商品明細金額','原始貨號','來源列','拆分序號'],batch.orders.flatMap(o=>o.lines.map(l=>[o.order_id,kolTime(o.ordered_at),kolOrderStatus(o.status),kolMoney(o.total),o.utm_medium||'—',o.internal?'是':'否',l.sku,l.name,l.quantity,kolLineMoney(l.amount),l.original_sku,l.row_index,l.component_index])),'import-lines/'+batch.id);
 root.append(node('h3','排除明細'));
 kolPaged(root,['來源列','訂單','原始貨號','拆分序號','排除原因'],batch.exclusions.map(e=>[e.row_index,e.order_id,e.original_sku,e.component_index,e.reason]),'import-exclusions/'+batch.id);
}
