'use strict';
(() => {
  const make = (tag, text, cls) => {
    const e = document.createElement(tag);
    if (text !== undefined) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  };
  const chatPage = document.getElementById('assistant');
  const settings = document.getElementById('ai-settings');
  const heading = make('header', undefined, 'section-heading');
  const title = make('h2', 'AI 營運助理'); title.id = 'ai-chat-title';
  const settingsLink = make('a', 'AI 設定', 'button secondary'); settingsLink.href='#ai-settings';
  heading.append(title, settingsLink);
  const settingsTitle=make('h2','AI 設定');settingsTitle.id='ai-settings-title';
  const settingsHeading=make('header',undefined,'section-heading');
  const chatLink=make('a','前往聊天室','button secondary');chatLink.href='#assistant';
  settingsHeading.append(settingsTitle,chatLink);settings.append(settingsHeading);
  settings.append(make('h3', 'OpenAI Codex（ChatGPT 訂閱）'));
  const connection = make('p', '尚未檢查連線'); connection.setAttribute('role', 'status');
  const controls = make('div', undefined, 'ai-chat-actions');
  const login = make('button', '登入 ChatGPT');
  const check = make('button', '檢查登入與模型', 'secondary');
  const logout = make('button', '登出', 'secondary');
  for (const b of [login, check, logout]) b.type = 'button';
  controls.append(login, check, logout);
  const loginLink = make('a', '開啟官方登入頁'); loginLink.hidden = true;
  loginLink.target = '_blank'; loginLink.rel = 'noopener noreferrer';
  const modelLabel = make('label', '營運助理預設模型');
  const model = make('select'); model.id = 'ai-chat-model'; modelLabel.htmlFor = model.id;
  const effortLabel = make('label', '思考程度');
  const effort = make('select'); effort.id = 'ai-chat-effort'; effortLabel.htmlFor = effort.id;
  for (const [value, text] of [['low','低'],['medium','中'],['high','高']]) effort.add(new Option(text, value));
  effort.value = 'medium';
  const save = make('button', '儲存 AI 設定'); save.type = 'button';
  const instructionsLabel=make('label','助理指示');instructionsLabel.htmlFor='ai-instructions';
  const instructionsInput=make('textarea');instructionsInput.id='ai-instructions';instructionsInput.rows=8;instructionsInput.maxLength=8000;
  const roleLabel=make('label','助理角色');roleLabel.htmlFor='ai-role';
  const roleInput=make('select');roleInput.id='ai-role';
  const toneLabel=make('label','回答語氣');toneLabel.htmlFor='ai-tone';
  const toneInput=make('select');toneInput.id='ai-tone';
  const brandLabel=make('label','品牌名稱');brandLabel.htmlFor='ai-brand';
  const brandInput=make('input');brandInput.id='ai-brand';brandInput.type='text';brandInput.maxLength=80;
  const status = make('p', '', 'ai-chat-status');
  status.setAttribute('role', 'status');
  const settingsStatus=make('p','', 'ai-chat-status');settingsStatus.setAttribute('role','status');
  settings.append(connection, controls, loginLink, modelLabel, model, effortLabel, effort,
    make('h3','回答方式'),roleLabel,roleInput,toneLabel,toneInput,brandLabel,brandInput,
    instructionsLabel,instructionsInput,save);
  const log = make('div', undefined, 'ai-chat-log'); log.setAttribute('role','log'); log.setAttribute('aria-live','polite'); log.setAttribute('aria-label','營運助理對話');
  const form = make('form', undefined, 'ai-chat-form');
  const questionLabel = make('label', '你的問題');
  const input = make('textarea'); input.id='ai-chat-question'; input.rows=3; input.maxLength=2000; input.required=true;
  questionLabel.htmlFor=input.id; input.placeholder='例如：2026 年 10 月 1 日的訂單總金額是多少？';
  const actions = make('div', undefined, 'ai-chat-actions');
  const submit = make('button','傳送'); submit.type='submit';
  const clear = make('button','新對話','secondary'); clear.type='button';
  actions.append(submit,clear); form.append(questionLabel,input,actions);
  settings.append(settingsStatus);
  const implementation=make('section',undefined,'ai-implementation');
  settings.append(implementation);
  chatPage.append(heading,log,status,form);
  let history=[]; let busy=false;
  let availableModels=[];
  let policyLoaded=false;
  const showStatus=(text,error=false)=>{const target=location.hash==='#ai-settings'?settingsStatus:status;target.textContent=text;target.classList.toggle('error',error);};
  async function run(work) {
    if(busy)return;
    busy=true;
    for(const b of [submit,clear,login,check,logout,save]) b.disabled=true;
    try { await work(); } catch(e) {showStatus(e.message||'無法連線，請確認本機服務仍在運作。',true);}
    finally {busy=false;for(const b of [submit,clear,login,check,logout,save])b.disabled=false;}
  }
  function message(role,text) {
    const box=make('article',undefined,'ai-message '+role);
    box.append(make('strong',role==='user'?'你':'營運助理'),make('p',text));log.append(box);
    return box;
  }
  async function refresh() {
    if(!policyLoaded){
      const [policy,catalog]=await Promise.all([request('/api/assistant/settings'),request('/api/assistant/capabilities')]);
      instructionsInput.value=policy.instructions||'';
      for(const [value,label] of Object.entries(catalog.roles))roleInput.add(new Option(label,value));
      for(const [value,label] of Object.entries(catalog.tones))toneInput.add(new Option(label,value));
      roleInput.value=policy.role;toneInput.value=policy.tone;brandInput.value=policy.brand_name;
      renderImplementation(catalog);
      policyLoaded=true;
    }
    showStatus('正在檢查登入…');
    const state=await request('/api/assistant/status',{});
    connection.textContent=state.logged_in?`已登入 · ${state.plan||'方案由官方帳戶決定'}`:'尚未登入';
    if(state.logged_in) {
      loginLink.hidden=true;
      const result=await request('/api/assistant/models',{});
      availableModels=result.models;
      model.replaceChildren(new Option('請選擇模型',''));
      for(const m of result.models)model.add(new Option(m.name,m.id));
      model.value=state.settings.model;effort.value=state.settings.effort;
      updateEffort();
      showStatus('連線檢查完成。');
    } else showStatus('請登入 ChatGPT，再檢查登入與模型。');
  }
  function renderAnswer(parent,text) {
    const lines=text.split('\n');
    for(let i=0;i<lines.length;i++){
      const line=lines[i];
      if(line.trim().startsWith('|')&&/^\s*\|?\s*:?-{3,}/.test(lines[i+1]||'')){
        const wrap=make('div',undefined,'table-scroll');const table=make('table');
        const cells=s=>s.trim().replace(/^\||\|$/g,'').split('|').map(v=>v.trim());
        const head=make('thead');const row=make('tr');
        for(const value of cells(line))row.append(make('th',value));head.append(row);table.append(head);i++;
        const body=make('tbody');
        while(i+1<lines.length&&lines[i+1].trim().startsWith('|')){const r=make('tr');for(const value of cells(lines[++i]))r.append(make('td',value));body.append(r);}
        table.append(body);wrap.append(table);parent.append(wrap);
      }else if(line.trim()){
        const p=make('p');
        const clean=line.replace(/^#{1,6}\s+/,'');
        for(const part of clean.split(/(\*\*[^*]+\*\*)/g))p.append(part.startsWith('**')&&part.endsWith('**')?make('strong',part.slice(2,-2)):document.createTextNode(part));
        parent.append(p);
      }
    }
  }
  function disclosure(parent,label,value,prose=false) {
    const details=make('details');details.append(make('summary',label));
    if(prose){
      const body=make('div',undefined,'ai-prompt-text');
      for(const line of String(value).split('\n').filter(line=>line.trim()))body.append(make('p',line));
      details.append(body);
    }else details.append(make('pre',typeof value==='string'?value:JSON.stringify(value,null,2)));
    parent.append(details);return details;
  }
  function renderOrderLinks(parent,result) {
    for(const evidence of result.evidence||[]){
      if(evidence.tool!=='query_orders'||evidence.filters?.view!=='unshipped')continue;
      const section=make('section');
      section.append(make('h3','查看訂單'));
      const offset=evidence.offset||0, limit=evidence.limit||5, total=evidence.total_rows??evidence.unshipped_order_count??0;
      const filters=evidence.filters||{};
      section.append(make('p',`下單日期：${filters.start||'不限'}～${filters.end||'不限'}；${filters.account_id?'指定來源':'全部來源'}。排除取消單。`));
      section.append(make('p',total&&evidence.orders?.length?`顯示第 ${offset+1}–${offset+evidence.orders.length} 筆，共 ${total} 筆。`:`本頁沒有訂單；目前共 ${total} 筆。`));
      for(const row of evidence.orders||[]){
        const details=make('details');
        details.append(make('summary',`${row.order_id} · ${row.fulfillment_status} · 尚待出貨 ${row.remaining_quantity} 件`));
        for(const item of row.items||[])details.append(make('p',`${item.name}（${item.sku||item.product_id}）：需求 ${item.required_quantity}、已出 ${item.shipped_quantity}、未出 ${item.remaining_quantity} 件`));
        if(result.order_links_enabled){
          const link=make('a','查看原訂單','button secondary');
          link.href='#orders/'+encodeURIComponent(JSON.stringify([row.account_id,row.order_id]));
          details.append(link);
        }else details.append(make('p','原訂單入口尚未支援：目前頁面與查詢資料來源不同。'));
        section.append(details);
      }
      if(evidence.limit){
        const controls=make('div',undefined,'ai-chat-actions');
        for(const [label,target,disabled] of [['上一頁',Math.max(0,offset-limit),offset===0],['下一頁',offset+limit,!evidence.truncated]]){
          const button=make('button',label,'secondary');button.type='button';button.disabled=disabled;
          button.addEventListener('click',()=>run(async()=>{
            const buttons=controls.querySelectorAll('button');buttons.forEach(b=>b.disabled=true);
            showStatus('正在讀取訂單…');
            try{
              const next=await request('/api/assistant/chat',{question:'訂單清單分頁',order_page:{...filters,offset:target,limit}});
              const replacement=make('div');renderOrderLinks(replacement,next);
              const nextSection=replacement.firstChild;renderExecution(nextSection,next);
              section.replaceWith(nextSection);
              history.push({role:'user',text:`訂單清單翻頁至第 ${target+1} 筆，沿用條件：${JSON.stringify(filters)}`},{role:'assistant',text:next.answer.slice(0,4000)});
              showStatus('');
            }finally{buttons.forEach((b,i)=>b.disabled=i===0?offset===0:!evidence.truncated);}
          }));controls.append(button);
        }
        section.append(controls);
      }
      parent.append(section);
    }
  }
  function renderImplementation(catalog) {
    implementation.replaceChildren(make('h3','AI 功能現況'));
    for(const item of catalog.implementation||[]){
      const section=make('section',undefined,'ai-capability');
      section.append(make('h4',item.name));
      const list=make('ul');
      for(const sentence of item.content.split('。').filter(Boolean))list.append(make('li',sentence+'。'));
      section.append(list);
      for(const prompt of item.prompts||[])disclosure(section,prompt.name,prompt.text,true);
      implementation.append(section);
    }
  }
  function parseObject(text) {try{return JSON.parse(text);}catch{return {};}}
  function renderExecution(parent,result) {
    const execution=result.execution;if(!execution)return;
    const details=make('details',undefined,'ai-execution');
    const requests=execution.model_requests||[];
    const chars=requests.reduce((total,item)=>total+[...item.system_prompt].length,0);
    details.append(make('summary','這個回答怎麼產生的？'));
    details.append(make('p',`${requests.length} 份 system prompt · ${execution.tool_call_count} 次工具查詢 · ${execution.total_seconds.toFixed(2)} 秒`,'ai-execution-overview'));
    const flow=make('ol',undefined,'ai-execution-flow');details.append(flow);
    function step(title,description){const item=make('li');item.append(make('h4',title),make('p',description));flow.append(item);return item;}
    const firstInput=parseObject(requests[0]?.input||'{}');
    step('你的問題',execution.fixed_query?.question||firstInput.question||'問題內容請見原始輸入');
    if(execution.fixed_query){
      const tools=step('系統執行查詢','條件明確，直接執行唯讀查詢並以固定格式回答，未呼叫模型。');
      for(const call of execution.tool_calls||[]){
        disclosure(tools,'查看查詢條件',call.parameters);
        disclosure(tools,'查看完整查詢結果',call.result);
      }
    }
    for(const [index,item] of requests.entries()){
      const plan=item.stage==='planning'?parseObject(item.output):{};
      const action={query:'決定查詢系統資料。',answer:'直接說明系統功能或資料口徑。',clarify:'需要先釐清問題。',reject:'判定需求不在系統服務範圍。'}[plan.action]||'產生查詢計畫。';
      const stage=step(item.stage==='planning'?'AI 判斷要怎麼處理':'AI 整理回答',item.stage==='planning'?action:'根據本次查詢結果，整理成上方的回答。');
      disclosure(stage,`閱讀系統提示詞 · ${[...item.system_prompt].length.toLocaleString()} 字元`,item.system_prompt,true);
      const raw=make('details',undefined,'ai-raw-details');raw.append(make('summary','查看原始傳送內容'));
      raw.append(make('p',`模型：${item.model} · 思考程度：${item.effort}`));
      disclosure(raw,'問題與上下文',item.input);
      if(item.output_schema)disclosure(raw,'輸出格式（schema）',item.output_schema);
      disclosure(raw,'模型實際回傳',item.output);stage.append(raw);
      if(item.stage==='planning'){
        const tools=step('系統執行查詢',execution.tool_call_count?`實際執行 ${execution.tool_call_count} 次唯讀查詢。`:'本次沒有查詢資料。');
        for(const call of execution.tool_calls||[]){
          const names={orders:'訂單與 UTM',shipping:'出貨與成本',inventory:'商品庫存'};
          const p=call.parameters;
          tools.append(make('p',`${names[call.tool]||call.tool} · ${p.start||'未指定起日'} ～ ${p.end||'未指定迄日'} · ${call.seconds.toFixed(4)} 秒`));
          disclosure(tools,'查看查詢條件',p);
          disclosure(tools,'查看完整查詢結果',call.result);
        }
        if(result.scope==='rejected')tools.append(make('p','範圍外問題，後端使用固定拒答，未執行工具。'));
      }
    }
    const meta=make('details',undefined,'ai-raw-details');meta.append(make('summary','追蹤與快取資訊'));
    meta.append(make('p',`追蹤碼：${result.request_id}`),make('p',`提示詞合計 ${chars.toLocaleString()} 字元（非 token） · 模型呼叫 ${execution.model_call_count} 次`));
    const cache=execution.cache;
    if(cache)meta.append(make('p',`靜態配方快取：命中 ${cache.hits} 份，重新載入 ${cache.misses} 份。業務數字每題重查。`));
    details.append(meta);
    parent.append(details);
  }
  function updateEffort() {
    const supported=availableModels.find(m=>m.id===model.value)?.efforts||[];
    for(const option of effort.options)option.disabled=supported.length>0&&!supported.includes(option.value);
    if(effort.selectedOptions[0]?.disabled)effort.value=[...effort.options].find(o=>!o.disabled)?.value||'';
  }
  model.addEventListener('change',updateEffort);
  login.addEventListener('click',()=>run(async()=>{
    showStatus('正在建立官方登入連線…');
    const result=await request('/api/assistant/login',{});
    const url=new URL(result.authUrl);
    if(url.protocol!=='https:'||!['auth.openai.com','chatgpt.com','auth0.openai.com'].includes(url.hostname))throw new Error('登入網址不在官方允許清單內。');
    loginLink.href=url.href;loginLink.hidden=false;
    showStatus('請點「開啟官方登入頁」，完成後回來按「檢查登入與模型」。');
  }));
  check.addEventListener('click',()=>run(refresh));
  logout.addEventListener('click',()=>run(async()=>{
    await request('/api/assistant/logout',{});connection.textContent='已登出';loginLink.hidden=true;showStatus('已登出此 ERP 的 Codex 連線。');
  }));
  save.addEventListener('click',()=>run(async()=>{
    await request('/api/assistant/settings',{model:model.value,effort:effort.value,instructions:instructionsInput.value,
      role:roleInput.value,tone:toneInput.value,brand_name:brandInput.value});
    renderImplementation(await request('/api/assistant/capabilities'));
    showStatus('已儲存 AI 設定。下一則訊息開始生效。');
  }));
  clear.addEventListener('click',()=>{history=[];log.replaceChildren();showStatus('');input.focus();});
  form.addEventListener('submit',event=>{
    event.preventDefault();const question=input.value.trim();if(!question||busy)return;
    run(async()=>{
      message('user',question);showStatus('');
      const pending=make('article',undefined,'ai-message assistant ai-thinking');
      pending.setAttribute('aria-label','營運助理思考中');
      pending.append(make('strong','營運助理'));
      const dots=make('span',undefined,'ai-thinking-dots');
      dots.setAttribute('aria-hidden','true');
      for(let i=0;i<3;i++)dots.append(make('span',''));
      pending.append(dots);log.append(pending);
      pending.scrollIntoView({block:'nearest'});
      let result;
      try {
        result=await request('/api/assistant/chat',{question,history:history.slice(-8)});
      } finally {
        pending.remove();
      }
      const box=message('assistant','');box.lastChild.remove();renderAnswer(box,result.answer_summary??result.answer);
      renderOrderLinks(box,result);
      renderExecution(box,result);
      for(const evidence of result.execution?[]:result.evidence||[]) {
        const details=make('details');details.append(make('summary','查看本機查詢依據與訂單核對表'));
        details.append(make('pre',JSON.stringify(evidence,null,2)));box.append(details);
      }
      history.push({role:'user',text:question},{role:'assistant',text:result.answer.slice(0,4000)});
      input.value='';showStatus('');box.scrollIntoView({block:'nearest'});
    });
  });
  input.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();form.requestSubmit();}});
  function onRoute(){if(location.hash==='#ai-settings'&&!busy)run(refresh);}
  window.addEventListener('hashchange',onRoute);onRoute();
})();
