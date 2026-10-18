"""本机网页与 JSON 接口；默认只监听回环地址，写操作须携带页面令牌。"""
import io
import json
import os
import secrets
import requests
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .core import DEFAULT_FEES, FEE_KEYS, market, money, normalize_trade, number, replay, statistics
from .services import Jobs, growth_by_entry, closing_comparisons, REVIEW_MODELS, review_config
from .store import Store, now, dumps
from .stocks import search_stocks
from .daily import daily_snapshot, validate_day

BASE = Path(__file__).resolve().parent


def create_app(data_dir=None, background=True):
    """创建单账户应用；测试可注入隔离目录并停用外部后台调用。"""
    app = FastAPI(title='收盘之后 · 个人短线复盘', docs_url=None, redoc_url=None)
    store = Store(data_dir or os.environ.get('STOCK_DATA_DIR', BASE.parent / 'data'))
    jobs = Jobs(store)
    token = secrets.token_urlsafe(32)
    app.state.store, app.state.jobs = store, jobs
    app.state.csrf = token
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost','127.0.0.1','testserver'])
    app.mount('/static', StaticFiles(directory=BASE/'static'), name='static')
    templates = Jinja2Templates(directory=BASE/'templates')

    @app.middleware('http')
    async def local_security(request, call_next):
        if request.method not in ('GET','HEAD','OPTIONS'):
            if request.headers.get('X-CSRF-Token') != token:
                return JSONResponse({'error':'页面已过期，请刷新后重试'},status_code=403)
            origin = request.headers.get('origin')
            if origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse({'error':'不允许跨站写入'},status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; form-action 'self'"
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(KeyError)
    async def missing_field(request, exc):
        return JSONResponse({'error':'缺少必填字段，请检查表单'},status_code=400)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({'error':str(exc)},status_code=400)

    def ledger():
        with store.lock:
            cfg = store.settings()
            events = store.events()
            data = store.market_all()
            result = replay(cfg['initial'],events,store.quotes())
            result.update(settings=cfg, images=store.images(), closing_comparisons=closing_comparisons(events,data),
                          statistics=statistics(result['cycles'],growth_by_entry(events,data)),
                          market=[dict(code=k[0],kind=k[1],**v) for k,v in data.items()])
            return result

    @app.get('/', response_class=HTMLResponse)
    def home(request: Request):
        return templates.TemplateResponse(request=request,name='index.html',context={'csrf':token})

    @app.get('/api/state')
    def state():
        result = ledger()
        # 日常刷新不传全部分钟线，完整行情在证据导出里提供。
        result['market'] = [{k:v for k,v in m.items() if k != 'rows'} for m in result['market']]
        result['jobs'] = store.list_rows('jobs')[:30]
        result['reports'] = [{k:v for k,v in r.items() if k != 'input'} for r in store.list_rows('reports')[:30]]
        return Response(dumps(result),media_type='application/json')

    @app.get('/api/stocks')
    def stocks(q: str = ''):
        """提供双向搜索候选；查询失败时明确提示，历史交易仅用作备用候选。"""
        query = q.strip()
        if not query or len(query) > 40:
            return {'items': [], 'warning': ''}
        try:
            return {'items': search_stocks(query), 'warning': ''}
        except (requests.RequestException, ValueError, KeyError, TypeError):
            history = {}
            for event in store.events():
                if event['kind'] == 'trade' and (query in event['code'] or query in event['name']):
                    history[event['code']] = {'code': event['code'], 'name': event['name']}
            return {'items': list(history.values()),
                    'warning': '股票查询暂不可用；候选来自历史记录，请核对或手工填写。'}

    @app.post('/api/settings')
    async def settings(request: Request):
        raw = await request.json()
        cfg = dict(initial=str(money(number(raw['initial']))))
        fees = raw.get('fees',store.settings()['fees'])
        if set(fees) != set(DEFAULT_FEES):
            raise ValueError('费用配置字段不完整')
        for key,value in fees.items():
            n = number(value)
            if (key != 'minimum' and n > 1) or (key == 'minimum' and n > 10000):
                raise ValueError('费率使用小数，例如万分之1.71填0.000171')
        cfg['fees'] = {k:str(number(v)) for k,v in fees.items()}
        store.mutate(settings=cfg)
        return {'ok':True}

    async def save_trade(request, ident=None):
        raw = await request.json()
        config = store.settings()['fees']
        if ident is not None:
            previous = next((e for e in store.events() if e['id'] == ident and e['kind']=='trade'),None)
            if not previous:
                raise ValueError('交易不存在')
            config = previous.get('fee_config', config)
        event = normalize_trade(raw, config)
        if event['at'] > now()[:19]:
            raise ValueError('不能录入未来成交')
        ident = store.mutate(event,event_id=ident)
        if background:
            jobs.submit(event['code'])
        return {'id':ident}

    @app.post('/api/trades')
    async def add_trade(request: Request):
        """录入买卖成交，保存费用快照并异步获取行情。"""
        return await save_trade(request)

    @app.put('/api/trades/{ident}')
    async def edit_trade(ident: int, request: Request):
        """更正成交并重放账本；沿用原估算费率，实际费用由用户提供。"""
        return await save_trade(request,ident)

    @app.delete('/api/events/{ident}')
    def delete_event(ident: int):
        store.mutate(event_id=ident)
        return {'ok':True}

    @app.post('/api/cash')
    async def cash(request: Request):
        """入金/出金仅改变本金，不计为收益。"""
        raw = await request.json()
        if raw.get('kind') not in ('deposit','withdraw'):
            raise ValueError('请选择入金或出金')
        when = datetime.fromisoformat(raw['at'])
        if when.tzinfo:
            raise ValueError('请输入不带时区偏移的上海本地时间')
        at = when.isoformat(timespec='seconds')
        value = money(number(raw['amount']))
        if value <= 0 or at > now()[:19]:
            raise ValueError('请输入正数金额和已发生的上海时间')
        return {'id':store.mutate(dict(kind=raw['kind'],amount=str(value),at=at,note=str(raw.get('note',''))[:500]))}

    @app.post('/api/adjustments')
    async def adjustment(request: Request):
        """人工登记已到账税后分红及已上市股份调整；保留说明供复盘核对。"""
        raw = await request.json()
        code = str(raw['code'])
        market(code)
        note = str(raw.get('note','')).strip()
        if not note:
            raise ValueError('公司行为调整必须填写依据和说明')
        when = datetime.fromisoformat(raw['at'])
        if when.tzinfo:
            raise ValueError('请输入不带时区偏移的上海本地时间')
        at = when.isoformat(timespec='seconds')
        if at > now()[:19]:
            raise ValueError('调整必须使用已发生的上海时间')
        delta = number(raw.get('qty_delta',0),False)
        if delta != delta.to_integral_value():
            raise ValueError('股份调整必须为整数')
        value = money(number(raw.get('amount',0)))
        return {'id':store.mutate(dict(kind='adjustment',code=code,at=at,amount=str(value),qty_delta=int(delta),note=note[:2000]))}

    @app.post('/api/trades/{ident}/images')
    async def upload(ident: int, image: UploadFile=File(...)):
        """上传并重新编码截图，去掉元数据；每张限制 10 MB。"""
        content = await image.read(10*1024*1024+1)
        if len(content) > 10*1024*1024:
            raise ValueError('截图不能超过 10 MB')
        try:
            image_id = store.attach(ident,content)
        except (OSError, SyntaxError) as exc:
            raise ValueError('无法读取图片，请上传有效截图') from exc
        return {'id':image_id}

    @app.get('/images/{ident}')
    def image(ident: str):
        record = next((i for i in store.images() if i['id']==ident),None)
        if not record:
            raise HTTPException(404)
        return FileResponse(store.root/'images'/record['name'],media_type='image/png')

    @app.get('/api/daily/{day}')
    def daily(day: str):
        """查询指定上海日期的全天流水、持仓与收益口径。"""
        return Response(dumps(daily_snapshot(store, day)), media_type='application/json')

    @app.put('/api/daily/{day}/note')
    async def daily_note(day: str, request: Request):
        """保存当日事后说明并更新修订号；沿用账本设置的备份机制。"""
        validate_day(day)
        raw = await request.json()
        note = raw.get('note', '')
        if not isinstance(note, str) or len(note) > 4000:
            raise ValueError('每日说明最多 4000 字')
        with store.lock:
            notes = store.settings().get('daily_notes', {})
            notes[day] = note
            store.mutate(settings={'daily_notes': notes})
        return {'ok': True}

    @app.get('/api/review-options')
    def review_options():
        """返回复盘支持的配置项；不代表当前账号拥有所有模型的访问权限。"""
        return {'models': REVIEW_MODELS, 'defaults': review_config()}

    @app.post('/api/reviews')
    async def review(request: Request):
        """显式触发复盘；截图默认不发送，勾选后才作为附件传给 Codex。"""
        raw = await request.json()
        scope = raw.get('scope', 'stock')
        if scope not in ('day', 'stock'):
            raise ValueError('复盘范围无效')
        if scope == 'day':
            day = validate_day(raw.get('date'))
            snapshot = daily_snapshot(store, day)
            if not snapshot['events'] and not snapshot['codes']:
                raise ValueError('当天没有操作或持仓可供复盘')
            return {'id': jobs.submit(None, bool(raw.get('review', True)), '', bool(raw.get('include_images', False)),
                                      review_config(raw.get('model'), raw.get('effort')) if raw.get('review', True) else None, day=day)}
        code = str(raw['code'])
        market(code)
        if not any(e.get('code') == code for e in store.events()):
            raise ValueError('该股票尚无交易记录')
        return {'id':jobs.submit(code,bool(raw.get('review',True)),str(raw.get('sector','')),bool(raw.get('include_images',False)),
                                 review_config(raw.get('model'), raw.get('effort')) if raw.get('review',True) else None)}

    @app.get('/api/reports/{ident}/evidence')
    def report_evidence(ident: str):
        report = next((r for r in store.list_rows('reports') if r['id']==ident),None)
        if not report:
            raise HTTPException(404)
        return Response(dumps(report['input']),media_type='application/json',headers={'Content-Disposition':'attachment; filename="review-evidence.json"'})

    @app.get('/api/export')
    def export():
        return Response(dumps(ledger()),media_type='application/json',headers={'Content-Disposition':'attachment; filename="ledger-export.json"'})

    @app.get('/api/backup')
    def backup():
        return Response(store.backup(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="stock-review-backup.zip"'})

    @app.post('/api/restore')
    async def restore(archive: UploadFile=File(...)):
        """恢复前自动保存回滚备份；任务进行时拒绝恢复。"""
        content = await archive.read(100*1024*1024+1)
        if len(content)>100*1024*1024:
            raise ValueError('上传备份超过 100 MB')
        try:
            rollback = store.restore(content)
        except (KeyError,OSError, __import__('zipfile').BadZipFile, __import__('sqlite3').DatabaseError) as exc:
            raise ValueError('备份格式无效或缺少必要数据') from exc
        return {'ok':True,'rollback':rollback}

    @app.get('/api/health')
    def health():
        return {'ok':True,'timezone':'Asia/Shanghai'}

    return app


app = create_app()
