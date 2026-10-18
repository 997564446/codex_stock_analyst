"""隔离免费行情请求；父进程设置硬超时，不阻塞记账。"""
import json
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation


def fetch(kind, code, start, end, sector=''):
    """拉取不复权价格；只返回行情，不根据日线编造盘口和历史板块归属。"""
    import os
    import requests
    # 国内行情默认直连，避免 macOS 系统代理把请求送到不可用的代理端口。
    # 仅修改隔离的行情子进程，Codex 与用户全局网络设置不受影响。
    if os.environ.get('STOCK_MARKET_PROXY') != '1':
        original = requests.sessions.Session
        class DirectSession(original):
            def __init__(self):
                super().__init__()
                self.trust_env = False
        requests.Session = DirectSession
        requests.sessions.Session = DirectSession
    import akshare as ak
    if kind == 'daily':
        source = 'AKShare.stock_zh_a_hist / 东方财富 / 不复权'
        df = ak.stock_zh_a_hist(symbol=code, period='daily', start_date=start, end_date=end, adjust='')
    elif kind == 'minute':
        source = 'AKShare.stock_zh_a_hist_min_em / 东方财富 / 1分钟不复权'
        fmt = lambda v: v[:4]+'-'+v[4:6]+'-'+v[6:8]
        df = ak.stock_zh_a_hist_min_em(symbol=code, period='1', start_date=fmt(start)+' 09:00:00', end_date=fmt(end)+' 15:30:00', adjust='')
    elif kind == 'index':
        source = 'AKShare.index_zh_a_hist / 沪深300日线'
        df = ak.index_zh_a_hist(symbol='000300', period='daily', start_date=start, end_date=end)
    elif kind == 'sector':
        if not sector:
            raise ValueError('未填写行业名称，未推断历史板块归属；板块数据缺失')
        source = 'AKShare.stock_board_industry_hist_em / 用户选择行业：'+sector
        df = ak.stock_board_industry_hist_em(symbol=sector, start_date=start, end_date=end, period='日k', adjust='')
    elif kind in ('daily_backup','index_backup'):
        symbol = 'sh000300' if kind == 'index_backup' else ('sh' if code.startswith('6') else 'sz')+code
        source = 'AKShare.stock_zh_a_hist_tx / 腾讯 / 不复权'
        df = ak.stock_zh_a_hist_tx(symbol=symbol,start_date=start,end_date=end,adjust='',timeout=15)
    elif kind == 'minute_backup':
        source = 'AKShare.stock_zh_a_minute / 新浪 / 1分钟不复权'
        df = ak.stock_zh_a_minute(symbol=('sh' if code.startswith('6') else 'sz')+code,period='1',adjust='')
    else:
        raise ValueError('未知行情类型')
    mapping = {'日期':'at', '时间':'at', '开盘':'open', '收盘':'close', '最高':'high', '最低':'low',
               '成交量':'volume', '成交额':'turnover', '涨跌幅':'change', '换手率':'turnover_rate', '均价':'average'}
    mapping.update({'date':'at','day':'at','open':'open','close':'close','high':'high','low':'low','volume':'volume','amount':'turnover'})
    rows = []
    for record in df.to_dict(orient='records'):
        row = {}
        for cn, key in mapping.items():
            if cn in record and str(record[cn]) not in ('nan', 'NaT', 'None'):
                row[key] = str(record[cn]).replace(' ', 'T') if key == 'at' else str(record[cn])
        if 'at' in row and 'close' in row:
            try:
                stamp = datetime.fromisoformat(row['at'])
                close = Decimal(row['close'])
                if stamp.tzinfo or not close.is_finite() or close <= 0:
                    continue
                row['at'] = stamp.isoformat(timespec='seconds') if kind.startswith('minute') else stamp.date().isoformat()
                for key in list(row):
                    if key != 'at' and not Decimal(row[key]).is_finite():
                        del row[key]
            except (ValueError, InvalidOperation):
                continue
            day = row['at'][:10].replace('-','')
            if start <= day <= end:
                row['source'] = source
                row['volume_unit'] = '股' if kind == 'daily_backup' else '来源原始单位，勿跨来源比较'
                rows.append(row)
    if not rows:
        raise ValueError('接口返回空数据，可能停牌、历史分钟已过期或来源不可用')
    return {'source': source, 'rows': rows}


if __name__ == '__main__':
    try:
        result = fetch(*sys.argv[1:])
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'error': type(exc).__name__+': '+str(exc)[:300]}, ensure_ascii=False))
        sys.exit(1)
