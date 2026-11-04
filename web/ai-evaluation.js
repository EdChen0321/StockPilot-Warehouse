'use strict';
const evaluationModel = (() => {
  const status = row => row.checks?.evidence === false || row.checks?.route === false || row.checks?.answer === false
    ? 'fail' : row.checks?.answer == null ? 'review' : 'pass';
  const select = (rows, category, variant, outcome) => rows.filter(r =>
    (!category || r.category === category) && (!variant || r.variant === variant) && (!outcome || status(r) === outcome));
  const aggregate = rows => {
    const times=rows.map(r=>r.elapsed_ms).filter(Number.isFinite).sort((a,b)=>a-b);
    const known=rows.filter(r=>r.checks?.answer!=null);
    return {count:rows.length,evidence:rows.filter(r=>r.checks?.evidence===true).length,
      reviewed:known.length,answerPassed:known.filter(r=>r.checks.answer===true).length,
      mean:times.length?times.reduce((a,b)=>a+b,0)/times.length:null,
      p50:times.length?times[Math.ceil(times.length*.5)-1]:null,
      p95:times.length?times[Math.ceil(times.length*.95)-1]:null,
      errors:rows.filter(r=>!r.execution_success).length};
  };
  return {status,select,aggregate};
})();
if(typeof module!=='undefined')module.exports=evaluationModel;
if(typeof document!=='undefined')(() => {
  const root=document.getElementById('ai-evaluation');
  const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
  const button=(text,handler)=>{const e=make('button',text,'secondary');e.type='button';e.addEventListener('click',handler);return e;};
  const label=(text,input)=>{const e=make('label',text);e.append(input);return e;};
  const seconds=value=>value==null?'—':(value/1000).toFixed(2)+' 秒';
  const variants={model:'原模型流程',fast:'快速路徑＋模型回退'};
  const states={fail:'需修正',review:'回答待覆核',pass:'已通過限定檢查'};
  let run=null,chart=null,busy=false,timer=null,loaded=false;
  const heading=make('header',undefined,'section-heading');
  const title=make('h2','AI 評測');title.id='ai-evaluation-title';
  const actions=make('div',undefined,'eval-actions');
  const refresh=button('重新整理',()=>load(true));
  const download=button('匯出本次結果',()=>{
    if(!run)return;
    const url=URL.createObjectURL(new Blob([JSON.stringify(run,null,2)],{type:'application/json'}));
    const link=make('a');link.href=url;link.download=run.id+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  download.disabled=true;actions.append(refresh,download);heading.append(title,actions);
  const notice=make('p','','eval-notice');notice.setAttribute('role','status');
  const controls=make('div',undefined,'eval-filters');
  const runs=make('select');runs.id='eval-run';
  const category=make('select');category.id='eval-category';
  const variant=make('select');variant.id='eval-variant';
  const outcome=make('select');outcome.id='eval-outcome';
  for(const [el,entries] of [[category,[['','全部大類'],['營運','營運'],['庫存','庫存'],['訂單','訂單']]],
    [variant,[['','全部流程'],...Object.entries(variants)]],
    [outcome,[['','全部結果'],['fail','需修正'],['review','回答待覆核'],['pass','限定檢查通過']]]])
    for(const [value,text] of entries)el.add(new Option(text,value));
  controls.append(label('評測批次',runs),label('問題大類',category),label('執行流程',variant),label('檢查結果',outcome));
  const meta=make('p','','eval-meta');
  const metrics=make('dl',undefined,'eval-metrics');
  const acceptance=make('section');
  const compareTitle=make('h3','同題流程比較');
  const comparison=make('div',undefined,'table-scroll');
  const chartTitle=make('h3','各階段平均耗時');
  const chartNode=make('div',undefined,'eval-chart');chartNode.setAttribute('role','img');
  const sampleNote=make('p','','eval-meta');
  const listHeading=make('header',undefined,'section-heading');
  const listTitle=make('h3','案例與失敗原因');const count=make('span');listHeading.append(listTitle,count);
  const table=make('div',undefined,'table-scroll eval-table');
  const methodology=make('details',undefined,'eval-methodology');methodology.append(make('summary','如何閱讀這份評測'));
  methodology.append(make('p','證據檢查核對工具、條件、數字與清單；不等於回答完整正確。固定數字回答有少量自動檢查，其餘清單、建議與文字待人工覆核。'),
    make('p','耗時包含啟動與登入；失敗請求仍列入樣本。停用工具若被正確阻擋，執行可為錯誤，但權限檢查可通過。Token、費用與未評分項目以未知顯示，不當成零。'),
    make('p','A/B 只配對同一批次、案例與輪次，差額為快速流程減原模型流程。合成資料庫與目前工作庫分開；本頁只讀取評測，不會觸發模型或修改業務資料。'));
  root.append(heading,controls,notice,meta,acceptance,metrics,compareTitle,comparison,chartTitle,chartNode,sampleNote,listHeading,table,methodology);
  function cellRow(values,header=false){const tr=make('tr');for(const value of values)tr.append(make(header?'th':'td',String(value)));return tr;}
  function render(){
    if(!run)return;
    acceptance.replaceChildren();
    if(run.acceptance_checks?.length){
      acceptance.append(make('h3','訂單流程驗收'));
      const checklist=make('table');checklist.append(cellRow(['驗收項目','結果','核對依據'],true));
      for(const item of [...(run.deployment_checks||[]),...run.acceptance_checks])checklist.append(cellRow([item.name,item.passed===true?'通過':item.passed===false?'未通過':'待驗收',item.detail]));
      acceptance.append(checklist);
    }
    const rows=evaluationModel.select(run.records,category.value,variant.value,outcome.value),stats=evaluationModel.aggregate(rows);
    meta.textContent=`${run.dataset} · ${run.model} / ${run.effort} · ${run.scorer_version} / ${run.answer_scorer_version||'未覆核'} · ${run.calendar?.today||''}（台北日期依據）`;
    metrics.replaceChildren();
    for(const [name,value] of [['查詢證據通過',`${stats.evidence} / ${stats.count}`],['回答欄位通過／已檢查',`${stats.answerPassed} / ${stats.reviewed}`],
      ['平均耗時',seconds(stats.mean)],['P95 耗時',seconds(stats.p95)],['執行錯誤',stats.errors],['Token／費用','未知']]){
      const group=make('div');group.append(make('dt',name),make('dd',String(value)));metrics.append(group);
    }
    const compareTable=make('table');compareTable.append(cellRow(['大類','成對樣本','原模型平均','快速流程平均','平均差額'],true));
    for(const cat of (category.value?[category.value]:['營運','庫存','訂單'])){
      const pairs=new Map();
      for(const r of run.records.filter(r=>r.category===cat)){
        const key=`${r.case_id}/${r.round}`;if(!pairs.has(key))pairs.set(key,{});pairs.get(key)[r.variant]=r;
      }
      const matched=[...pairs.values()].filter(p=>p.model?.execution_success&&p.fast?.execution_success);
      const a=matched.length?matched.reduce((sum,p)=>sum+p.model.elapsed_ms,0)/matched.length:null;
      const b=matched.length?matched.reduce((sum,p)=>sum+p.fast.elapsed_ms,0)/matched.length:null;
      compareTable.append(cellRow([cat,matched.length,seconds(a),seconds(b),a==null?'—':seconds(b-a)]));
    }
    comparison.replaceChildren(compareTable);
    sampleNote.textContent=`目前篩選 ${rows.length} 次執行，中位耗時 ${seconds(stats.p50)}；小樣本 P95 僅描述本批結果，不是效能保證。耗時圖不含尚未量到的階段。`;
    renderChart(rows);
    count.textContent=`${rows.length} 次執行`;
    const t=make('table');t.append(cellRow(['案例／問題','大類','流程','檢查結果','完整耗時','模型／工具','詳情'],true));
    for(const row of [...rows].sort((a,b)=>(evaluationModel.status(a)==='fail'?0:1)-(evaluationModel.status(b)==='fail'?0:1)||a.case_id.localeCompare(b.case_id)||a.round-b.round)){
      const tr=make('tr');const q=make('td');q.append(make('strong',row.case_id),make('p',row.question));
      const state=make('td');const badge=make('span',states[evaluationModel.status(row)],'eval-state');badge.dataset.state=evaluationModel.status(row);state.append(badge);
      const details=make('details');details.append(make('summary','查看結果'));
      details.append(make('h4','預期條件'),make('pre',JSON.stringify(row.expected,null,2)),
        make('h4','檢查結果'),make('p',(row.reasons||[]).join('；')||'查詢證據符合本案例的限定檢查。'),
        make('p',row.checks?.answer==null?'回答品質：待覆核。':`回答檢查：${row.answer_check_scope||'限定數字敘述'}。完整文字品質仍待覆核。`),
        make('p',`第 ${row.round} 輪 · 查詢期間資料${row.business_data_unchanged?'未變':'已變更'}`),
        make('h4','實際回答'),make('p',row.answer||row.error||'沒有回答'));
      const evidence=make('details');evidence.append(make('summary','查詢條件與原始證據'),make('pre',JSON.stringify(row.calls,null,2)));details.append(evidence);
      const d=make('td');d.append(details);
      tr.append(q,make('td',row.category),make('td',variants[row.variant]||row.variant),state,make('td',seconds(row.elapsed_ms)),make('td',`${row.model_calls} / ${row.tool_calls}`),d);t.append(tr);
    }
    table.replaceChildren(rows.length?t:make('p','目前篩選沒有符合的案例。'));
    download.disabled=false;
  }
  function renderChart(rows){
    if(!window.echarts){chartNode.textContent='圖表無法載入；各案例仍可查看完整耗時。';return;}
    chart ||= echarts.init(chartNode,null,{renderer:'svg'});
    const groups=Object.keys(variants).filter(v=>rows.some(r=>r.variant===v));
    const phases=[['setup_seconds','啟動／登入'],['planning_seconds','模型規劃'],['query','資料查詢'],['answer_seconds','回答']];
    const values=phases.map(([key])=>groups.map(v=>{
      const measured=rows.filter(r=>r.variant===v&&r.timings&&(key==='query'?Array.isArray(r.timings.query_calls):Number.isFinite(r.timings[key])));
      return measured.length?measured.reduce((sum,r)=>sum+(key==='query'?r.timings.query_calls.reduce((a,b)=>a+b.seconds,0):r.timings[key]),0)/measured.length:null;
    }));
    chartNode.setAttribute('aria-label',phases.map(([,name],i)=>`${name}：${groups.map((v,j)=>`${variants[v]} ${values[i][j]==null?'未知':values[i][j].toFixed(2)+'秒'}`).join('；')}`).join('。'));
    chart.setOption({animation:false,color:['#94a3b8','#326b89','#609278','#b58a42'],tooltip:{trigger:'axis',axisPointer:{type:'shadow'},renderMode:'richText'},
      legend:{bottom:0},grid:{left:16,right:25,top:15,bottom:60,containLabel:true},xAxis:{type:'value',name:'秒'},
      yAxis:{type:'category',data:groups.map(v=>variants[v])},series:phases.map(([,name],i)=>({name,type:'bar',stack:'time',barMaxWidth:32,data:values[i]}))},true);
    chart.resize();
  }
  async function read(url){const response=await fetch(url);const data=await response.json();if(!response.ok)throw new Error(data.error||'讀取失敗');return data;}
  async function load(refreshList=false){
    if(busy)return;busy=true;refresh.disabled=true;notice.textContent='正在讀取評測結果…';notice.classList.remove('error');clearTimeout(timer);
    try{
      if(refreshList||!loaded){
        const selected=runs.value;const data=await read('/api/assistant/evaluations');runs.replaceChildren();
        for(const entry of data.runs)runs.add(new Option(`${entry.started_at.replace('T',' ').slice(0,19)} UTC · ${entry.completed_attempts}/${entry.expected_attempts} · ${entry.status}`,entry.id));
        if([...runs.options].some(o=>o.value===selected))runs.value=selected;loaded=true;
      }
      if(!runs.value){notice.textContent='尚無評測結果。執行評測後，按重新整理即可查看。';download.disabled=true;return;}
      if(run&&run.id!==runs.value){run=null;meta.textContent='';acceptance.replaceChildren();metrics.replaceChildren();comparison.replaceChildren();table.replaceChildren();chart?.clear();download.disabled=true;}
      run=await read('/api/assistant/evaluations/'+encodeURIComponent(runs.value));render();
      const selected=[...runs.options].find(o=>o.value===run.id);
      if(selected)selected.textContent=`${run.started_at?.replace('T',' ').slice(0,19)||run.id} UTC · ${run.records.length}/${run.expected_attempts} · ${run.status}`;
      const state={running:'評測進行中',completed:'本批已執行完成',interrupted:'本批中斷'}[run.status]||run.status;
      notice.textContent=`${state} · ${run.records.length} / ${run.expected_attempts} 次。${run.status==='running'?'每 5 秒更新。':''}`;
      if(run.status==='running'&&!root.hidden)timer=setTimeout(()=>load(false),5000);
    }catch(error){notice.textContent=`無法載入評測：${error.message}。請按重新整理重試。`;notice.classList.add('error');}
    finally{busy=false;refresh.disabled=false;}
  }
  for(const control of [category,variant,outcome])control.addEventListener('change',render);
  runs.addEventListener('change',()=>load(false));
  function activate(){if(location.hash==='#ai-evaluation')load(!loaded);else clearTimeout(timer);}
  window.addEventListener('hashchange',activate);window.addEventListener('resize',()=>chart?.resize());activate();
})();
