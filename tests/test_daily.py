from decimal import Decimal as D
import pytest
from app.store import Store
from app.daily import daily_snapshot, prepare_daily_payload
from app.services import Jobs
from test_ledger import trade


def ledger(tmp_path):
    s = Store(tmp_path)
    s.mutate(trade(qty=100, price='10', at='2026-10-12T10:00:00'))
    s.mutate(trade(qty=100, price='20', code='000001', at='2026-10-12T11:00:00'))
    s.mutate(trade(side='sell', qty=100, price='12', at='2026-10-13T10:00:00'))
    s.mutate(trade(qty=100, price='5', code='600000', at='2026-10-13T11:00:00'))
    s.mutate(dict(kind='deposit', at='2026-10-13T12:00:00', amount='1000'))
    for code, prices in [('603230', ('11','12')), ('000001',('20','21')), ('600000',('5','6'))]:
        s.save_market(code, 'daily', {'rows':[{'at':'2026-10-12','close':prices[0]}, {'at':'2026-10-13','close':prices[1]}, {'at':'2026-10-14','close':'999'}]})
    s.save_market('000300-index', 'index', {'rows':[{'at':'2026-10-12','close':'4000'}]})
    return s


def test_daily_multi_stock_cash_pnl_and_future_exclusion(tmp_path):
    s = ledger(tmp_path)
    day = daily_snapshot(s, '2026-10-13')
    assert day['trade_count']==2 and day['stock_count']==2
    assert set(day['codes'])=={'603230','000001','600000'}
    assert len(day['opening']['positions'])==2
    assert day['net_external']==D('1000')
    assert day['account_pnl']==day['ending']['assets']-day['opening']['assets']-D('1000')
    assert day['account_pnl'] != day['sell_realized']
    assert [e['at'] for e in day['events']]==sorted(e['at'] for e in day['events'])
    payload, allowed, _ = prepare_daily_payload(s, '2026-10-13')
    assert all(r['at'][:10]<='2026-10-13' for d in payload['outcome_data'] for r in d['rows'])
    assert all('2026-10-13' not in e for e in allowed)  # 日线只取操作前一日及更早
    s.mutate(trade(qty=100, price='3', code='600001', at='2026-10-14T10:00:00'))
    assert daily_snapshot(s, '2026-10-13')['ending']==day['ending']


def test_daily_missing_prices_and_no_trade_holdings(tmp_path):
    s=Store(tmp_path);s.mutate(trade())
    day=daily_snapshot(s,'2026-10-13')
    assert day['trade_count']==0 and day['codes']==['603230']
    assert day['account_pnl'] is None
    assert prepare_daily_payload(s,'2026-10-13')[0]['daily']['opening']['positions']
    with pytest.raises(ValueError):daily_snapshot(s,'2099-01-01')
    with pytest.raises(ValueError):prepare_daily_payload(s,'2026-10-11')


def test_daily_job_one_ai_call_and_config(tmp_path,monkeypatch):
    from test_services import report
    s=ledger(tmp_path);jobs=Jobs(s);calls=[];fetched=[]
    monkeypatch.setattr(jobs,'fetch_market',lambda code,sector:fetched.append(code) or [])
    monkeypatch.setattr('app.services.run_codex',lambda *args:calls.append(args) or report())
    jobs.run(dict(id='day',scope='day',date='2026-10-13',code=None,review=True,sector='',include_images=False,
                  ai_config={'model':'gpt-6.1-sol','effort':'high'}))
    assert len(calls)==1 and len(fetched)==3
    result=s.list_rows('reports')[0]
    assert result['scope']=='day' and result['date']=='2026-10-13'
    assert result['input']['screenshots']==[]
    jobs.pool.shutdown()


def test_stale_previous_price_not_reported_as_daily_pnl(tmp_path):
    s=Store(tmp_path);s.mutate(trade(at='2026-10-09T10:00:00'))
    s.save_market('000300-index','index',{'rows':[{'at':'2026-10-09','close':'4000'}]})
    s.save_market('603230','daily',{'rows':[{'at':'2026-10-09','close':'14'}, {'at':'2026-10-13','close':'15'}]})
    assert daily_snapshot(s,'2026-10-13')['account_pnl'] is None


def test_position_prices_partial_sale_rebuy_and_close(tmp_path, monkeypatch):
    s=Store(tmp_path)
    s.mutate(trade(qty=200,price='10',at='2026-10-12T10:00:00'))
    s.mutate(trade(side='sell',qty=100,price='11',at='2026-10-13T10:00:00'))
    s.mutate(trade(qty=50,price='10',at='2026-10-13T11:00:00'))
    s.save_market('000300-index','index',{'rows':[{'at':'2026-10-12','close':'4000'}]})
    s.save_market('603230','daily',{'rows':[{'at':'2026-10-12','close':'10.1234'},{'at':'2026-10-13','close':'11.1234'}]})
    day=daily_snapshot(s,'2026-10-13')
    assert day['opening']['positions'][0]['quote_price']=='10.1234'
    p=day['ending']['positions'][0]
    assert p['qty']==150 and p['quote_price']=='11.1234' and p['quote_at']=='2026-10-13T15:00:00'
    monkeypatch.setattr('app.daily.now',lambda:'2026-10-13T14:00:00+08:00')
    day=daily_snapshot(s,'2026-10-13')
    assert not day['closed']
    assert all(day['ending']['positions'][0][key] is None for key in ('quote_price','value','unrealized','quote_at'))
    monkeypatch.setattr('app.daily.now',lambda:'2026-10-17T16:00:00+08:00')
    day=daily_snapshot(s,'2026-10-14')
    assert day['ending']['positions'][0]['quote_price'] is None
    s.mutate(trade(side='sell',qty=150,price='11',at='2026-10-14T10:00:00'))
    assert daily_snapshot(s,'2026-10-14')['ending']['positions']==[]


def test_daily_minute_outcomes_only_selected_day(tmp_path):
    s=ledger(tmp_path)
    s.save_market('603230','minute',{'rows':[{'at':'2026-10-12T10:00:00','close':'10'},
        {'at':'2026-10-13T09:30:00','close':'11'}, {'at':'2026-10-14T10:00:00','close':'12'}]})
    payload,allowed,_=prepare_daily_payload(s,'2026-10-13')
    rows=next(d['rows'] for d in payload['outcome_data'] if d['kind']=='minute')
    assert [r['at'] for r in rows]==['2026-10-13T09:30:00']
    assert any('2026-10-12T10:00:00' in e for e in allowed)
    assert 'before_each_buy' not in payload
    assert len(s.market_all()[('603230','minute')]['rows'])==3
