/* 浏览器端端到端验收；配合 browser_server.py 使用隔离数据。 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '/Applications/ChatGPT.app/Contents/Resources/cua_node/lib/node_modules/playwright');
const assert = require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const page=await browser.newPage({viewport:{width:1440,height:1100}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8766');
 await page.locator('#metrics').getByText('10,000.00').first().waitFor();
 await page.locator('nav [data-tab="trades"]').click();
 const expected=page.locator('#trade-form [name=expected]');
 const direction=page.locator('#trade-form [name=side]');
 async function checkExpected(buy){
  assert.equal(await page.locator('#expected-field').isVisible(),buy);
  assert.equal(await expected.isEnabled(),buy);
  assert.equal(await expected.evaluate(e=>e.required),buy);
  assert.equal(await page.locator('#trade-form').evaluate(f=>new FormData(f).has('expected')),buy);
 }
 await checkExpected(true);
 await expected.fill('15');await direction.selectOption('sell');await checkExpected(false);
 await direction.selectOption('buy');await checkExpected(true);assert.equal(await expected.inputValue(),'15');
 await direction.selectOption('sell');await page.locator('#cancel-edit').click();
 await checkExpected(true);assert.equal(await expected.inputValue(),'');
 async function fill(side,at,price,stamp){
  const f=page.locator('#trade-form');
  await f.locator('[name=code]').fill('603230');await f.locator('[name=name]').fill('内蒙新华');
  await f.locator('[name=side]').selectOption(side);await f.locator('[name=at]').fill(at.slice(0,16));
  await f.locator('[name=price]').fill(price);await f.locator('[name=qty]').fill('200');await checkExpected(side==='buy');
  await f.locator('[name=fee_mode]').selectOption('actual');
  for(const [k,v] of Object.entries({stamp,transfer:'0.03',commission:'4.84',other:'0.16'}))await f.locator(`[name=${k}]`).fill(v);
  if(side==='buy'){
   assert.equal(await expected.evaluate(e=>e.validity.valueMissing),true);
   await f.locator('[type=submit]').click();
   assert.equal(await expected.evaluate(e=>e.matches(':invalid')),true);
   await expected.fill('15');
  }
  const response=page.waitForResponse(r=>r.url().endsWith('/api/trades')&&r.request().method()==='POST');
  await f.locator('[type=submit]').click();const saved=await response;assert.equal(saved.status(),200);assert.equal(Object.hasOwn(saved.request().postDataJSON(),'expected'),side==='buy');
  await page.locator('#notice').filter({hasText:'成交已保存'}).waitFor();
 }
 await fill('buy','2026-10-12T10:00:00','14.2','0');
 await fill('sell','2026-10-13T10:00:00','14.4','1.44');
 await page.locator('nav [data-tab="overview"]').click();
 await page.locator('#metrics').getByText('10,028.50').first().waitFor();
 await page.locator('nav [data-tab="trades"]').click();
 await page.locator('[data-edit="2"]').click();await checkExpected(false);
 const sellEdit=page.waitForResponse(r=>r.url().endsWith('/api/trades/2')&&r.request().method()==='PUT');
 await page.locator('#trade-form [type=submit]').click();
 const sellSaved=await sellEdit;assert.equal(sellSaved.status(),200);assert.equal(Object.hasOwn(sellSaved.request().postDataJSON(),'expected'),false);
 await page.waitForFunction(()=>document.querySelector('#trade-form').elements.id.value==='');
 await checkExpected(true);
 await page.locator('#all-transactions').check();
 // 更正仍保持原始费用，并上传截图。
 await page.locator('[data-edit="1"]').click();await checkExpected(true);assert.equal(await expected.inputValue(),'15');
 await page.locator('#trade-form details').evaluate(e=>e.open=true);
 const png=await page.screenshot();await page.locator('#trade-image').setInputFiles({name:'test.png',mimeType:'image/png',buffer:png});
 await page.locator('#trade-form [name=note]').fill('浏览器测试：事后补充');
 await page.locator('#trade-form [type=submit]').click();
 await page.locator('#transactions a').filter({hasText:'截图'}).waitFor();
 await page.locator('nav [data-tab="reviews"]').click();
 await page.locator('#review-model').selectOption('gpt-6-astra');
 await page.locator('#review-effort').selectOption('xhigh');
 await page.reload();await page.locator('nav [data-tab="reviews"]').click();
 await page.waitForFunction(()=>document.querySelector('#review-effort').value==='xhigh');
 assert.equal(await page.locator('#review-model').inputValue(),'gpt-6-astra');
 await page.waitForFunction(()=>document.querySelector('#review-stock').value==='603230');
 await page.locator('#review-scope').selectOption('stock');
 const reviewRequest=page.waitForRequest(r=>r.url().endsWith('/api/reviews')&&r.method()==='POST');
 await page.locator('#review-form [type=submit]').click();
 const config=(await reviewRequest).postDataJSON();assert.equal(config.model,'gpt-6-astra');assert.equal(config.effort,'xhigh');
 await page.locator('#reports').getByText('浏览器验收模拟报告：本周期净赚28.50元。').waitFor({timeout:20000});
 assert.ok((await page.locator('#reports').innerText()).includes('gpt-6-astra / xhigh'));
 await page.locator('nav [data-tab="stats"]').click();
 await page.locator('#statistics').getByText('28.50').first().waitFor();
 await page.locator('nav [data-tab="settings"]').click();
 const dl=page.waitForEvent('download');await page.getByText('下载完整备份 ZIP').click();
 const download=await dl;const backup=await download.path();
 await page.locator('#cash-form [name=amount]').fill('100');
 await page.locator('#cash-form [name=at]').fill('2026-10-14T10:00');
 await page.locator('#cash-form button').click();await page.locator('#notice').filter({hasText:'资金变动已保存'}).waitFor();
 await page.locator('#restore-form input').setInputFiles(backup);
 page.once('dialog',dialog=>dialog.accept());
 await page.locator('#restore-form button').click();await page.locator('#notice').filter({hasText:'恢复成功'}).waitFor();
 await page.locator('nav [data-tab="overview"]').click();
 await page.locator('#metrics').getByText('10,028.50').first().waitFor();
 await page.screenshot({path:'artifacts/browser-overview.png',fullPage:true});
 assert.deepEqual(errors,[]);
 await browser.close();console.log('PASS: 浏览器录入、更正、截图、复盘、统计、备份恢复；无 JavaScript 错误');
})().catch(e=>{console.error(e);process.exit(1)});
