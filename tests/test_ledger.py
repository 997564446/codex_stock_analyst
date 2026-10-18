import io
from decimal import Decimal as D
import pytest
from app.core import DEFAULT_FEES, fees, normalize_trade, replay, statistics
from app.store import Store


def trade(side='buy', price='14.2', qty=200, at='2026-10-12T10:00:00', code='603230', **kw):
    raw=dict(code=code,name='内蒙新华',side=side,price=price,qty=qty,at=at,expected='15',**kw)
    return normalize_trade(raw,DEFAULT_FEES)


def test_actual_neimeng_acceptance():
    buy=trade(fee_mode='actual',stamp='0',transfer='.03',commission='4.84',other='.16')
    sell=trade('sell','14.4',at='2026-10-13T10:00:00',fee_mode='actual',stamp='1.44',transfer='.03',commission='4.84',other='.16')
    r=replay('10000',[buy,sell])
    assert r['events'][0]['cash_after']=='7154.97'
    assert r['cash']==D('10028.50')
    assert r['realized']==D('28.50')
    assert r['total_fees']==D('11.50')
    assert r['cycles'][0]['pnl']==D('28.50')


def test_yisheng_fees_acceptance():
    f=fees('002458','sell',D('45718.40'))
    assert f==dict(stamp=D('22.86'),transfer=D(0),commission=D('7.82'),other=D('2.94'))
    assert D('45718.40')-sum(f.values())==D('45684.78')


def test_small_fee_estimates():
    assert fees('603230','buy',D('2840'))==dict(stamp=D(0),transfer=D('.03'),commission=D('4.84'),other=D('.16'))
    assert fees('603230','sell',D('2880'))==dict(stamp=D('1.44'),transfer=D('.03'),commission=D('4.84'),other=D('.16'))


def free(side,price,qty,at):
    return trade(side,price,qty,at,fee_mode='actual',stamp='0',transfer='0',commission='0',other='0')


def test_weighted_add_partial_exit_and_cycle_reset():
    events=[free('buy','10',100,'2026-10-09T10:00:00'),free('buy','20',100,'2026-10-12T10:00:00'),
            free('sell','18',50,'2026-10-13T10:00:00'),free('sell','16',150,'2026-10-14T10:00:00')]
    partial=replay('10000',events[:3])
    assert partial['positions'][0]['cost']==D('2250')
    assert partial['realized']==D('150')
    full=replay('10000',events)
    assert full['cash']==D('10300')
    assert full['cycles'][0]['pnl']==D('300')
    assert len(replay('10000',events+[free('buy','10',100,'2026-10-15T10:00:00')])['cycles'])==1


def test_no_negative_cash_or_overselling_or_t0():
    buy=trade()
    with pytest.raises(ValueError,match='现金不足'): replay('100',[buy])
    with pytest.raises(ValueError,match='超过持仓'): replay('10000',[buy,trade('sell',qty=201,at='2026-10-13T10:00:00')])
    with pytest.raises(ValueError,match='当日'): replay('10000',[buy,trade('sell',at='2026-10-12T11:00:00')])


def test_existing_shares_can_sell_after_same_day_add():
    events=[free('buy','10',100,'2026-10-09T10:00:00'),free('buy','11',100,'2026-10-12T10:00:00'),free('sell','12',100,'2026-10-12T11:00:00')]
    assert replay('10000',events)['positions'][0]['qty']==100


def test_deposit_withdraw_and_missing_quote():
    events=[dict(kind='deposit',amount='1000',at='2026-10-09T09:00:00'),trade(),dict(kind='withdraw',amount='500',at='2026-10-13T10:00:00')]
    r=replay('10000',events)
    assert r['net_deposits']==500
    assert r['realized']==0
    assert r['assets'] is None and r['market_value'] is None
    priced=replay('10000',events,{'603230':dict(price='14.2',at='2026-10-12T15:00:00')})
    assert priced['assets']==D('10494.97')


def test_dividend_and_bonus_cost_reconciliation():
    events=[free('buy','10',100,'2026-10-09T10:00:00'),dict(kind='adjustment',code='603230',at='2026-10-12T10:00:00',amount='100',qty_delta=100),free('sell','6',200,'2026-10-13T10:00:00')]
    r=replay('10000',events)
    assert r['cash']==D('10300') and r['realized']==D('300')
    assert r['cycles'][0]['adjusted']


def test_edit_rollback_and_fee_snapshot(tmp_path):
    s=Store(tmp_path)
    ident=s.mutate(trade())
    s.mutate(trade('sell','14.4',at='2026-10-13T10:00:00'))
    with pytest.raises(ValueError): s.mutate(trade(qty=100),event_id=ident)
    assert s.events()[0]['qty']==200
    with pytest.raises(ValueError): s.mutate(event_id=ident)
    s.mutate(settings={'fees':dict(DEFAULT_FEES,net_rate='.003')})
    assert s.events()[0]['fees']['commission']=='4.84'
    s.mutate(trade(price='14.1'),event_id=ident)
    assert replay(s.settings()['initial'],s.events())['cash']>D('10028.5')


def test_backup_restores_db_and_images(tmp_path):
    from PIL import Image
    s=Store(tmp_path/'one')
    ident=s.mutate(trade())
    image=io.BytesIO();Image.new('RGB',(20,20),'white').save(image,'PNG')
    pic=s.attach(ident,image.getvalue())
    archive=s.backup()
    s.mutate(trade(qty=100),event_id=ident)
    name=s.restore(archive)
    assert s.events()[0]['qty']==200
    assert (s.root/name).exists()
    assert (s.root/'images'/(pic+'.png')).exists()


def test_invalid_archive_paths_rejected(tmp_path):
    import zipfile
    s=Store(tmp_path)
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as z:
        z.writestr('manifest.json','{"version":1}')
        z.writestr('../escape','bad')
    with pytest.raises(ValueError,match='路径'):s.restore(output.getvalue())


@pytest.mark.parametrize('price',['NaN','Infinity','-1','0'])
def test_invalid_money(price):
    with pytest.raises(ValueError):trade(price=price)


def test_statistics_counts_closed_cycles_only():
    r=replay('10000',[trade(),trade('sell','14.4',at='2026-10-13T10:00:00'),trade(at='2026-10-14T10:00:00')])
    stats=statistics(r['cycles'])
    all_group=next(x for x in stats if x['group']=='全部')
    assert all_group['count']==1 and all_group['mean']==D('28.50')


def test_zero_initial_cash_rejects_without_division_error():
    with pytest.raises(ValueError,match='现金不足'):
        replay('0',[trade()])


def test_historical_estimation_requires_actual_fees():
    with pytest.raises(ValueError,match='实际交割费用'):
        trade(at='2023-05-01T10:00:00')
