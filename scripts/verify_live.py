"""使用临时示例账本验证真实行情和 Codex；不写入个人 data 目录。"""
import json
import tempfile
from pathlib import Path
from app.core import DEFAULT_FEES, normalize_trade
from app.services import Jobs
from app.store import Store, now


def main():
    with tempfile.TemporaryDirectory(prefix='stock-live-check-') as work:
        store = Store(work)
        event = normalize_trade(dict(code='603230',name='内蒙新华',side='buy',
                    at='2026-10-16T10:00:00',price='14.2',qty=200,expected='15',
                    note='开发验收虚构时点与成交样例，不代表用户真实交易。'),DEFAULT_FEES)
        store.mutate(event)
        jobs = Jobs(store)
        try:
            jobs.run(dict(id='live-check',code='603230',review=True,sector='文化传媒',include_images=False))
            job = store.list_rows('jobs')[0]
            summary = dict(at=now(),fixture='临时示例，不是个人账本',
                           status=job['status'],error=job.get('error'),
                           market=[dict(code=key[0],kind=key[1],source=data.get('source'),
                                        rows=len(data.get('rows',[])),coverage=data.get('coverage'),error=data.get('error'))
                                   for key,data in store.market_all().items()],
                           report_count=len(store.list_rows('reports')))
            if store.list_rows('reports'):
                summary['report'] = store.list_rows('reports')[0]['result']
            output = Path('artifacts/live-verification.json')
            output.parent.mkdir(exist_ok=True)
            output.write_text(json.dumps(summary,ensure_ascii=False,indent=2))
            print(json.dumps(summary,ensure_ascii=False,indent=2))
            if job['status'] != 'done':
                raise SystemExit(1)
        finally:
            jobs.pool.shutdown()


if __name__ == '__main__':
    main()
