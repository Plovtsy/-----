const {chromium}=require('../frontend/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const base=process.env.BASE_URL||'http://127.0.0.1:8080';
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000},baseURL:base});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.locator('#regime').getByText('Шторм').waitFor();
  assert(await page.locator('#sandbox-preview').isDisabled());
  const data=await (await page.request.get('/api/demo')).json();const analysis=await (await page.request.post('/api/analyze',{data})).json();
  const format=v=>new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB',maximumFractionDigits:0}).format(v);
  for(const shock of [-15,-10,5]){await page.locator(`[data-shock="${shock}"]`).click();const expected=analysis.scenarios.find(s=>s.shock===shock/100);assert.equal(await page.locator('#scenario-unhedged').textContent(),(expected.unhedged>0?'+':'')+format(expected.unhedged));assert.equal(await page.locator('#scenario-hedged').textContent(),(expected.hedged>0?'+':'')+format(expected.hedged));}
  await page.locator('#market-shock').fill('0');assert.equal(await page.locator('#scenario-hedged').textContent(),format(-analysis.plan.cost));
  await page.locator('#cash').fill('-1');assert(await page.locator('#market-shock').isDisabled());assert(await page.locator('#scenario-stale').isVisible());await page.locator('#recalculate').click();await page.locator('#error').waitFor();
  await page.locator('#cash').fill('180000');await page.locator('#recalculate').click();await page.waitForFunction(()=>!document.querySelector('#market-shock').disabled);
  await page.getByRole('button',{name:/Посмотреть расчет/}).click();assert(await page.locator('#decision').getAttribute('open')!==null);
  await page.locator('#execute').click();await page.waitForFunction(()=>document.querySelector('#journal-items').textContent.includes('Продажа'));assert.match(await page.locator('#journal-items').textContent(),/Продажа/);
  await page.locator('#reset').click();await page.waitForFunction(()=>document.querySelector('#journal-items').textContent.includes('сброшен') && !document.querySelector('#reset').disabled);
  const keys=['date','index','future','SBER','LKOH','GAZP','YDEX','event'];const csv=keys.join(',')+'\n'+data.history.map(row=>keys.map(k=>row[k]).join(',')).join('\n');
  await page.locator('#csv').setInputFiles({name:'history.csv',mimeType:'text/csv',buffer:Buffer.from(csv)});await page.waitForFunction(()=>document.querySelector('#asof').textContent.includes('Импорт CSV'));
  await page.locator('#csv').setInputFiles({name:'bad.csv',mimeType:'text/csv',buffer:Buffer.from('bad')});await page.locator('#error').waitFor();
  await page.locator('#sandbox').click();await page.waitForFunction(()=>document.querySelector('#sandbox-status').textContent.includes('Токен не задан'));
  const commandId='00000000-0000-4000-8000-000000000002';
  await page.route('**/api/status',route=>route.fulfill({json:{mode:'demo',sandbox_configured:true,live_trading:false}}));
  await page.route('**/api/sandbox/accounts',route=>route.fulfill({json:{accounts:[{id:'dedicated',name:'Тестовый счет'}]}}));
  await page.route(/\/api\/sandbox\/futures\?/,route=>route.fulfill({json:{instruments:[{uid:'future-uid',ticker:'INDEX-F',name:'Индексный фьючерс'}]}}));
  await page.route('**/api/sandbox/preview',route=>{const request=route.request().postDataJSON();assert.equal(request.account_id,'dedicated');assert.equal(request.instrument_id,'future-uid');assert.equal(request.data.source,'import');return route.fulfill({json:{...analysis,proposal_id:'00000000-0000-4000-8000-000000000003',sandbox_instrument_id:'future-uid',plan:{...analysis.plan,current:0,target:1,delta:1,action:'SELL',warnings:[]}}});});
  await page.route('**/api/sandbox/apply-proposal',route=>{const request=route.request().postDataJSON();assert.equal(request.proposal_id,'00000000-0000-4000-8000-000000000003');return route.fulfill({json:{id:commandId,status:'pending'}});});
  await page.route('**/api/sandbox/command/*',route=>route.fulfill({json:{id:commandId,status:'filled'}}));
  await page.locator('#sandbox').click();await page.locator('#sandbox-account').waitFor();
  await page.locator('#future-query').fill('индекс');await page.locator('#future-search').click();await page.locator('#sandbox-future').selectOption('future-uid');
  await page.locator('#sandbox-preview').click();await page.locator('#sandbox-preview-result').waitFor();
  await page.locator('#sandbox-apply').click();await page.waitForFunction(()=>document.querySelector('#sandbox-command-state')?.textContent.includes('filled'));
  fs.mkdirSync('artifacts',{recursive:true});await page.screenshot({path:'artifacts/react-desktop.png',fullPage:true});
  for(const width of [390,320]){await page.setViewportSize({width,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`overflow at ${width}`);await page.screenshot({path:`artifacts/react-${width}.png`,fullPage:true});}
  assert.deepEqual(errors,[]);console.log('React UI OK: scenarios, stale lock, paper fill, CSV, Sandbox state, 390/320 px, no JS errors');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
