/* 股票双向填写验收，使用隔离服务及受控查询结果，不写入交易。 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '/Applications/ChatGPT.app/Contents/Resources/cua_node/lib/node_modules/playwright');
const assert = require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try{
  const page=await browser.newPage();
  const item={code:'603230',name:'内蒙新华'};
  await page.route('**/api/stocks?*',async route=>{
   const q=new URL(route.request().url()).searchParams.get('q');
   if(q==='600000')await new Promise(r=>setTimeout(r,650));
   const items=q==='无匹配'?[]:q==='新华'?[item,{code:'000782',name:'新测试新华'}]:q==='600000'?[{code:'600000',name:'测试银行'}]:[item];
   await route.fulfill({json:{items,warning:''}});
  });
  await page.goto('http://127.0.0.1:8766');
  await page.locator('nav [data-tab=trades]').click();
  const code=page.locator('#trade-form [name=code]'),name=page.locator('#trade-form [name=name]');
  async function value(locator,expected){await page.waitForFunction(({selector,expected})=>document.querySelector(selector).value===expected,{selector:await locator.evaluate(e=>'#trade-form [name='+e.name+']'),expected});}
  await code.fill('603230');await value(name,'内蒙新华');
  await page.locator('#cancel-edit').click();
  await name.fill('内蒙新华');await value(code,'603230');
  await name.fill('新华');await page.locator('#stock-matches button').first().waitFor();
  assert.equal(await code.inputValue(),'');assert.equal(await page.locator('#stock-matches button').count(),2);
  await page.locator('#stock-matches button').first().click();await value(code,'603230');
  await code.fill('600000');await page.waitForTimeout(350);await code.fill('603230');
  await page.waitForTimeout(900);assert.equal(await code.inputValue(),'603230');assert.equal(await name.inputValue(),'内蒙新华');
  await code.fill('600000');await page.waitForTimeout(350);await page.locator('#cancel-edit').click();
  await page.waitForTimeout(800);assert.equal(await code.inputValue(),'');assert.equal(await name.inputValue(),'');
  await name.fill('无匹配');await page.locator('#stock-hint').filter({hasText:'未找到'}).waitFor();assert.equal(await code.inputValue(),'');
  console.log('PASS: 双向补齐、模糊候选、修改清除旧配对、过期响应、清空、无匹配');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
