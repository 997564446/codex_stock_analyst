/* 展示验收：模拟响应，不改写账本。 */
const {chromium}=require('/Applications/ChatGPT.app/Contents/Resources/cua_node/lib/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try{
 const page=await browser.newPage({viewport:{width:1440,height:1100}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const position=(code,name,cost,price)=>({code,name,qty:200,cost,quote_price:price,quote_at:'2026-10-16T15:00:00',value:String(Number(price)*200),unrealized:String(Number(price)*200-Number(cost))});
 const opening=position('603230','内蒙新华','2845.03','14.52');opening.quote_at='2026-10-15T15:00:00';
 let mode='closed';
 await page.route('**/api/daily/*',async route=>{
  const positions=[position('000993','闽东电力','3775','18.20'),position('000678','襄阳轴承','2155','10.93')];
  if(mode==='missing'||mode==='open')for(const p of positions)for(const k of ['quote_price','quote_at','value','unrealized'])p[k]=null;
  await route.fulfill({json:{date:'2026-10-16',closed:mode!=='open',opening:{positions:[opening],cash:'7154.97'},ending:{positions:mode==='empty'?[]:positions,cash:'4098.50'},trade_count:3,stock_count:3,buy_amount:'5920',sell_amount:'2880',fees:'16.47',sell_realized:'28.50',account_pnl:null,note:'',previous_valuation_date:'2026-10-15',valuation_notice:'估值日期按各表明确标注。'}});
 });
 await page.route('**/api/state',async route=>{
  const response=await route.fetch();const state=await response.json();
  state.events=[{id:99,kind:'trade',side:'sell',code:'603230',name:'内蒙新华',at:'2026-10-16T09:33:30',qty:200,price:'14.40',amount:'2880',fees:{stamp:'1.44',transfer:'.03',commission:'4.84',other:'.16'},fee_mode:'estimate',cash_after:'10028.50'}];
  await route.fulfill({json:state});
 });
 await page.goto('http://127.0.0.1:8766');await page.locator('nav [data-tab=trades]').click();
 await page.locator('#trade-day').fill('2026-10-16');await page.locator('#trade-day').dispatchEvent('change');
 await page.locator('#daily-positions-title').filter({hasText:'2026-10-16 收盘持仓'}).waitFor();
 assert.equal(await page.locator('#opening-holdings').evaluate(e=>e.open),false);
 assert.equal((await page.locator('#daily-positions').innerText()).includes('内蒙新华'),false);
 assert.ok((await page.locator('#daily-positions').innerText()).includes('3,640.00'));
 assert.ok((await page.locator('#transactions').innerText()).includes('到账 2,873.53'));
 await page.locator('#opening-holdings summary').click();
 const text=await page.locator('#daily-opening-positions').innerText();assert.ok(text.includes('2026-10-15')&&text.includes('2,904.00'));
 for(const [testMode,expected] of [['missing','缺少当日收盘行情'],['open','待收盘'],['empty','当日收盘无持仓']]){
  mode=testMode;await page.locator('#trade-day').dispatchEvent('change');await page.locator('#daily-positions').getByText(expected,{exact:false}).first().waitFor();
  if(mode==='open')assert.ok((await page.locator('#daily-positions-title').innerText()).includes('尚未收盘'));
 }
 assert.deepEqual(errors,[]);console.log('PASS: 日初折叠、明确估值日期、清仓排除、卖出到账、缺行情、未收盘、空仓');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
