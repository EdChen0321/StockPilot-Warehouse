'use strict';

const $ = (id) => document.getElementById(id);
let boot = { accounts: [], products: [], batches: [], mappings: [], samples: [] };
let bootstrapReady=false;
let activeBatch = null;
let querySequence = 0;
const labels = {
  draft: '預檢草稿', confirmed: '已確認', pending: '待確認', ready: '可匯入',
  applied: '已生效',
  valid: '可匯入', accepted: '已納入', imported: '已匯入', inserted: '已新增',
  updated: '已更新', duplicate: '重複，未新增', unchanged: '內容相同',
  stale: '舊版本，不覆蓋', conflict: '版本衝突', invalid: '資料待修正',
  cancelled: '已取消', error: '待修正', new: '新訂單', revision: '新版訂單',
};

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = String(text);
  if (className) element.className = className;
  return element;
}

function announce(message, isError = false) {
  $('notice').textContent = message;
  $('notice').classList.toggle('error', isError);
}

async function request(path, body) {
  const options = body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'operations-ui' },
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : '操作未完成，請檢查輸入後重試。');
  return data;
}

async function action(button, work) {
  const original = button.textContent;
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  button.textContent = '處理中…';
  try { await work(); } catch (error) { announce(error.message || '無法連線，請確認本機服務已啟動。', true); }
  finally { button.disabled = false; button.removeAttribute('aria-busy'); button.textContent = original; }
}

function options(id, items, prompt) {
  const select = $(id);
  const previous = select.value;
  select.replaceChildren();
  if (prompt !== undefined) select.append(new Option(prompt, ''));
  for (const item of items) select.append(new Option(item.label, item.value));
  if ([...select.options].some((option) => option.value === previous)) select.value = previous;
}

function emptyRow(body, columns, message) {
  const row = node('tr');
  const cell = node('td', message, 'empty');
  cell.colSpan = columns;
  row.append(cell);
  body.append(row);
}

const listPages = new Map();
function pageItems(bodyId, items, render, filterKey='') {
  const previous=listPages.get(bodyId);
  const pages=Math.max(1,Math.ceil(items.length/50));
  const page=Math.min(previous?.filterKey===filterKey?previous.page:1,pages);
  listPages.set(bodyId,{page,filterKey});
  let pager=$(bodyId+'-pager');
  if(!pager){pager=node('div',undefined,'form-actions');pager.id=bodyId+'-pager';const body=$(bodyId);(body.closest('.table-scroll')||body.closest('table')).after(pager);}
  pager.replaceChildren(node('span',`${items.length} 筆 · 第 ${page} / ${pages} 頁`));
  for(const [label,step] of [['上一頁',-1],['下一頁',1]]){
    const control=button(label,async()=>{listPages.set(bodyId,{page:page+step,filterKey});await render();});
    control.disabled=page+step<1||page+step>pages;pager.append(control);
  }
  return items.slice((page-1)*50,page*50);
}

function button(label, handler) {
  const element = node('button', label, 'table-button');
  element.type = 'button';
  element.addEventListener('click', () => action(element, handler));
  return element;
}

function describe(value) { return typeof value === 'string' ? value : JSON.stringify(value); }
function dateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-TW', { timeZone: 'Asia/Taipei', hour12: false });
}

function renderSamples() {
  $('samples').replaceChildren();
  for (const sample of boot.samples || []) {
    const item = node('div', undefined, 'sample-item');
    item.append(node('span', sample.label || sample.filename));
    const download = node('a', '下載 CSV');
    download.href = `/samples/${encodeURIComponent(sample.filename)}`;
    download.download = sample.filename;
    const preview = button('載入範例預檢', async () => {
      if (!boot.accounts.some((account) => account.id === sample.account_id)) throw new Error('請先初始化示範主檔。');
      const response = await fetch(download.href);
      if (!response.ok) throw new Error('範例檔案無法讀取。');
      const csv = await response.text();
      $('import-account').value = sample.account_id;
      const batch = await request('/api/imports', { account_id: sample.account_id, filename: sample.filename, csv_text: csv, complete_snapshot: true });
      await refreshBootstrap();
      renderBatch(batch);
      announce('合成範例預檢完成；確認後才納入銷售。');
    });
    item.append(download, preview);
    $('samples').append(item);
  }
}


function renderHistory() {
  const body = $('history-rows');
  body.replaceChildren();
  for (const batch of pageItems('history-rows',boot.batches||[],renderHistory)) {
    const row = node('tr');
    const name = node('td', batch.filename);
    name.append(node('small', batch.id));
    const account = (boot.accounts || []).find(entry => entry.id === batch.account_id);
    row.append(name, node('td', account ? (account.name || account.platform || batch.account_id) : batch.account_id), node('td', dateTime(batch.created_at)), node('td', labels[batch.state] || batch.state));
    const controls = node('td');
    controls.append(button('查看批次', () => openBatch(batch.id)));
    row.append(controls);
    body.append(row);
  }
  if (!body.children.length) emptyRow(body, 5, '尚無匯入紀錄，請從「匯入銷售訂單」上傳訂單檔案。');
}

async function refreshBootstrap() {
  boot = await request('/api/bootstrap?summary=1');
  const accounts = boot.accounts.map((entry) => ({ value: entry.id, label: `${entry.platform} · ${entry.name || entry.id}` }));
  const products = boot.products.map((entry) => ({ value: entry.id, label: `${entry.name} · ${entry.sku || entry.id}` }));
  options('filter-account', accounts, '全部來源');
  options('filter-product', products, '全部品項');
  renderSamples(); renderHistory();
  $('demo-setup').hidden = boot.mode !== 'demo';
  bootstrapReady=true;window.dispatchEvent(new Event('workspace-refreshed'));
}

async function openBatch(id, rowIndex) {
  const batch = await request(`/api/imports/${encodeURIComponent(id)}`);
  renderBatch(batch, rowIndex);
}

function renderBatch(batch, rowIndex) {
  location.hash = 'import';
  activeBatch = batch;
  const panel = $('batch-preview');
  panel.hidden = false;
  panel.replaceChildren(node('h3', `${batch.filename} · ${labels[batch.state] || batch.state}`));
  panel.append(node('p', `來源帳戶 ${batch.account_id} ｜ 批次 ${batch.id}`, 'hint'));
  if(batch.format==='xlsx'){
    const active=(batch.results||[]).filter(o=>o.status==='active').length;
    const cancelled=(batch.results||[]).filter(o=>o.status==='cancelled').length;
    panel.append(node('p',`Excel：${active} 張有效訂單、${cancelled} 張取消訂單。確認後保存營運訂單；不增加庫存，也不自動出貨。`));
    const products=batch.new_products||[];
    if(products.length){
      const details=node('details'),list=node('ul');details.append(node('summary',`${batch.state==='draft'?'確認時將建立':'本批新增商品清單'} ${products.length} 個 SKU`));
      for(const p of products)list.append(node('li',`${p.sku} — ${p.name}${p.group_name?'（'+p.group_name+'）':''}`));
      details.append(list);panel.append(details);
    }
  }
  if (batch.parent_id) {
    panel.append(node('p', `重處理來源批次 ${batch.parent_id}`, 'hint'));
    panel.append(button('查看原批次', () => openBatch(batch.parent_id)));
  }
  const scroll = node('div', undefined, 'table-scroll');
  const table = node('table');
  const head = node('thead');
  const titles = node('tr');
  for (const title of ['訂單／版本', '檢查結果', '問題與提醒']) titles.append(node('th', title));
  head.append(titles); table.append(head);
  const body = node('tbody');
  for (const result of batch.results || []) {
    const row = node('tr');
    row.append(node('td', `${result.order_id || '無訂單 ID'} / v${result.revision ?? '—'}`), node('td', labels[result.outcome] || result.outcome));
    const messages = node('td');
    for (const [key, style] of [['errors', 'error-text'], ['warnings', 'warning-text']]) {
      for (const value of result[key] || []) messages.append(node('div', describe(value), style));
    }
    if (!messages.children.length) messages.textContent = '無檢查問題';
    row.append(messages); body.append(row);
  }
  if (!body.children.length) emptyRow(body, 3, '這個批次沒有可處理的訂單。');
  table.append(body); scroll.append(table); panel.append(scroll);
  const controls = node('div', undefined, 'preview-actions');
  if (batch.state === 'draft') {
    const confirm = button(batch.format==='xlsx'?'確認建立商品並匯入訂單':'確認匯入合格訂單', async () => {
      const updated = await request(`/api/imports/${encodeURIComponent(batch.id)}/confirm`, {});
      await refreshBootstrap(); await refreshSales(); renderBatch(updated);
      announce('批次已確認。請查看各訂單結果；有問題的訂單不會強制納入。');
    });
    confirm.className = '';
    if(batch.format==='xlsx')confirm.disabled=!batch.can_confirm;
    controls.append(confirm);
  }
  if(batch.state==='confirmed'&&batch.format==='xlsx'){
    const orders=node('a','查看接收訂單');orders.href='#orders';orders.className='button secondary';controls.append(orders);
  }
  if (batch.raw_rows?.length) controls.append(button('重新預檢', async () => {
    const updated = await request(`/api/imports/${encodeURIComponent(batch.id)}/reprocess`, {});
    await refreshBootstrap(); renderBatch(updated);
    announce('已依目前商品庫重新預檢，請檢查後確認。');
  }));
  panel.append(controls);
  const raw = node('section',undefined,'content-section');
  raw.append(node('h3', rowIndex === undefined ? '查看原始列與來源資料' : `查看來源列 ${rowIndex}`));
  const rows = batch.raw_rows || [];
  raw.append(node('pre', rows.length ? rows.map((row) => `原始列 ${row.row_index}\n${JSON.stringify(row.data, null, 2)}`).join('\n\n') : '查看批次明細以讀取原始列。'));
  if (rowIndex !== undefined) raw.open = true;
  panel.append(raw);
  panel.focus({ preventScroll: true });
  panel.scrollIntoView({ behavior: 'instant', block: 'nearest' });
}

function filters() {
  const params = new URLSearchParams();
  for (const key of ['account', 'product', 'start', 'end']) {
    const value = $(`filter-${key}`).value;
    if (value) params.set(key === 'account' || key === 'product' ? `${key}_id` : key, value);
  }
  return params;
}

async function refreshSales() {
  const sequence = ++querySequence;
  const params = filters();
  window.dispatchEvent(new Event('sales-loading'));
  try {
  const data = await request(`/api/sales?${params}`);
  if (sequence !== querySequence) return;
  $('amount').textContent = formatAmount(data.amount);
  $('order-count').textContent = data.order_count;
  $('quantity').textContent = data.sales_quantity;
  $('sales-query-context').textContent = `${params.get('start')||'不限起日'} ～ ${params.get('end')||'不限迄日'} · ${boot.accounts.find(a=>a.id===params.get('account_id'))?.name||'全部來源'} · ${boot.products.find(p=>p.id===params.get('product_id'))?.name||'全部品項'} · ${(data.lines||[]).length} 筆明細`;
  $('pending').textContent = data.pending_count ? `此來源有 ${data.pending_count} 筆待確認訂單。` : '';
  $('physical').textContent = `套組展開成品件數：${(data.physical_quantities || []).map((entry) => `${entry.name || entry.product_id} ${entry.quantity} 件`).join(' ／ ') || '尚無資料'}。這不是庫存數量。`;
  $('report-meta').textContent = `計算規則 ${data.rule_version} ｜ 幣別 ${data.currency} ｜ 查詢時間 ${dateTime(data.generated_at)}`;
  $('export').href = `/api/sales/export?${params}`;
  function renderSalesRows(){
  const body = $('sales-rows');
  body.replaceChildren();
  for (const line of pageItems('sales-rows',data.lines||[],renderSalesRows,params.toString())) {
    const row = node('tr');
    const source = node('td', `${boot.accounts.find(a=>a.id===line.account_id)?.name || line.platform} · ${line.order_id}`);
    const product = node('td', line.product_name || line.product_id);
    product.append(node('small', boot.products.find(p=>p.id===line.product_id)?.sku || ''));
    const trace = node('td');
    trace.append(button(`原始列 ${line.row_index}`, () => openBatch(line.batch_id, line.row_index)));
    row.append(source, node('td', dateTime(line.ordered_at)), product, node('td', line.quantity, 'numeric'), node('td', line.amount, 'numeric'), node('td', line.kol_ref || '未歸因'), trace);
    body.append(row);
  }
  if (!body.children.length) emptyRow(body, 7, '此條件下沒有有效銷售。請先匯入並確認訂單，或調整篩選。');
  }
  renderSalesRows();
  window.dispatchEvent(new CustomEvent('sales-refreshed',{detail:{data}}));
  } catch(error) {
    if(sequence===querySequence)window.dispatchEvent(new CustomEvent('sales-error',{detail:{message:error.message}}));
    throw error;
  }
}

$('seed').addEventListener('click', () => action($('seed'), async () => {
  await request('/api/demo/seed', {});
  await refreshBootstrap(); await refreshSales();
  announce('示範商品與來源已備妥。接著下載或預檢 01、02 訂單範例。');
}));
$('filter-form').addEventListener('submit', (event) => {
  event.preventDefault();
  action(event.submitter, async () => { await refreshSales(); announce('已依目前篩選更新銷售查詢。'); });
});
$('import-form').addEventListener('submit', (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    const body=await operationalImportBody($('import-file').files[0],$('import-account').value,$('complete-snapshot').checked);
    const batch = await request('/api/imports', body);
    await refreshBootstrap(); renderBatch(batch); announce('預檢完成，尚未改動銷售。請檢查結果後確認匯入。');
  });
});

window.addEventListener('DOMContentLoaded', async () => {
  try {
    await refreshBootstrap();
    announce(boot.products.length ? '' : '可先建立商品，或從匯入銷售訂單上傳 Excel，預覽後一起確認建立商品與訂單。');
  } catch (error) { announce(error.message || '無法讀取工作區，請確認服務已啟動後重新整理。', true); }
});
