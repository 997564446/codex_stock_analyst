"""纯账本计算：所有金额使用 Decimal，AI 不参与资金变更。"""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_CEILING
import re

D = Decimal
CENT = D('0.01')
DEFAULT_FEES = {'net_rate': '0.000171', 'minimum': '5', 'stamp': '0.0005',
                'handling': '0.0000341', 'regulatory': '0.00002', 'transfer': '0.00001'}
FEE_KEYS = ('stamp', 'transfer', 'commission', 'other')


def number(value, nonnegative=True):
    """解析有限十进制数，拒绝 NaN、无穷值和非法负数。"""
    try:
        result = D(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('请输入有效数字')
    if not result.is_finite() or abs(result) > D('1000000000000'):
        raise ValueError('数字超出允许范围')
    if nonnegative and result < 0:
        raise ValueError('金额不能为负数')
    return result


def money(value):
    """金额显示与结算按分四舍五入。"""
    return number(value, False).quantize(CENT, rounding=ROUND_HALF_UP)


def up(value):
    return value.quantize(CENT, rounding=ROUND_CEILING)


def market(code):
    """仅接受本版支持的沪深 A 股代码段，不把基金或北交所混入账本。"""
    if re.fullmatch(r'(600|601|603|605|688)\d{3}', code):
        return 'sh'
    if re.fullmatch(r'(000|001|002|003|300|301)\d{3}', code):
        return 'sz'
    raise ValueError('请输入沪深 A 股六位代码；本版不支持基金、北交所或融资交易')


def fees(code, side, amount, config=None):
    """按个人待验证配置估算费用；沪市过户费单列，深市包含于规费。"""
    cfg = {k: number(v) for k, v in (config or DEFAULT_FEES).items()}
    exchange = market(code)
    other = up(amount * cfg['handling']) + up(amount * cfg['regulatory'])
    transfer = up(amount * cfg['transfer'])
    if exchange == 'sz':
        other += transfer
        transfer = D(0)
    commission = max(up(amount * cfg['net_rate']), cfg['minimum'] - other, D(0))
    return dict(stamp=money(amount * cfg['stamp']) if side == 'sell' else D(0),
                transfer=transfer, commission=money(commission), other=other)


def normalize_trade(raw, config):
    """校验成交输入并固化本笔费用快照，历史费用不随配置变更。"""
    code = str(raw.get('code', '')).strip()
    market(code)
    side = raw.get('side')
    if side not in ('buy', 'sell'):
        raise ValueError('请选择买入或卖出')
    try:
        when = datetime.fromisoformat(raw['at'])
    except (ValueError, KeyError, TypeError):
        raise ValueError('请输入成交日期和时间')
    if when.tzinfo or when.year < 2023:
        raise ValueError('使用上海本地时间；本版从 2023 年起记录，历史税费请填实际值')
    qty = number(raw.get('qty', ''))
    if qty != qty.to_integral_value() or qty <= 0:
        raise ValueError('成交数量必须为正整数股')
    price = number(raw.get('price', ''))
    if price <= 0 or price.as_tuple().exponent < -4:
        raise ValueError('成交均价必须大于零，最多四位小数')
    expected = raw.get('expected')
    expected = str(number(expected)) if expected not in (None, '') else None
    if side == 'buy' and (expected is None or number(expected) <= 0):
        raise ValueError('买入请填写预期当日收盘价')
    mode = raw.get('fee_mode', 'estimate')
    if mode not in ('actual', 'estimate'):
        raise ValueError('费用来源无效')
    if mode == 'estimate' and when.date().isoformat() < '2023-08-28':
        raise ValueError('该日期早于当前估算税费口径，请填写实际交割费用')
    amount = money(price * qty)
    charge = ({k: money(number(raw.get(k, ''))) for k in FEE_KEYS}
              if mode == 'actual' else fees(code, side, amount, config))
    tags = raw.get('tags', [])
    if isinstance(tags, str):
        tags = [s.strip() for s in tags.split(',') if s.strip()]
    if not isinstance(tags, list) or len(tags) > 12 or any(not isinstance(t, str) or len(t) > 40 for t in tags):
        raise ValueError('标签最多 12 个，每个不超过 40 字')
    timing = raw.get('record_timing', 'retrospective')
    if timing not in ('contemporaneous', 'retrospective'):
        raise ValueError('记录时点无效')
    name = str(raw.get('name', code)).strip()[:40] or code
    if 'ST' in name.upper() or '退' in name:
        raise ValueError('第一版仅支持普通 A 股，请勿录入风险警示或退市股票')
    return dict(kind='trade', code=code, name=name, side=side,
                at=when.isoformat(timespec='seconds'), qty=int(qty), price=str(price),
                amount=str(amount), expected=expected, fee_mode=mode,
                fees={k: str(v) for k, v in charge.items()}, fee_config=dict(config),
                tags=tags, record_timing=timing, note=str(raw.get('note', ''))[:2000])


def replay(initial, events, quotes=None):
    """按时间重放账本；持仓返回实际估值单价 quote_price 和时间，不反算价格。"""
    cash = money(number(initial))
    realized = D(0)
    total_fees = D(0)
    external = D(0)
    positions = {}
    cycles = []
    rows = []
    for event in sorted(events, key=lambda e: (e['at'], e.get('id', 0))):
        e = dict(event)
        kind = e['kind']
        if kind in ('deposit', 'withdraw'):
            value = money(number(e['amount'])) * (1 if kind == 'deposit' else -1)
            cash += value
            external += value
        elif kind == 'adjustment':
            code = e['code']
            p = positions.get(code)
            if not p or p['qty'] <= 0:
                raise ValueError('公司行为调整必须对应现有持仓')
            # 分红现金降低未收回成本；成本不足时余款确认为收益。
            dividend = money(number(e.get('amount', '0')))
            delta = int(e.get('qty_delta', 0))
            if p['qty'] + delta <= 0:
                raise ValueError('调整后持股数量必须大于零')
            p['qty'] += delta
            p['cost'] -= dividend
            cash += dividend
            if p['cost'] < 0:
                realized -= p['cost']
                p['cycle']['pnl'] -= p['cost']
                p['cost'] = D(0)
            p['cycle']['adjusted'] = True
        elif kind == 'trade':
            code, side, qty = e['code'], e['side'], e['qty']
            value = number(e['amount'])
            fee = sum((number(v) for v in e['fees'].values()), D(0))
            total_fees += fee
            p = positions.setdefault(code, {'code': code, 'name': e['name'], 'qty': 0, 'cost': D(0), 'buys': {}})
            if side == 'buy':
                if cash < value + fee:
                    raise ValueError('该操作导致现金不足，请检查初始资金或出入金')
                if p['qty'] == 0:
                    p['cycle'] = {'code': code, 'name': e['name'], 'start': e['at'], 'end': None,
                                  'entry_id': e.get('id'), 'tags': e.get('tags', []), 'pnl': D(0),
                                  'invested': D(0), 'fees': D(0), 'adjusted': False,
                                  'entry_cash_ratio': (value + fee) / cash if cash else D(0),
                                  'entry_position_ratio': (value + fee) / (cash + sum(x['cost'] for x in positions.values()))}
                p['qty'] += qty
                p['cost'] += value + fee
                day = e['at'][:10]
                p['buys'][day] = p['buys'].get(day, 0) + qty
                cash -= value + fee
                p['cycle']['invested'] += value + fee
            else:
                if qty > p['qty']:
                    raise ValueError('%s 卖出数量超过持仓' % code)
                if qty > p['qty'] - p['buys'].get(e['at'][:10], 0):
                    raise ValueError('%s 当日新买股份不能当日卖出' % code)
                allocated = p['cost'] if qty == p['qty'] else money(p['cost'] * qty / p['qty'])
                profit = value - fee - allocated
                cash += value - fee
                p['qty'] -= qty
                p['cost'] -= allocated
                realized += profit
                p['cycle']['pnl'] += profit
                e['realized'] = str(profit)
            p['cycle']['fees'] += fee
            if p['qty'] == 0:
                p['cycle']['end'] = e['at']
                cycles.append(dict(p['cycle']))
        else:
            raise ValueError('未知流水类型')
        if cash < 0:
            raise ValueError('该操作导致现金不足，请检查初始资金、流水顺序或出入金')
        e['cash_after'] = str(money(cash))
        rows.append(e)
    held = []
    unknown = []
    known_value = D(0)
    for code, p in positions.items():
        if p['qty'] <= 0:
            continue
        q = (quotes or {}).get(code)
        value = money(number(q['price']) * p['qty']) if q else None
        if value is None:
            unknown.append(code)
        else:
            known_value += value
        held.append(dict(code=code, name=p['name'], qty=p['qty'], cost=p['cost'],
                         avg_cost=money(p['cost'] / p['qty']), value=value,
                         unrealized=value-p['cost'] if value is not None else None,
                         quote_at=q['at'] if q else None, quote_price=str(q['price']) if q else None, cycle=p['cycle']))
    return dict(cash=money(cash), realized=money(realized), total_fees=money(total_fees),
                net_deposits=money(external), positions=held, cycles=cycles, events=rows,
                market_value=None if unknown else known_value,
                assets=None if unknown else money(cash + known_value), missing_quotes=unknown)


def statistics(cycles, growth=None):
    """仅统计已清仓周期；分组并非独立实验，样本数和缺失值必须同时展示。"""
    groups = defaultdict(list)
    for c in cycles:
        hour = int(c['start'][11:13])
        slot = '上午' if hour < 12 else '下午'
        ratio = c['entry_cash_ratio']
        cash_group = '≤25%' if ratio <= D('.25') else '25%–50%' if ratio <= D('.5') else '>50%'
        position = c.get('entry_position_ratio', ratio)
        position_group = '≤25%' if position <= D('.25') else '25%–50%' if position <= D('.5') else '>50%'
        g = (growth or {}).get(c['entry_id'])
        ggroup = '未知' if g is None else '<5%' if g < 5 else '5%–7%' if g < 7 else '≥7%'
        for key in ['全部', '买入时段 / '+slot, '首次买入占可用现金 / '+cash_group,
                    '首次买入成本仓位 / '+position_group, '买入涨幅 / '+ggroup] + ['标签 / '+t for t in set(c['tags'])]:
            groups[key].append(c)
    output = []
    for group, cs in sorted(groups.items()):
        values = [c['pnl'] for c in cs]
        wins = [v for v in values if v > 0]
        losses = [v for v in values if v < 0]
        output.append(dict(group=group, count=len(cs), wins=len(wins), win_rate=money(D(len(wins))*100/len(cs)),
                           mean=money(sum(values)/len(cs)), mean_win=money(sum(wins)/len(wins)) if wins else D(0),
                           mean_loss=money(sum(losses)/len(losses)) if losses else D(0),
                           mean_return=money(sum(c['pnl']/c['invested']*100 for c in cs)/len(cs))))
    return output
