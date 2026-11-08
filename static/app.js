'use strict';
const $ = id => document.getElementById(id);
const rub = v => new Intl.NumberFormat('ru-RU', {style:'currency',currency:'RUB',maximumFractionDigits:0}).format(v);
const pct = v => new Intl.NumberFormat('ru-RU', {style:'percent',maximumFractionDigits:1}).format(v);
const regimes = {normal:'Спокойно', caution:'Внимание', storm:'Шторм'};
let data, result, busy = false, toastTimer, lastBacktest;

async function api(path, payload) {
  const response = await fetch(path, payload === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || 'Ошибка сервера');
  return body;
}
function notify(message) {
  $('toast').textContent = message; $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 4500);
}
function error(message) { $('error').textContent = message; $('error').hidden = !message; }
function setScenarioEnabled(enabled) {
  $('market-shock').disabled = !enabled;
  document.querySelectorAll('[data-shock]').forEach(b => b.disabled = !enabled);
}
function invalidate() {
  result = null; $('execute').disabled = true;
  setScenarioEnabled(false);
  $('scenario-stale').hidden = false;
  $('hero-status').textContent = 'НУЖЕН НОВЫЙ РАСЧЕТ';
}
function node(tag, text, cls) {
  const n = document.createElement(tag); if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls; return n;
}
function syncInputs() {
  data.free_cash = Number($('cash').value);
  data.settings = {max_hedge:Number($('max-hedge').value)/100, margin_buffer:Number($('margin-buffer').value), cost_bps:Number($('cost-bps').value)};
  data.contract = {point_value:Number($('point-value').value), margin:Number($('contract-margin').value)};
  document.querySelectorAll('[data-ticker]').forEach(input => {
    const position = data.positions.find(p => p.ticker === input.dataset.ticker);
    if (position) position.quantity = Number(input.value);
  });
}
function portfolioTable() {
  const last = data.history.at(-1);
  const total = data.positions.reduce((s,p) => s + p.quantity * last[p.ticker], 0);
  $('positions').replaceChildren(...data.positions.map(p => {
    const row = node('tr'); row.append(node('td',p.ticker));
    const cell = node('td'), input = node('input'); input.type='number'; input.min='0.000001'; input.step='any'; input.value=p.quantity; input.dataset.ticker=p.ticker;
    input.setAttribute('aria-label',`Количество ${p.ticker}`); input.addEventListener('input', invalidate);
    cell.append(input); row.append(cell,node('td',rub(last[p.ticker])),node('td',pct(p.quantity*last[p.ticker]/total))); return row;
  }));
}
function renderAnalysis(r) {
  const m = r.metrics, p = r.plan;
  $('portfolio-value').textContent = rub(m.value);
  $('beta').textContent = m.beta.toFixed(2);
  $('beta-fit').textContent = `Корреляция с индексом: ${m.fit.toFixed(2)}`;
  $('coverage').textContent = pct(p.coverage);
  $('contract-count').textContent = `Цель: ${p.target} контрактов · сейчас: ${p.current}`;
  $('margin').textContent = rub(p.reserved);
  $('available').textContent = `Базовое ГО: ${rub(p.margin)}`;
  $('asof').textContent = `${r.source === 'synthetic' ? 'Синтетический пример' : 'Импорт CSV'} · на ${r.as_of}`;
  $('regime').textContent = regimes[r.regime]; $('regime').className = `badge ${r.regime}`;
  $('risk-meter').querySelectorAll('i').forEach((bar,i) => bar.classList.toggle('on',i < {normal:1,caution:2,storm:3}[r.regime]));
  $('hero-status').textContent = {normal:'РЫНОК СПОКОЕН',caution:'РЫНОК ТРЕБУЕТ ВНИМАНИЯ',storm:'РЫНОК НЕСПОКОЕН'}[r.regime];
  $('hero-title').textContent = p.delta > 0 ? 'Добавьте защиту, сохранив акции' : p.delta < 0 ? 'Сократите хедж, сохранив акции' : p.target ? 'Защита уже в вашем портфеле' : 'Ваш портфель — без лишнего хеджа';
  $('hero-description').textContent = p.target ? `Цель — ${p.target} контрактов. Хедж покрывает ${pct(p.coverage)} рыночного риска по бете. Резерв с запасом: ${rub(p.reserved)}.` : 'По текущему расчету короткая позиция не требуется или ограничена правилами. Подробности — в расчете робота.';
  $('vol').textContent = pct(m.volatility); $('correlation').textContent = m.correlation.toFixed(2); $('drawdown').textContent = pct(m.drawdown);
  $('decision-title').textContent = p.delta > 0 ? 'Добавить защиту портфеля' : p.delta < 0 ? 'Сократить текущий хедж' : p.target ? 'Сохранить текущую защиту' : 'Оставить портфель без хеджа';
  $('decision-description').textContent = r.regime === 'storm' ? 'Риск повышен. Размер короткой позиции рассчитан по бете, лимиту защиты и доступному обеспечению.' : r.regime === 'caution' ? 'Рынок требует внимания. Модель рассматривает частичный хедж в пределах заданных ограничений.' : 'Сигналы нормализовались. Модель снимает хедж, чтобы сохранить участие в росте рынка.';
  $('order-direction').textContent = p.action === 'SELL' ? 'Продать для открытия / увеличения хеджа' : p.action === 'BUY' ? 'Купить для сокращения / закрытия хеджа' : 'Изменение позиции не требуется';
  $('order-quantity').textContent = Math.abs(p.delta);
  $('hedge-notional').textContent = rub(p.hedge_notional); $('cost').textContent = rub(p.cost);
  $('reasons').replaceChildren(...r.reasons.map(t => node('li',t)));
  const warnings = [...p.warnings]; if (!r.paper_compatible) warnings.push('Снимок данных изменился. Для применения сбросьте виртуальный счет.');
  $('warnings').hidden = !warnings.length; $('warnings').textContent = warnings.join(' · ');
  $('execute').disabled = !p.delta || !r.paper_compatible;
  $('scenario-stale').hidden = true;
  setScenarioEnabled(true);
  renderScenario();
}
function renderScenario() {
  if (!result) return;
  const shock = Number($('market-shock').value) / 100;
  const {metrics:m,plan:p} = result;
  const unhedged = m.value * m.beta * shock;
  const hedged = unhedged - p.hedge_notional * shock - p.cost;
  const signed = v => (v > 0 ? '+' : '') + rub(v);
  $('shock-value').textContent = (shock > 0 ? '+' : '') + pct(shock);
  $('scenario-unhedged').textContent = signed(unhedged);
  $('scenario-hedged').textContent = signed(hedged);
  $('scenario-portfolio-value').textContent = rub(m.value);
  $('scenario-beta').textContent = `Бета ${m.beta.toFixed(2)}`;
  $('scenario-source').textContent = result.source === 'synthetic' ? 'Синтетический пример' : 'Данные из CSV';
  $('scenario-requirements').textContent = `${p.target} контрактов · ${rub(p.reserved)} ГО с запасом`;
  const difference = hedged - unhedged;
  $('scenario-explanation').textContent = shock === 0 ? `При неизменном рынке учитываются только издержки изменения хеджа: ${rub(p.cost)}.` :
    difference > .5 ? `В этом сценарии результат с целевым хеджем лучше на ${rub(difference)}. Оценка включает издержки изменения позиции.` :
    difference < -.5 ? `В этом сценарии результат с хеджем меньше на ${rub(-difference)}. Защита может ограничивать прибыль и требует издержек.` :
    'В этом сценарии результаты совпадают: целевой хедж не меняет рыночную экспозицию.';
  const scale = Math.max(Math.abs(unhedged),Math.abs(hedged),1);
  $('unhedged-bar').style.width = `${Math.abs(unhedged)/scale*100}%`;
  $('hedged-bar').style.width = `${Math.abs(hedged)/scale*100}%`;
  document.querySelectorAll('[data-shock]').forEach(b => b.setAttribute('aria-pressed',String(Number(b.dataset.shock) === shock*100)));
}
function renderChart(bt) {
  lastBacktest = bt;
  const svg = $('chart'), ns='http://www.w3.org/2000/svg'; svg.replaceChildren();
  const values=bt.curve.flatMap(p=>[p.hedged,p.unhedged]);
  const size = Math.max(240,svg.getBoundingClientRect().width);
  const mobile = size < 500, chartHeight = mobile ? 210 : 250;
  svg.setAttribute('viewBox',`0 0 ${size} ${chartHeight}`);
  const left = mobile ? 56 : 70, right = size-15, bottom = chartHeight-32;
  const min=Math.min(...values)*.99, max=Math.max(...values)*1.01;
  const x=i=>left+i/(bt.curve.length-1)*(right-left), y=v=>12+(max-v)/(max-min)*(bottom-12);
  function shape(tag,attrs,text) {const e=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);if(text!==undefined)e.textContent=text;svg.append(e);return e;}
  for(let i=0;i<4;i++){const v=min+(max-min)*i/3, yy=y(v);shape('line',{x1:left,x2:right,y1:yy,y2:yy,stroke:'#edf0f5'});shape('text',{x:0,y:yy+3},`${Math.round(v/1000)} тыс.`);}
  const path=key=>bt.curve.map((p,i)=>`${i?'L':'M'}${x(i).toFixed(2)},${y(p[key]).toFixed(2)}`).join(' ');
  shape('path',{d:path('hedged')+` L${right},${bottom} L${left},${bottom} Z`,fill:'#fff9df',opacity:'.8'});
  shape('path',{d:path('unhedged'),fill:'none',stroke:'#b6beca','stroke-width':2,'stroke-dasharray':'5 4'});
  shape('path',{d:path('hedged'),fill:'none',stroke:'#c2a116','stroke-width':2.5});
  const ticks = mobile ? 3 : 5;
  for(let i=0;i<ticks;i++){const ix=Math.round(i*(bt.curve.length-1)/(ticks-1));shape('text',{x:x(ix),y:chartHeight-7,'text-anchor':i===0?'start':i===ticks-1?'end':'middle'},mobile?bt.curve[ix].date.slice(5):bt.curve[ix].date);}
  const s=bt.summary;
  $('bt-hedged-dd').textContent=pct(s.hedged_drawdown);$('bt-base-dd').textContent=pct(s.unhedged_drawdown);
  $('bt-difference').textContent=(s.difference>0?'+':'')+rub(s.difference);$('bt-cost').textContent=rub(s.costs);
}
async function journal() {
  const account = await api('/api/paper');
  $('journal-items').replaceChildren(...(account.journal.length ? account.journal.map(entry=>{
    const row=node('div',undefined,'journal-row');
    const detail=entry.detail;
    row.append(node('small',new Date(entry.created*1000).toLocaleString('ru-RU')),
      node('span',entry.kind==='reset'?'Виртуальный счет сброшен':`${detail.delta>0?'Продажа':'Покупка'} ${Math.abs(detail.delta)} контр. · позиция ${detail.contracts}`),
      node('strong',entry.kind==='reset'?'':`Издержки ${rub(detail.cost)}`));return row;
  }):[node('div','Пока нет сделок. Рассчитайте и примените виртуальный хедж.','empty')]));
}
async function calculate() {
  syncInputs(); invalidate();
  const snapshot=JSON.parse(JSON.stringify(data));
  const [analysis,bt]=await Promise.all([api('/api/analyze',snapshot),api('/api/backtest',snapshot)]);
  result=analysis; renderAnalysis(analysis); renderChart(bt); portfolioTable(); await journal();
}
async function run(work) {
  if(busy)return;
  busy=true; error(''); $('recalculate').disabled=true; $('execute').disabled=true;
  const controls=[...document.querySelectorAll('input,button[data-scenario],button[data-shock],#reset,#csv,#sandbox')];controls.forEach(e=>e.disabled=true);
  try{await work();}catch(e){invalidate();error(e.message);}finally{
    busy=false;$('recalculate').disabled=false;controls.forEach(e=>e.disabled=false);
    setScenarioEnabled(Boolean(result));
    $('execute').disabled=!result||!result.plan.delta||!result.paper_compatible;
  }
}
async function loadScenario(scenario) {
  data=await api(`/api/demo?scenario=${encodeURIComponent(scenario)}`);
  $('cash').value=data.free_cash;$('max-hedge').value=data.settings.max_hedge*100;$('contract-margin').value=data.contract.margin;
  $('point-value').value=data.contract.point_value;$('margin-buffer').value=data.settings.margin_buffer;$('cost-bps').value=data.settings.cost_bps;
  document.querySelectorAll('[data-scenario]').forEach(b=>{b.classList.toggle('selected',b.dataset.scenario===scenario);b.setAttribute('aria-pressed',String(b.dataset.scenario===scenario));});
  portfolioTable();await calculate();
}
$('recalculate').addEventListener('click',()=>run(calculate));
document.querySelectorAll('[data-scenario]').forEach(b=>b.addEventListener('click',()=>run(()=>loadScenario(b.dataset.scenario))));
document.querySelectorAll('.settings-grid input').forEach(i=>i.addEventListener('input',invalidate));
$('market-shock').addEventListener('input',renderScenario);
document.querySelectorAll('[data-shock]').forEach(b=>b.addEventListener('click',()=>{
  $('market-shock').value = b.dataset.shock; renderScenario();
}));
document.querySelectorAll('[data-open-decision]').forEach(b=>b.addEventListener('click',()=>{
  $('decision').open = true;
  $('decision').scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});
}));
new ResizeObserver(()=>{if(lastBacktest)renderChart(lastBacktest);}).observe($('chart').parentElement);
function updateNavigation() {
  const section = window.location.hash || '#overview';
  document.querySelectorAll('nav a').forEach(a=>{
    a.classList.toggle('active',a.getAttribute('href')===section);
    if(a.getAttribute('href')===section)a.setAttribute('aria-current','location');else a.removeAttribute('aria-current');
  });
}
window.addEventListener('hashchange',updateNavigation);updateNavigation();
$('execute').addEventListener('click',()=>run(async()=>{
  const fill=await api('/api/paper/execute',{proposal_id:result.proposal_id});
  notify(`Виртуальный хедж применен: ${fill.contracts} контрактов`);await calculate();
}));
$('reset').addEventListener('click',()=>run(async()=>{await api('/api/paper/reset',{});await calculate();notify('Виртуальный счет сброшен. Журнал сохранен.');}));
$('download').addEventListener('click',()=>{
  const keys=['date','index','future',...data.positions.map(p=>p.ticker),'event'];
  const csv=keys.join(',')+'\n'+data.history.map(r=>keys.map(k=>r[k]??0).join(',')).join('\n');
  const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
  const a=node('a');a.href=url;a.download='hedger-history-example.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
function parseCSV(text) {
  const lines=text.replace(/^\uFEFF/,'').trim().split(/\r?\n/);
  if(lines.length<42||lines.length>3001)throw new Error('В CSV нужно от 41 до 3000 дневных строк');
  const delimiter=lines[0].includes(';')?';':',';
  const headers=lines[0].split(delimiter).map(s=>s.trim());
  if(new Set(headers).size!==headers.length||!['date','index','future'].every(k=>headers.includes(k)))throw new Error('CSV должен содержать уникальные колонки date,index,future и тикеры акций');
  const tickers=headers.filter(k=>!['date','index','future','event'].includes(k));
  if(!tickers.length||tickers.some(t=>!/^[A-Za-z0-9_]{1,20}$/.test(t)))throw new Error('Добавьте колонки цен с латинскими тикерами акций');
  const history=lines.slice(1).map((line,i)=>{
    const cells=line.split(delimiter).map(s=>s.trim());if(cells.length!==headers.length)throw new Error(`CSV: неверное число ячеек в строке ${i+2}`);
    return Object.fromEntries(headers.map((k,j)=>{if(!cells[j])throw new Error(`Пустая ячейка ${k}, строка ${i+2}`);return[k,k==='date'?cells[j]:Number(cells[j])];}));
  });
  return {history,positions:tickers.map(ticker=>({ticker,quantity:data.positions.find(p=>p.ticker===ticker)?.quantity||1}))};
}
$('csv').addEventListener('change',()=>{
  const file=$('csv').files[0]; if(!file)return;
  run(async()=>{
    if(file.size>3_000_000)throw new Error('Файл больше 3 МБ');
    syncInputs();const imported=parseCSV(await file.text());
    const candidate={...data,...imported,source:'import'};
    // Validate on the server before replacing the working dataset.
    await api('/api/analyze',candidate);data=candidate;portfolioTable();
    document.querySelectorAll('[data-scenario]').forEach(b=>{b.classList.remove('selected');b.setAttribute('aria-pressed','false');});
    await calculate();notify('История импортирована. Проверьте количества акций и параметры контракта.');
  });
  $('csv').value='';
});
$('sandbox').addEventListener('click',()=>run(async()=>{
  const status=await api('/api/status');
  if(!status.sandbox_configured){$('sandbox-status').textContent='Токен не задан. На сервере задайте TINVEST_SANDBOX_TOKEN и перезапустите приложение. Подробнее — README.';return;}
  const response=await api('/api/sandbox/accounts');
  const accounts=response.accounts||[];
  $('sandbox-status').textContent=`Подключено. Открытых тестовых счетов: ${accounts.length}.`;
  const panel=$('sandbox-result');panel.hidden=false;panel.replaceChildren();
  for(const account of accounts){
    const button=node('button',`${account.name||'Тестовый счет'} · ${account.id}`,'button light');
    button.addEventListener('click',()=>run(async()=>{
      const portfolio=await api('/api/sandbox/portfolio',{account_id:account.id});
      panel.replaceChildren(node('pre',JSON.stringify(portfolio,null,2)));notify('Получен снимок тестового портфеля');
    }));panel.append(button);
  }
}));
run(()=>loadScenario('storm'));
