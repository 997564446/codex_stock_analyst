"""按上海日期重放账户，整日复盘只使用所选日及此前的资料。"""
from datetime import date, timedelta
from .core import D, money, number, replay
from .store import now


def validate_day(value):
    """校验交易日期，拒绝未来日期与不规范输入。"""
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError('请选择有效交易日期')
    if parsed.isoformat() != value or value < '2023-01-01' or value > now()[:10]:
        raise ValueError('交易日期须在 2023 年起至今天之间')
    return value


def daily_snapshot(store, day):
    """计算日初、日末持仓及流水；行情不齐时不推算账户日盈亏。"""
    validate_day(day)
    with store.lock:
        cfg, events, datasets = store.settings(), store.events(), store.market_all()
        prior = [e for e in events if e['at'][:10] < day]
        through = [e for e in events if e['at'][:10] <= day]
        # 用指数交易日确定共同估值基准，不混用各股票不同日期的最近价格。
        index_days = sorted({r['at'][:10] for r in datasets.get(('000300-index', 'index'), {}).get('rows', []) if r['at'][:10] < day})
        candidate = date.fromisoformat(day)-timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        # 缺少完整交易日历时保守处理节假日和行情缺口，不将多日涨跌算作日盈亏。
        previous = candidate.isoformat() if candidate.isoformat() in index_days else None
        closed = day < now()[:10] or now()[11:19] >= '15:00:00'
        def quotes(target):
            if not target:
                return {}
            result = {}
            for (code, kind), data in datasets.items():
                if kind != 'daily':
                    continue
                row = next((r for r in data.get('rows', []) if r['at'][:10] == target), None)
                if row:
                    result[code] = dict(price=row['close'], at=target+'T15:00:00')
            return result
        opening = replay(cfg['initial'], prior, quotes(previous))
        ending = replay(cfg['initial'], through, quotes(day) if closed else {})
        today = [e for e in ending['events'] if e['at'][:10] == day]
        trades = [e for e in today if e['kind'] == 'trade']
        codes = sorted({p['code'] for p in opening['positions']+ending['positions']} | {e['code'] for e in today if e.get('code')})
        external = ending['net_deposits']-opening['net_deposits']
        pnl = (money(ending['assets']-opening['assets']-external)
               if closed and ending['assets'] is not None and opening['assets'] is not None else None)
        # 逐笔重放保留仓位变化；成本仓位不是盘中实时市值仓位。
        timeline = []
        accumulated = list(prior)
        for event in today:
            accumulated.append(event)
            ledger = replay(cfg['initial'], accumulated)
            cost = sum((p['cost'] for p in ledger['positions']), D(0))
            base = ledger['cash']+cost
            timeline.append(dict(event=event, cash=ledger['cash'], positions=ledger['positions'],
                                 cost_position_ratio=str(cost/base) if base else None))
        compact = lambda ledger: {k:ledger[k] for k in ('cash','assets','positions','missing_quotes')}
        return dict(date=day, scope='day', revision=cfg['revision'], previous_valuation_date=previous,
                    closed=closed, opening=compact(opening), ending=compact(ending), events=today,
                    timeline=timeline, codes=codes, trade_count=len(trades), stock_count=len({e['code'] for e in trades}),
                    buy_amount=sum((number(e['amount']) for e in trades if e['side']=='buy'), D(0)),
                    sell_amount=sum((number(e['amount']) for e in trades if e['side']=='sell'), D(0)),
                    fees=ending['total_fees']-opening['total_fees'],
                    sell_realized=sum((number(e['realized'], False) for e in trades if e['side']=='sell'), D(0)),
                    net_external=external, account_pnl=pnl, note=cfg.get('daily_notes', {}).get(day, ''),
                    valuation_notice='账户日盈亏按前一可确认交易日收盘至本日收盘计算并剔除出入金；缺行情或未收盘则留空。卖出兑现盈亏含跨日持仓收益。成本仓位不代表实时市值仓位。')


def prepare_daily_payload(store, day):
    """打包多股全天操作及事前证据，排除所选日期之后的交易和行情。"""
    with store.lock:
        snapshot = daily_snapshot(store, day)
        if not snapshot['codes'] and not snapshot['events']:
            raise ValueError('当天没有操作或持仓可供复盘')
        datasets = store.market_all()
        codes = set(snapshot['codes']) | {'000300-index'}
        allowed, before, after, sources = set(), [], [], []
        for event in snapshot['events']:
            if event['kind'] != 'trade':
                continue
            evidence = []
            for (code, kind), data in datasets.items():
                if code not in (event['code'], '000300-index'):
                    continue
                rows = [r for r in data.get('rows', []) if
                        (r['at'] < event['at'][:16] if kind == 'minute' else r['at'][:10] < day)]
                for row in rows[-120 if kind == 'minute' else -30:]:
                    ident = '%s:%s:%s:%s' % (event['id'], code, kind, row['at'])
                    allowed.add(ident)
                    evidence.append(dict(id=ident, kind=kind, row=row))
            before.append(dict(trade_id=event['id'], side=event['side'], cutoff=event['at'], evidence=evidence))
        for (code, kind), data in datasets.items():
            if code not in codes:
                continue
            rows = [r for r in data.get('rows', []) if r['at'][:10] <= day and
                    (kind != 'minute' or r['at'][:10] == day) and
                    (snapshot['closed'] or r['at'][:10] < day or (kind == 'minute' and r['at'] < now()[:16]))]
            after.append(dict(symbol=code, kind=kind, total_rows=len(rows), rows=rows[-1500 if kind=='minute' else -60:]))
            sources.append(dict(symbol=code, kind=kind, source=data.get('source'), error=data.get('error')))
        ids = {e['id'] for e in snapshot['events']}
        images = [i for i in store.images() if i['event_id'] in ids]
        payload = dict(scope='day', date=day, revision=snapshot['revision'], created_at=now(),
                       daily=snapshot, before_each_trade=before,
                       outcome_data=after, sources=sources, screenshots=images,
                       coverage_notice='事后分钟行情仅含所选当天；每次操作前最多120条分钟线及30条日线，日线背景最多60条。完整原始行情仍保存在本地。',
                       note_timing='每日说明为事后记录，不能据此认定操作前已有计划。')
        return payload, allowed, images
