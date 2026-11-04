'use strict';

let catalogueData = {products: [], definitions: [], mappings: []};
let editingProduct = null;
let catalogueSequence = 0;
let catalogueReady=false,productEditSequence=0;
function operation() { return crypto.randomUUID(); }
function localInstant(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) throw new Error('請輸入有效生效時間');
  return date.toISOString();
}
function catalogueMessage(text, error = false) {
  const el = $('catalogue-notice'); el.hidden = !text; el.textContent = text; el.className = error ? 'notice error' : 'notice';
}
async function catalogueAction(form, work) {
  const submit = form.querySelector('button[type=submit]');
  submit.disabled = true;
  try { await work(); } catch (error) { catalogueMessage(error.message, true); $('catalogue-notice').scrollIntoView({block:'nearest'}); }
  finally { submit.disabled = false; }
}
async function refreshCatalogue() {
  const sequence = ++catalogueSequence;
  const data = await request(`/api/catalogue?q=${encodeURIComponent($('product-search').value)}`);
  if (sequence !== catalogueSequence) return;
  catalogueData = data;
  function renderProducts(){
  const rows = $('product-rows'); rows.replaceChildren();
  for (const p of pageItems('product-rows',data.products,renderProducts,$('product-search').value)) {
    const row = node('tr'); row.dataset.recordId=p.id; row.classList.toggle('selected-record',editingProduct?.id===p.id); const name = node('td', p.name);
    if (p.specification || p.group_name) name.append(node('small', [p.group_name, p.specification].filter(Boolean).join(' · ')));
    const edit = node('td'); edit.append(button('查看／編輯', () => editProduct(p.id)));
    if(p.kind==='product'){const link=node('a','庫存詳細','table-button button secondary');link.href='#quality/by-product/'+encodeURIComponent(p.id);edit.append(link);}
    const stockCells=['on_hand','available'].map(key=>{const cell=node('td','—','numeric');cell.dataset.stockStat=key;return cell;});
    row.append(name, node('td', p.sku), node('td', p.barcode || '—'), node('td', p.kind === 'bundle' ? '套組' : '單品'),...stockCells, statusCell(p.active ? '啟用' : '停用'), edit); rows.append(row);
  }
  if (!data.products.length) emptyRow(rows, 8, $('product-search').value ? '找不到符合條件的商品。' : '尚無商品，請先新增商品與 SKU。');
  renderSkuStock();
  }
  renderProducts();
  renderAccounts();
  catalogueReady=true;window.dispatchEvent(new Event('catalogue-refreshed'));
}
function attributeInputs(values = {}) {
  const container = $('product-attributes'); container.replaceChildren();
  for (const d of catalogueData.definitions) {
    const label = node('label', d.label); const input = node(d.kind === 'choice' ? 'select' : 'input');
    input.dataset.attribute = d.key;
    if (d.kind === 'choice') { input.append(new Option('未設定', '')); d.options.forEach(value => input.append(new Option(value, value))); }
    else { input.type = 'text'; input.maxLength = 200; if (d.kind === 'number') input.inputMode = 'decimal'; }
    input.value = values[d.key] || ''; label.append(input); container.append(label);
  }
}
async function editProduct(id = null) {
  const sequence=++productEditSequence,route=location.hash;
  const result=id ? await request(`/api/catalogue/${encodeURIComponent(id)}`) : null;
  if(sequence!==productEditSequence||location.hash!==route)return;
  editingProduct=result;
  const p = editingProduct || {name:'', sku:'', barcode:'', group_name:'', specification:'', kind:'product', active:true, attributes:{}};
  for(const row of $('product-rows').children)row.classList.toggle('selected-record',row.dataset.recordId===id);
  $('product-editor').querySelector('.section-title').textContent=id?p.name:'商品資料';
  for (const [field, key] of [['name','name'],['sku','sku'],['barcode','barcode'],['group','group_name'],['specification','specification'],['kind','kind']]) $('product-' + field).value = p[key] || '';
  $('product-kind').disabled = !!id; $('product-active').checked = p.active; $('product-active').disabled = !id;
  attributeInputs(p.attributes); $('product-history').replaceChildren();
  $('bundle-editor').hidden = !id || p.kind !== 'bundle'; $('bundle-lines').replaceChildren();
  if (id && p.kind === 'bundle') {
    for (const line of p.bundle_versions.at(-1)?.components || []) addBundleLine(line);
    if (!$('bundle-lines').children.length) addBundleLine();
  }
  $('product-editor').open = true; $('product-name').focus();
  window.dispatchEvent(new CustomEvent('product-selected',{detail:p.id||null}));
}
function addBundleLine(line = {}) {
  const wrap = node('div', undefined, 'bundle-line');
  const label = node('label', '組成商品'); const select = node('select'); select.required = true;
  select.append(new Option('選擇單品', ''));
  for (const p of boot.products.filter(p => p.kind === 'product')) select.append(new Option(p.name, p.id));
  select.value = line.product_id || ''; label.append(select);
  const quantityLabel = node('label', '每套件數'); const quantity = node('input'); quantity.type = 'number'; quantity.min = '1'; quantity.max = '100000'; quantity.required = true; quantity.value = line.quantity || 1; quantityLabel.append(quantity);
  const remove = button('移除', async () => wrap.remove()); wrap.append(label, quantityLabel, remove); $('bundle-lines').append(wrap);
}
function renderAccounts() {
  const container = $('account-list'); container.replaceChildren();
  for (const account of boot.accounts) {
    const form = node('form', undefined, 'search-bar'); const label = node('label', '來源名稱'); const name = node('input'); name.value = account.name; name.required = true; name.maxLength = 200; label.append(name);
    const save = node('button', '更新名稱', 'secondary'); save.type = 'submit'; form.append(label, save);
    form.addEventListener('submit', e => { e.preventDefault(); action(save, async () => { await request('/api/accounts', {operation_id:operation(), id:account.id, name:name.value}); await refreshBootstrap(); renderAccounts(); announce('來源名稱已保存。'); }); }); container.append(form);
  }
}
$('new-product').addEventListener('click', () => action($('new-product'), () => editProduct()));
$('cancel-product').addEventListener('click', () => { $('product-editor').open = false; });
$('catalogue-search').addEventListener('submit', e => { e.preventDefault(); catalogueAction(e.currentTarget, refreshCatalogue); });
$('product-form').addEventListener('submit', e => {
  e.preventDefault(); catalogueAction(e.currentTarget, async () => {
    const attributes = {}; for (const input of $('product-attributes').querySelectorAll('[data-attribute]')) if (input.value) attributes[input.dataset.attribute] = input.value;
    const body = {operation_id:operation(), name:$('product-name').value, sku:$('product-sku').value, barcode:$('product-barcode').value, group_name:$('product-group').value, specification:$('product-specification').value, kind:$('product-kind').value, attributes};
    if (editingProduct) Object.assign(body, {expected_version:editingProduct.version, active:$('product-active').checked, reason:'商品庫維護'});
    const saved = await request('/api/catalogue/products' + (editingProduct ? `/${editingProduct.id}` : ''), body);
    await refreshBootstrap(); await refreshCatalogue(); await editProduct(saved.id); catalogueMessage('商品已保存。');
  });
});
$('attribute-form').addEventListener('submit', e => {
  e.preventDefault(); catalogueAction(e.currentTarget, async () => {
    await request('/api/catalogue/attributes', {operation_id:operation(), key:$('attribute-key').value, label:$('attribute-label').value, kind:$('attribute-kind').value, options:$('attribute-kind').value === 'choice' ? $('attribute-options').value.split(/[,，]/).map(x=>x.trim()).filter(Boolean) : []});
    await refreshCatalogue(); attributeInputs(editingProduct?.attributes); catalogueMessage('屬性已建立，可在商品資料中填寫。');
  });
});
$('bundle-add').addEventListener('click', () => addBundleLine());
$('bundle-form').addEventListener('submit', e => {
  e.preventDefault(); catalogueAction(e.currentTarget, async () => {
    if (!editingProduct) throw new Error('請先保存套組商品');
    const components = [...$('bundle-lines').children].map(line => ({product_id:line.querySelector('select').value, quantity:Number(line.querySelector('input').value)}));
    await request(`/api/catalogue/bundles/${editingProduct.id}`, {operation_id:operation(), expected_version:editingProduct.version, effective_from:localInstant($('bundle-start').value), components});
    await editProduct(editingProduct.id); catalogueMessage('套組新版本已保存，既有訂單快照保持不變。');
  });
});
$('account-form').addEventListener('submit', e => {
  e.preventDefault(); action(e.submitter, async () => {
    await request('/api/accounts', {operation_id:operation(), name:$('account-name').value, platform:$('account-platform').value});
    await refreshBootstrap(); renderAccounts(); $('account-name').value = ''; announce('訂單來源已建立，可用於接收訂單。');
  });
});
window.addEventListener('workspace-refreshed', () => { refreshCatalogue().catch(error => catalogueMessage(error.message, true)); });
