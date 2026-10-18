import pytest
from fastapi.testclient import TestClient
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path,background=False)
    with TestClient(app) as c:
        c.headers['X-CSRF-Token']=app.state.csrf
        yield c
    app.state.jobs.pool.shutdown()


def payload(**kw):
    data=dict(code='603230',name='内蒙新华',side='buy',at='2026-10-12T10:00:00',price='14.2',qty=200,expected='15',fee_mode='actual',stamp='0',transfer='.03',commission='4.84',other='.16')
    data.update(kw);return data


def test_browser_routes_and_acceptance_flow(client):
    assert client.get('/').status_code==200
    assert client.get('/static/app.js').status_code==200
    r=client.post('/api/trades',json=payload());assert r.status_code==200
    r=client.post('/api/trades',json=payload(side='sell',at='2026-10-13T10:00:00',price='14.4',stamp='1.44'));assert r.status_code==200
    data=client.get('/api/state').json()
    assert data['cash']=='10028.50' and data['realized']=='28.50'
    assert data['statistics']
    backup=client.get('/api/backup')
    client.post('/api/cash',json=dict(kind='deposit',amount='100',at='2026-10-14T10:00:00'))
    r=client.post('/api/restore',files={'archive':('backup.zip',backup.content,'application/zip')})
    assert r.status_code==200
    assert client.get('/api/state').json()['cash']=='10028.50'


def test_invalid_edit_is_atomic(client):
    ident=client.post('/api/trades',json=payload()).json()['id']
    response=client.put('/api/trades/'+str(ident),json=payload(qty=10000))
    assert response.status_code==400
    assert client.get('/api/state').json()['events'][0]['qty']==200


def test_csrf_and_host(client):
    assert client.post('/api/cash',headers={'X-CSRF-Token':''},json={}).status_code==403
    assert client.post('/api/cash',headers={'Origin':'https://evil.example'},json={}).status_code==403
    assert client.get('/',headers={'Host':'evil.example'}).status_code==400


def test_export_and_image(client):
    import io
    from PIL import Image
    ident=client.post('/api/trades',json=payload()).json()['id']
    image=io.BytesIO();Image.new('RGB',(50,50)).save(image,'PNG')
    r=client.post(f'/api/trades/{ident}/images',files={'image':('test.png',image.getvalue(),'image/png')})
    assert r.status_code==200
    assert client.get('/images/'+r.json()['id']).status_code==200
    assert client.get('/api/export').json()['events'][0]['qty']==200


def test_stock_search_bidirectional_and_failure(client, monkeypatch):
    import requests
    item = {'code': '603230', 'name': '内蒙新华'}
    queries = []
    def lookup(query):
        queries.append(query)
        return [item]
    monkeypatch.setattr('app.main.search_stocks', lookup)
    for query in ('603230', '内蒙新华'):
        assert client.get('/api/stocks', params={'q': query}).json()['items'] == [item]
    assert queries == ['603230', '内蒙新华']
    assert client.get('/api/stocks?q=').json()['items'] == []
    client.post('/api/trades', json=payload())
    def unavailable(query):
        raise requests.Timeout()
    monkeypatch.setattr('app.main.search_stocks', unavailable)
    result = client.get('/api/stocks?q=603230').json()
    assert result['warning'] and result['items'] == [item]
    assert client.get('/api/stocks?q=不存在').json()['items'] == []


def test_expected_price_required_only_for_buy(client):
    buy = payload()
    buy.pop('expected')
    assert client.post('/api/trades', json=buy).status_code == 400
    assert client.post('/api/trades', json=payload()).status_code == 200
    sell = payload(side='sell', at='2026-10-13T10:00:00')
    sell.pop('expected')
    response = client.post('/api/trades', json=sell)
    assert response.status_code == 200
    assert client.put('/api/trades/'+str(response.json()['id']), json=sell).status_code == 200
    assert client.get('/api/state').json()['events'][-1]['expected'] is None


def test_review_options_and_submission(client, monkeypatch):
    options = client.get('/api/review-options').json()
    assert options['defaults']['effort'] == 'high'
    assert 'ultra' not in options['models']['gpt-6-luna']
    client.post('/api/trades', json=payload())
    monkeypatch.setattr(client.app.state.jobs.pool, 'submit', lambda *args: None)
    response = client.post('/api/reviews', json={'code':'603230','model':'gpt-6-astra','effort':'xhigh'})
    assert response.status_code == 200
    job = client.app.state.store.list_rows('jobs')[0]
    assert job['ai_config'] == {'model':'gpt-6-astra','effort':'xhigh'}
    assert client.post('/api/reviews', json={'code':'603230','model':'bad'}).status_code == 400


def test_daily_routes_notes_backup_and_review(client, monkeypatch):
    client.post('/api/trades', json=payload())
    revision=client.get('/api/state').json()['settings']['revision']
    assert client.put('/api/daily/2026-10-12/note',json={'note':'先控制仓位'}).status_code==200
    day=client.get('/api/daily/2026-10-12').json()
    assert day['note']=='先控制仓位' and day['trade_count']==1
    assert day['revision']>revision
    monkeypatch.setattr(client.app.state.jobs.pool,'submit',lambda *args:None)
    r=client.post('/api/reviews',json={'scope':'day','date':'2026-10-12','model':'gpt-6.1-sol','effort':'high'})
    assert r.status_code==200
    job=client.app.state.store.list_rows('jobs')[0]
    assert job['scope']=='day' and job['date']=='2026-10-12' and job['code'] is None
    assert client.post('/api/reviews',json={'scope':'day','date':'2026-10-11'}).status_code==400
    job['status']='done'
    client.app.state.store.put('jobs',job['id'],job)
    backup=client.get('/api/backup').content
    client.put('/api/daily/2026-10-12/note',json={'note':'新说明'})
    assert client.post('/api/restore',files={'archive':('backup.zip',backup,'application/zip')}).status_code==200
    assert client.get('/api/daily/2026-10-12').json()['note']=='先控制仓位'
