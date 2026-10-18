'use strict';
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = v => v === null || v === undefined ? '待补行情' : Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
const sign = v => Number(v)>0?'profit':Number(v)<0?'loss':'';
const csrf = $('meta[name="csrf-token"]').content;
let state, settingsLoaded=false;
const titles={overview:'资金总览',trades:'每日交易',reviews:'AI 复盘',stats:'交易统计',settings:'设置与备份'};
function notice(text,error=false){const el=$('#notice');el.textContent=text;el.className=error?'error':'success';}
function tab(name){document.querySelectorAll('.page').forEach(e=>e.classList.toggle('hidden',e.id!==name));document.querySelectorAll('nav button').forEach(e=>e.classList.toggle('active',e.dataset.tab===name));$('#page-title').textContent=titles[name];}
document.addEventListener('click',e=>{const target=e.target.closest('[data-tab]');if(target)tab(target.dataset.tab);});
async function api(url,method='GET',body){const options={method,headers:{'X-CSRF-Token':csrf}};if(body instanceof FormData)options.body=body;else if(body!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}const response=await fetch(url,options);const value=await response.json();if(!response.ok)throw Error(value.error||value.detail||'请求失败');return value;}
function values(form){return Object.fromEntries(new FormData(form));}
function localTime(){return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date()).replace(' ','T');}
function dates(){document.querySelectorAll('input[type=datetime-local]').forEach(e=>{if(!e.value)e.value=localTime();});}
function table(headers,rows){return rows.length?`<table><thead><tr>${headers.map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`:'<div class="empty">还没有记录。从第一笔真实交易开始。</div>';}
function feesTotal(e){return Object.values(e.fees||{}).reduce((a,b)=>a+Number(b),0);}
function render(){
 const s=state;
 $('#metrics').innerHTML=[['剩余现金',s.cash,''],['总资产',s.assets,''],['已实现净盈亏',s.realized,sign(s.realized)],['累计交易费用',s.total_fees,'']].map(([label,value,cls])=>`<div class="metric"><span>${label}</span><strong class="${cls}">${fmt(value)}</strong><small>元${label==='总资产'?' · 按已获取行情':''}</small></div>`).join('');
 $('#positions').innerHTML=table(['股票','持股 / 股','含费均价','持仓市值','浮动盈亏','行情时间'],s.positions.map(p=>[`${esc(p.name)}<small>${esc(p.code)}</small>`,p.qty,fmt(p.avg_cost),fmt(p.value),`<span class="${sign(p.unrealized)}">${fmt(p.unrealized)}</span>`,esc(p.quote_at||'缺失，请更新行情')]));
 if(!s.positions.length)$('#positions').innerHTML='<div class="empty">当前空仓，没有持有股票。</div>';
 const recent=s.events.slice().reverse();
 $('#recent').innerHTML=table(['时间','操作','金额','现金余额'],recent.slice(0,5).map(e=>[esc(e.at.replace('T',' ')),esc(e.kind==='trade'?`${e.side==='buy'?'买入':'卖出'} ${e.name}`:({deposit:'入金',withdraw:'出金',adjustment:'公司行为调整'}[e.kind])),fmt(e.amount),fmt(e.cash_after)]));
 $('#transactions').innerHTML=table(['时间','股票','交易类型','成交数量（股）','成交均价（元）','成交金额（元）','费用（元）','实际支出/到账（元）','操作'],($('#all-transactions').checked?recent:s.events.filter(e=>e.at.slice(0,10)===$('#trade-day').value)).map(e=>[
 esc(e.at.replace('T',' ')),`${esc(e.name||e.code||'—')}<small>${esc(e.name?e.code:'')}${e.kind!=='trade'?' '+esc(e.note||''):''}</small>`,esc(e.kind==='trade'?(e.side==='buy'?'买入':'卖出'):({deposit:'入金',withdraw:'出金',adjustment:'公司行为'}[e.kind])),
 e.kind==='trade'?e.qty:(e.kind==='adjustment'?`股份调整 ${esc(e.qty_delta)}`:'—'),
 e.kind==='trade'?`${esc(e.price)}${e.side==='buy'?`<small>预期收盘 ${esc(e.expected)} / 实际 ${s.closing_comparisons[e.id]?esc(s.closing_comparisons[e.id].close):'待补行情'}</small>`:''}`:'—',
 e.kind==='trade'?fmt(e.amount):'—',
 e.kind==='trade'?`${fmt(feesTotal(e))}<small>${e.fee_mode==='actual'?'已核对':'估算'} · 印花 ${fmt(e.fees.stamp)} / 过户 ${fmt(e.fees.transfer)} / 佣金 ${fmt(e.fees.commission)} / 其他 ${fmt(e.fees.other)}</small>`:'—',
 `${e.kind==='trade'?(e.side==='buy'?'支出 '+fmt(Number(e.amount)+feesTotal(e)):'到账 '+fmt(Number(e.amount)-feesTotal(e))):(e.kind==='withdraw'?'支出 ':'到账 ')+fmt(e.amount)}<small>操作后现金 ${fmt(e.cash_after)}</small>`,
 `${e.kind==='trade'?`<button data-edit="${e.id}">更正</button>`:''} <button data-delete="${e.id}" class="text-danger">删除</button>${s.images.filter(i=>i.event_id===e.id).map(i=>` <a target="_blank" href="/images/${esc(i.id)}" rel="noopener">截图</a>`).join('')}`]));
 const selected=$('#review-stock').value;const codes=new Map(s.events.filter(e=>e.kind==='trade').map(e=>[e.code,e.name]));$('#review-stock').innerHTML=[...codes].map(([c,n])=>`<option value="${esc(c)}">${esc(n)} · ${c}</option>`).join('');if(codes.has(selected))$('#review-stock').value=selected;
 $('#jobs').innerHTML=s.jobs.length?s.jobs.map(j=>`<div class="job"><span class="pill ${j.status==='failed'?'bad':''}">${esc({pending:'排队',running:'运行中',done:'已完成',failed:'失败'}[j.status])}</span><b>${esc(j.scope==='day'?j.date+' 全天':j.code)} · ${j.review?'复盘':'行情更新'}</b><span>${esc(j.error||j.stage||'等待开始')}</span>${j.market_errors?.length?`<small>${esc(j.market_errors.join('；'))}</small>`:''}</div>`).join(''):'<div class="empty">尚无任务。录入交易后会自动尝试获取行情。</div>';
 $('#market-status').innerHTML=s.market.length?s.market.map(m=>`<div class="source"><b>${esc(m.code)} / ${esc(m.kind)}</b><small>${esc(m.source||'未取得来源')} · 获取于 ${esc(m.fetched_at)}</small><p>${esc(m.error||'已保存')} ${m.coverage?'覆盖：'+esc(m.coverage.join(' → ')):''}</p></div>`).join(''):'<div class="empty">尚未获取行情。</div>';
 $('#reports').innerHTML=s.reports.map(r=>`<article><div class="section-head"><h2>${esc(r.scope==='day'?r.date+' 全天':r.code)} · 复盘报告</h2><span class="pill">${r.revision===s.settings.revision?'当前账本版本':'账本已修改，请重新复盘'}</span></div><small>${esc(r.created)} · ${r.ai_config?esc(r.ai_config.model)+' / '+esc(r.ai_config.effort):'历史报告未记录模型配置'}</small><h3>交易结果</h3><p>${esc(r.result.summary)}</p><h3>结果发生在哪个阶段</h3><ul>${r.result.phases.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><h3>${r.scope==='day'?'操作前可观察的风险':'买入前可观察的风险'}</h3>${r.result.risks.length?r.result.risks.map(x=>`<p>${esc(x.text)}<small>证据：${x.evidence_ids.map(esc).join(' / ')}</small></p>`).join(''):'<p>未提取到可核验的事前风险，操作评价与可能解释见下方。</p>'}<h3>主要问题</h3><p>${esc(r.result.main_issue)}</p><h3>下一次验证一件事</h3><p>${esc(r.result.next_experiment)}</p><h3>证据边界</h3><ul>${r.result.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><a href="/api/reports/${esc(r.id)}/evidence">下载本次分析证据</a></article>`).join('');
 $('#statistics').innerHTML=table(['分组','样本数','胜率','平均净收益 / 元','平均盈利 / 元','平均亏损 / 元','平均收益率'],s.statistics.map(r=>[esc(r.group),`${r.count}${r.count<20?'<small>小样本，仅作观察</small>':''}`,fmt(r.win_rate)+'%',`<span class="${sign(r.mean)}">${fmt(r.mean)}</span>`,fmt(r.mean_win),fmt(r.mean_loss),fmt(r.mean_return)+'%']));
 if(!settingsLoaded){const f=$('#settings-form');f.elements.initial.value=s.settings.initial;Object.entries(s.settings.fees).forEach(([k,v])=>f.elements[k].value=v);settingsLoaded=true;}
}
async function refresh(){state=await api('/api/state');render();await loadDaily();}
function formAction(selector,action){$(selector).addEventListener('submit',async e=>{e.preventDefault();const buttons=[...e.target.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);try{await action(e.target);await refresh();}catch(err){notice(err.message,true);}finally{buttons.forEach(b=>b.disabled=false);}});}
let stockVersion=0, stockTimer, matchedStock=null;
// 每次输入、清空或更正均使旧请求失效，避免异步查询覆盖当前股票。
function clearStockSearch(){stockVersion++;clearTimeout(stockTimer);$('#stock-matches').replaceChildren();$('#stock-hint').textContent='输入代码或名称可自动匹配，也可手工填写。';}
function chooseStock(stock){clearStockSearch();const f=$('#trade-form');f.elements.code.value=stock.code;f.elements.name.value=stock.name;matchedStock=stock;}
// 仅完整代码或唯一的完整名称自动补齐；模糊查询必须由用户选择。
for(const field of ['code','name']){
 const input=$('#trade-form').elements[field];
 input.addEventListener('input',e=>{
  clearStockSearch();
  const other=field==='code'?'name':'code';
  if(matchedStock&&$('#trade-form').elements[other].value===matchedStock[other])$('#trade-form').elements[other].value='';
  matchedStock=null;
  if(e.isComposing)return;
  const query=input.value.trim(),version=stockVersion;
  if(!query)return;
  stockTimer=setTimeout(async()=>{
   $('#stock-hint').textContent='正在查找股票…';
   try{
    const result=await api('/api/stocks?q='+encodeURIComponent(query));
    if(version!==stockVersion)return;
    const exact=result.items.filter(item=>item[field]===query);
    if(exact.length===1&&!result.warning){chooseStock(exact[0]);$('#stock-hint').textContent='已匹配 '+exact[0].code+' · '+exact[0].name;return;}
    $('#stock-hint').textContent=result.warning||(result.items.length?'请选择股票：':'未找到匹配股票，请核对或手工填写。');
    for(const item of result.items){const button=document.createElement('button');button.type='button';button.textContent=item.code+' · '+item.name;button.addEventListener('click',()=>chooseStock(item));$('#stock-matches').append(button);}
   }catch(err){if(version===stockVersion)$('#stock-hint').textContent='股票查询暂不可用，请手工填写代码和名称。';}
  },250);
 });
 input.addEventListener('compositionend',()=>input.dispatchEvent(new Event('input')));
}
function resetTrade(){clearStockSearch();matchedStock=null;const f=$('#trade-form');f.reset();f.elements.id.value='';$('#trade-heading').textContent='记录成交';feeVisibility();tradeDirectionVisibility();dates();f.elements.at.value=$('#trade-day').value+'T'+localTime().slice(11);}
// 按买卖方向同步预期价格的显示、校验和提交状态，临时切换时保留输入值。
function tradeDirectionVisibility(){const f=$('#trade-form'),buy=f.elements.side.value==='buy';$('#expected-field').classList.toggle('hidden',!buy);f.elements.expected.required=buy;f.elements.expected.disabled=!buy;}
$('#trade-form').elements.side.addEventListener('change',tradeDirectionVisibility);
function feeVisibility(){const actual=$('#fee-mode').value==='actual';$('#actual-fees').classList.toggle('hidden',!actual);['stamp','transfer','commission','other'].forEach(k=>$('#trade-form').elements[k].required=actual);}
$('#fee-mode').addEventListener('change',feeVisibility);$('#cancel-edit').addEventListener('click',resetTrade);
formAction('#trade-form',async form=>{const data=values(form);const id=data.id;$('#trade-day').value=data.at.slice(0,10);const result=await api(id?'/api/trades/'+id:'/api/trades',id?'PUT':'POST',data);const file=$('#trade-image').files[0];resetTrade();notice('成交已保存，资金账本已更新。');if(file){const upload=new FormData();upload.append('image',file);try{await api('/api/trades/'+result.id+'/images','POST',upload);}catch(err){notice('成交已保存，但截图上传失败：'+err.message+'。请更正该笔交易后重新附图。',true);}}});
document.addEventListener('click',async e=>{const edit=e.target.closest('[data-edit]'),del=e.target.closest('[data-delete]');try{if(edit){const trade=state.events.find(x=>x.id===Number(edit.dataset.edit));const f=$('#trade-form');resetTrade();for(const [k,v] of Object.entries(trade)){if(f.elements[k]&&k!=='fees')f.elements[k].value=Array.isArray(v)?v.join(','):v??'';}Object.entries(trade.fees).forEach(([k,v])=>f.elements[k].value=v);$('#trade-heading').textContent='更正成交 #'+trade.id;feeVisibility();tradeDirectionVisibility();window.scrollTo({top:0,behavior:'smooth'});}if(del&&confirm('删除这条记录并重新计算账本？如果导致后续资金或持仓不成立，系统会拒绝删除。')){await api('/api/events/'+del.dataset.delete,'DELETE');notice('记录已删除。');await refresh();}}catch(err){notice(err.message,true);}});
formAction('#settings-form',async form=>{const data=values(form),initial=data.initial;delete data.initial;await api('/api/settings','POST',{initial,fees:data});notice('配置已保存；原交易费用快照保持不变。');});
formAction('#cash-form',async form=>{await api('/api/cash','POST',values(form));form.reset();dates();notice('资金变动已保存。');});
formAction('#adjustment-form',async form=>{await api('/api/adjustments','POST',values(form));form.reset();dates();notice('公司行为调整已保存，请结合交割单核对。');});
let dailyVersion=0, noteDay='', noteDirty=false;
// 日期切换和轮询均校验请求版本，不覆盖尚未保存的每日说明。
async function loadDaily(){
 const day=$('#trade-day').value,version=++dailyVersion;
 if(!day)return;
 const data=await api('/api/daily/'+encodeURIComponent(day));if(version!==dailyVersion)return;
 $('#daily-metrics').innerHTML=[['成交笔数 / 股票数',data.trade_count+' / '+data.stock_count],['买入金额',fmt(data.buy_amount)],['卖出金额',fmt(data.sell_amount)],['交易费用',fmt(data.fees)],['卖出兑现盈亏',fmt(data.sell_realized)],['账户日盈亏',data.account_pnl===null?'暂无法计算':fmt(data.account_pnl)],['日初现金',fmt(data.opening.cash)],['日末现金',fmt(data.ending.cash)]].map(([label,value])=>`<div class="metric"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join('');
 $('#daily-positions-title').textContent=data.closed?day+' 收盘持仓':day+' 当日持仓（尚未收盘）';
 // 收盘估值只展示当日价格；日初参考估值独立列出日期，不混用两种时点。
 const closingValue=(p,key)=>!data.closed?'待收盘':p[key]===null||p[key]===undefined?'缺少当日收盘行情':fmt(p[key]);
 $('#daily-positions').innerHTML=data.ending.positions.length?table(['股票','剩余股数','剩余含费成本（元）','当日收盘价（元）','当日收盘市值（元）','持仓浮动盈亏（元）'],data.ending.positions.map(p=>[esc(p.name)+' · '+esc(p.code),p.qty,fmt(p.cost),closingValue(p,'quote_price'),closingValue(p,'value'),closingValue(p,'unrealized')])):'<div class="empty">'+(data.closed?'当日收盘无持仓。':'当前无持仓。')+'</div>';
 $('#daily-opening-positions').innerHTML=data.opening.positions.length?table(['股票','日初股数','含费成本（元）','估值日期','参考收盘价（元）','日初参考市值（元）'],data.opening.positions.map(p=>[esc(p.name)+' · '+esc(p.code),p.qty,fmt(p.cost),esc(p.quote_at?p.quote_at.slice(0,10):'未取得估值'),p.quote_price==null?'缺少参考行情':fmt(p.quote_price),p.value==null?'缺少参考行情':fmt(p.value)])):'<div class="empty">日初无持仓。</div>';
 $('#daily-valuation').textContent=data.valuation_notice+(data.previous_valuation_date?' 前值日期：'+data.previous_valuation_date:' 尚未取得前一交易日基准。');
 if(noteDay!==day||!noteDirty){$('#daily-note').value=data.note;noteDay=day;noteDirty=false;}
}
$('#daily-note').addEventListener('input',()=>noteDirty=true);
formAction('#daily-note-form',async()=>{const day=$('#trade-day').value,note=$('#daily-note').value;await api('/api/daily/'+day+'/note','PUT',{note});if(day===$('#trade-day').value&&note===$('#daily-note').value)noteDirty=false;notice('今日说明已保存。');});
$('#trade-day').value=localTime().slice(0,10);$('#trade-day').max=localTime().slice(0,10);
$('#review-day').value=$('#trade-day').value;$('#review-day').max=$('#trade-day').max;
$('#trade-day').addEventListener('change',()=>{
 if(noteDirty&&!confirm('今日说明尚未保存，切换日期会丢弃未保存内容。继续？')){$('#trade-day').value=noteDay;return;}
 if(!$('#trade-day').reportValidity())return;
 noteDirty=false;resetTrade();$('#review-day').value=$('#trade-day').value;if(state)render();loadDaily().catch(e=>notice(e.message,true));
});
$('#all-transactions').addEventListener('change',()=>{if(state)render();});
// 整日模式不要求选择股票和行业；单股模式沿用原有周期复盘。
function reviewScopeVisibility(){const daily=$('#review-scope').value==='day';$('#review-day-field').classList.toggle('hidden',!daily);$('#review-day').disabled=!daily;$('#review-day').required=daily;$('#review-stock-field').classList.toggle('hidden',daily);$('#review-stock').disabled=daily;$('#review-stock').required=!daily;$('#review-sector-field').classList.toggle('hidden',daily);$('#review-form').elements.sector.disabled=daily;}
$('#review-scope').addEventListener('change',reviewScopeVisibility);
$('#review-this-day').addEventListener('click',()=>{if(noteDirty){notice('请先保存今日说明，再复盘这一天。',true);return;}$('#review-scope').value='day';$('#review-day').value=$('#trade-day').value;reviewScopeVisibility();tab('reviews');});
reviewScopeVisibility();
let reviewOptions;
// 仅在本浏览器记住选择；任务提交时固定配置，避免后续切换影响排队任务。
function saveReviewChoice(){try{localStorage.setItem('stock-review-ai',JSON.stringify({model:$('#review-model').value,effort:$('#review-effort').value}));}catch{}}
function updateReviewEfforts(preferred){const levels=reviewOptions.models[$('#review-model').value];$('#review-effort').innerHTML=levels.map(x=>`<option value="${esc(x)}">${esc({low:'低',medium:'中',high:'高',xhigh:'很高',max:'最大',ultra:'超高'}[x])} (${esc(x)})</option>`).join('');$('#review-effort').value=levels.includes(preferred)?preferred:'high';}
async function loadReviewOptions(){
 reviewOptions=await api('/api/review-options');let saved={};try{saved=JSON.parse(localStorage.getItem('stock-review-ai')||'{}')||{};}catch{}
 $('#review-model').innerHTML=Object.keys(reviewOptions.models).map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');
 $('#review-model').value=reviewOptions.models[saved.model]?saved.model:reviewOptions.defaults.model;
 updateReviewEfforts(saved.effort||reviewOptions.defaults.effort);
 $('#review-config-hint').textContent='选择自动记在当前浏览器。高强度通常更慢、占用更多额度；模型是否可用取决于账号权限，失败不会自动换模型。';
}
$('#review-model').addEventListener('change',()=>{updateReviewEfforts($('#review-effort').value);saveReviewChoice();});
$('#review-effort').addEventListener('change',saveReviewChoice);
loadReviewOptions().catch(()=>{$('#review-config-hint').textContent='模型选项加载失败，请刷新页面重试。';});
async function submitReview(review){const f=$('#review-form');if(!f.reportValidity())return;const data=values(f);data.review=review;data.include_images=f.elements.include_images.checked;await api('/api/reviews','POST',data);notice(review?'复盘任务已提交，可继续记账。':'行情更新已提交。');}
formAction('#review-form',async()=>submitReview(true));$('#market-only').addEventListener('click',async()=>{try{await submitReview(false);await refresh();}catch(err){notice(err.message,true);}});
formAction('#restore-form',async form=>{if(!confirm('恢复会替换当前账本。系统将先保留当前数据的回滚备份，是否继续？'))return;const result=await api('/api/restore','POST',new FormData(form));settingsLoaded=false;notice('恢复成功。恢复前备份：'+result.rollback);});
dates();feeVisibility();tradeDirectionVisibility();refresh().catch(e=>notice(e.message,true));setInterval(()=>refresh().catch(()=>{}),6000);
