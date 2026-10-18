/* 整日录入与复盘验收：只连接隔离测试服务器。 */
const {chromium}=require('/Applications/ChatGPT.app/Contents/Resources/cua_node/lib/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try{
 const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8766');await page.locator('nav [data-tab=trades]').click();
 await page.locator('#trade-day').fill('2026-10-12');await page.locator('#trade-day').dispatchEvent('change');
 await page.waitForFunction(()=>document.querySelector('#trade-form').elements.at.value.startsWith('2026-10-12'));
 for(const [code,name,time] of [['603230','内蒙新华','10:00'],['000001','平安银行','11:00']]){
  const f=page.locator('#trade-form');await f.locator('[name=code]').fill(code);await f.locator('[name=name]').fill(name);
  await f.locator('[name=at]').fill('2026-10-12T'+time);await f.locator('[name=price]').fill('10');await f.locator('[name=qty]').fill('100');await f.locator('[name=expected]').fill('11');
  const response=page.waitForResponse(r=>r.url().endsWith('/api/trades')&&r.request().method()==='POST');await f.locator('[type=submit]').click();assert.equal((await response).status(),200);
  await page.waitForFunction(()=>document.querySelector('#trade-form').elements.code.value==='');
  assert.equal(await page.locator('#trade-day').inputValue(),'2026-10-12');
 }
 await page.waitForFunction(()=>document.querySelectorAll('#transactions tbody tr').length===2);
 await page.locator('#daily-note').fill('今天买入两只股票，观察资金分配。');
 await page.locator('#daily-note-form button').first().click();await page.locator('#notice').filter({hasText:'今日说明已保存'}).waitFor();
 await page.locator('#trade-day').fill('2026-10-13');await page.locator('#trade-day').dispatchEvent('change');
 await page.waitForFunction(()=>document.querySelector('#transactions').textContent.includes('还没有记录'));
 await page.locator('#daily-positions').getByText('000001',{exact:false}).first().waitFor();
 await page.locator('#trade-day').fill('2026-10-12');await page.locator('#trade-day').dispatchEvent('change');
 await page.waitForFunction(()=>document.querySelector('#daily-note').value.includes('两只股票'));
 await page.locator('#review-this-day').click();assert.equal(await page.locator('#review-scope').inputValue(),'day');assert.equal(await page.locator('#review-day').inputValue(),'2026-10-12');assert.equal(await page.locator('#review-stock').isVisible(),false);
 const request=page.waitForRequest(r=>r.url().endsWith('/api/reviews')&&r.method()==='POST');await page.locator('#review-form [type=submit]').click();const body=(await request).postDataJSON();assert.equal(body.scope,'day');assert.equal(body.date,'2026-10-12');assert.equal('code' in body,false);
 await page.locator('#reports h2').filter({hasText:'2026-10-12 全天'}).waitFor({timeout:20000});
 const href=await page.locator('#reports a').first().getAttribute('href');const evidence=await (await page.request.get('http://127.0.0.1:8766'+href)).json();assert.equal(evidence.daily.stock_count,2);assert.equal(evidence.daily.trade_count,2);assert.ok(evidence.daily.note.includes('两只股票'));
 await page.locator('#review-scope').selectOption('stock');assert.equal(await page.locator('#review-stock').isVisible(),true);assert.equal(await page.locator('#review-day').isVisible(),false);
 assert.deepEqual(errors,[]);console.log('PASS: 每日多股录入、保留日期、日初持仓、每日说明、整日报告和证据、单股切换');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
