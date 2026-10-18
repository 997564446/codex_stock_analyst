import json
import subprocess
import pytest
from app.services import prepare_payload, validate_report, Jobs, run_codex
from app.store import Store
from test_ledger import trade


def report():
    return dict(summary='净亏损，费用以账本为准',phases=['缺乏日线，不能拆解隔夜影响'],risks=[],main_issue='证据不足，无法确定最大问题',next_experiment='下次保存买入前截图',limitations=['分钟行情缺失'])


def test_no_lookahead_evidence(tmp_path):
    s=Store(tmp_path);s.mutate(trade())
    s.save_market('603230','daily',dict(source='fixture',rows=[dict(at='2026-10-09',close='14'),dict(at='2026-10-12',close='13')]))
    s.save_market('603230','minute',dict(source='fixture',rows=[dict(at='2026-10-12T09:59:00',close='14.1'),dict(at='2026-10-12T10:00:00',close='14.2'),dict(at='2026-10-12T10:01:00',close='13.8')]))
    payload,allowed,_=prepare_payload(s,'603230')
    assert len(allowed)==2
    assert not any('10:00' in k or '10:01' in k for k in allowed)
    assert not any(':daily:2026-10-12' in k for k in allowed)
    r=report();r['risks']=[dict(text='待核实风险',evidence_ids=[next(iter(allowed))])]
    assert validate_report(r,allowed)==r
    r['risks'][0]['evidence_ids']=['1:603230:minute:2026-10-12T10:01:00']
    with pytest.raises(ValueError,match='买入后'):validate_report(r,allowed)


def test_failed_fetch_preserves_old_rows(tmp_path):
    s=Store(tmp_path)
    s.save_market('603230','minute',dict(source='fixture',rows=[dict(at='2026-10-12T10:00:00',close='14')]))
    s.save_market('603230','minute',dict(error='超时',rows=[]))
    d=s.market_all()[('603230','minute')]
    assert len(d['rows'])==1 and d['error']=='超时' and d['source']=='fixture'


def test_ai_failure_keeps_ledger_and_retry_possible(tmp_path,monkeypatch):
    s=Store(tmp_path);s.mutate(trade())
    jobs=Jobs(s)
    monkeypatch.setattr(jobs,'fetch_market',lambda *args:['minute: 缺失'])
    def unavailable(*args):raise ValueError('Codex 额度不足')
    monkeypatch.setattr('app.services.run_codex',unavailable)
    j=dict(id='test',code='603230',review=True,sector='',include_images=False)
    jobs.run(j)
    assert s.list_rows('jobs')[0]['status']=='failed'
    assert len(s.events())==1 and not s.list_rows('reports')
    monkeypatch.setattr('app.services.run_codex',lambda *args:report())
    jobs.run(dict(j,id='retry'))
    assert s.list_rows('jobs')[0]['status']=='done' and len(s.list_rows('reports'))==1
    jobs.pool.shutdown()


def test_cli_api_key_refused(tmp_path,monkeypatch):
    monkeypatch.setenv('CODEX_HOME',str(tmp_path))
    (tmp_path/'auth.json').write_text('{"auth_mode":"apikey","OPENAI_API_KEY":"not-a-real-key"}')
    monkeypatch.setattr('app.services.shutil.which',lambda _: '/mock/codex')
    with pytest.raises(ValueError,match='拒绝使用 API Key'): run_codex({},set(),[])


def test_cli_timeout_and_invalid_output(tmp_path,monkeypatch):
    monkeypatch.setenv('CODEX_HOME',str(tmp_path))
    (tmp_path/'auth.json').write_text('{"auth_mode":"chatgpt"}')
    monkeypatch.setattr('app.services.shutil.which',lambda _: '/mock/codex')
    def timeout(*args,**kwargs): raise subprocess.TimeoutExpired('codex',240)
    monkeypatch.setattr('app.services.subprocess.run',timeout)
    with pytest.raises(ValueError,match='240'):run_codex({},set(),[])
    def invalid(args,**kwargs):
        from pathlib import Path
        assert '--ignore-user-config' in args and 'read-only' in args
        assert 'OPENAI_API_KEY' not in kwargs['env']
        Path(args[args.index('-o')+1]).write_text('not json')
        return subprocess.CompletedProcess(args,0,'','')
    monkeypatch.setattr('app.services.subprocess.run',invalid)
    with pytest.raises(ValueError,match='JSON'):run_codex({},set(),[])


def test_market_task_does_not_swallow_review(tmp_path,monkeypatch):
    s=Store(tmp_path);jobs=Jobs(s)
    monkeypatch.setattr(jobs.pool,'submit',lambda *args:None)
    market_id=jobs.submit('603230',review=False)
    review_id=jobs.submit('603230',review=True)
    assert market_id!=review_id
    assert jobs.submit('603230',review=True)==review_id
    assert len(s.list_rows('jobs'))==2
    jobs.pool.shutdown()


def test_open_position_and_closing_comparisons_in_payload(tmp_path):
    s=Store(tmp_path);ident=s.mutate(trade())
    s.save_market('603230','daily',dict(rows=[dict(at='2026-10-12',close='14.4')],source='fixture'))
    payload,_,_=prepare_payload(s,'603230')
    assert payload['current_position']['qty']==200
    assert payload['closing_comparisons'][ident]['entry_day_pnl']=='34.97'
    assert payload['closing_comparisons'][ident]['expected_gap']=='-0.60'


def test_invalid_market_prices_do_not_enter_ledger(monkeypatch):
    import sys
    from types import SimpleNamespace
    from app.market_worker import fetch
    monkeypatch.setenv('STOCK_MARKET_PROXY','1')
    class DataFrame:
        def to_dict(self,orient):
            return [{'日期':'2026-10-12','收盘':float('inf')},
                    {'日期':'2026-10-13','收盘':0},
                    {'日期':'not-a-date','收盘':14},
                    {'日期':'2026-10-14','收盘':14.4}]
    monkeypatch.setitem(sys.modules,'akshare',SimpleNamespace(stock_zh_a_hist=lambda **kwargs:DataFrame()))
    result=fetch('daily','603230','20261001','20261016')
    assert len(result['rows'])==1 and result['rows'][0]['close']=='14.4'


def test_review_model_and_effort_passed_to_cli(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    (tmp_path/'auth.json').write_text('{"auth_mode":"chatgpt"}')
    monkeypatch.setattr('app.services.shutil.which', lambda _: '/mock/codex')
    def complete(args, **kwargs):
        assert args[args.index('-m')+1] == 'gpt-6-astra'
        assert args[args.index('-c')+1] == 'model_reasoning_effort="xhigh"'
        Path(args[args.index('-o')+1]).write_text(json.dumps(report()))
        return subprocess.CompletedProcess(args, 0, '', '')
    monkeypatch.setattr('app.services.subprocess.run', complete)
    assert run_codex({}, set(), [], {'model':'gpt-6-astra', 'effort':'xhigh'}) == report()


def test_review_config_validation_and_dedup(tmp_path, monkeypatch):
    from app.services import review_config
    with pytest.raises(ValueError):
        review_config('gpt-6-luna', 'ultra')
    with pytest.raises(ValueError):
        review_config('unknown', 'high')
    jobs = Jobs(Store(tmp_path))
    monkeypatch.setattr(jobs.pool, 'submit', lambda *args: None)
    first = jobs.submit('603230', review=True)
    other = jobs.submit('603230', review=True, config={'model':'gpt-6-astra','effort':'xhigh'})
    assert first != other
    assert other == jobs.submit('603230', review=True, config={'model':'gpt-6-astra','effort':'xhigh'})
    jobs.pool.shutdown()


def test_cli_error_classification_ignores_prompt_and_metadata():
    from app.services import codex_failure_message
    prefix='limitations: cannot prove intent\nauth_mode: chatgpt\nrate_limits: available\n'
    assert '资料过长' in codex_failure_message(prefix+'Error: turn/start failed: Input exceeds the maximum length of 1048576 characters. input_too_large')
    assert '未能确认原因' in codex_failure_message(prefix+'Error: connection failed')
    assert '用量或请求频率受限' in codex_failure_message('ERROR: usage_limit_reached')
    assert '登录失效' in codex_failure_message('Error: 401 Unauthorized')
    assert '强度不可用' in codex_failure_message('Error: unsupported reasoning effort')


def test_large_review_rejected_before_subprocess(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME',str(tmp_path))
    (tmp_path/'auth.json').write_text('{"auth_mode":"chatgpt"}')
    monkeypatch.setattr('app.services.shutil.which',lambda _: '/mock/codex')
    def forbidden(*args, **kwargs):
        pytest.fail('超长资料不应发起模型请求')
    monkeypatch.setattr('app.services.subprocess.run',forbidden)
    with pytest.raises(ValueError,match='资料过长'):
        run_codex({'note':'x'*1_000_001},set(),[])


def test_max_effort_timeout_is_longer(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME',str(tmp_path))
    (tmp_path/'auth.json').write_text('{"auth_mode":"chatgpt"}')
    monkeypatch.setattr('app.services.shutil.which',lambda _: '/mock/codex')
    def timeout(*args, **kwargs):
        assert kwargs['timeout']==600
        raise subprocess.TimeoutExpired('codex',600)
    monkeypatch.setattr('app.services.subprocess.run',timeout)
    with pytest.raises(ValueError,match='600'):
        run_codex({},set(),[],{'model':'gpt-6.1-sol','effort':'max'})
