"""股票代码和名称查询；外部不可用时允许继续手工记账。"""
import os
import re
import requests
from .core import market


def search_stocks(query):
    """按代码或名称查询 A 股候选，保留六位代码及名称，不自动选取模糊结果。"""
    with requests.Session() as session:
        session.trust_env = os.environ.get('STOCK_MARKET_PROXY') == '1'
        response = session.get('https://searchapi.eastmoney.com/api/suggest/get',
                               params={'input': query, 'type': '14', 'count': 20}, timeout=5)
        response.raise_for_status()
        rows = response.json()['QuotationCodeTable']['Data'] or []
    result = {}
    for row in rows:
        code, name = row.get('Code', ''), row.get('Name', '')
        if row.get('Classify') == 'AStock' and re.fullmatch(r'[0-9]{6}', code) and name:
            try:
                market(code)
            except ValueError:
                continue
            result[code] = {'code': code, 'name': name}
    return list(result.values())
