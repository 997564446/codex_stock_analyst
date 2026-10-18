"""浏览器验收专用服务：临时数据和明确的模拟行情/报告，不污染个人账本。"""
import tempfile
import uvicorn
import app.services as services
from app.main import create_app


def fixture_report(*args):
    return dict(summary='浏览器验收模拟报告：本周期净赚28.50元。',phases=['手续费共11.50元。'],risks=[],main_issue='证据不足，不能判定出货。',next_experiment='下次保留买入前截图。',limitations=['这是测试数据，不是真实行情分析。'])


with tempfile.TemporaryDirectory(prefix='stock-browser-test-') as work:
    app=create_app(work,background=False)
    services.run_codex=fixture_report
    app.state.jobs.fetch_market=lambda *args:['浏览器测试：行情缺失模拟']
    uvicorn.run(app,host='127.0.0.1',port=8766,log_level='warning')
