"""SQLite 持久化与一致性备份；写入前重放账本，失败整笔回滚。"""
import io
import json
import os
import secrets
import sqlite3
import threading
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from .core import DEFAULT_FEES, replay


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')


def dumps(value):
    return json.dumps(value, ensure_ascii=False, default=str)


class Store:
    """一个本地账户，串行写入；每次修改产生修订号供复盘标注过期。"""
    def __init__(self, directory):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        (self.root / 'images').mkdir(exist_ok=True)
        self.path = self.root / 'ledger.sqlite3'
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS images (id TEXT PRIMARY KEY, event_id INTEGER NOT NULL, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS market (code TEXT, kind TEXT, body TEXT NOT NULL, PRIMARY KEY(code,kind));
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL);
            ''')
            db.execute('INSERT OR IGNORE INTO settings VALUES (1,?)', (dumps(dict(initial='10000.00', fees=DEFAULT_FEES, revision=0)),))
        # 单进程服务重启后遗留任务不得永久显示运行中。
        for job in self.list_rows('jobs'):
            if job.get('status') in ('pending', 'running'):
                job.update(status='failed', error='服务重启，任务中断；可重新发起', finished=now())
                self.put('jobs', job['id'], job)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=20)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def settings(self):
        with self.connect() as db:
            return json.loads(db.execute('SELECT body FROM settings WHERE id=1').fetchone()[0])

    def events(self, db=None):
        if db is None:
            with self.connect() as conn:
                return self.events(conn)
        return [dict(json.loads(body), id=ident) for ident, body in db.execute('SELECT id,body FROM events')]

    def images(self):
        with self.connect() as db:
            return [dict(id=r[0], event_id=r[1], name=r[2]) for r in db.execute('SELECT id,event_id,name FROM images')]

    def list_rows(self, table):
        assert table in ('jobs', 'reports', 'audit')
        with self.connect() as db:
            return [dict(json.loads(body), id=ident) for ident, body in db.execute('SELECT id,body FROM '+table+' ORDER BY rowid DESC')]

    def put(self, table, ident, value):
        assert table in ('jobs', 'reports')
        with self.lock, self.connect() as db:
            db.execute('INSERT OR REPLACE INTO '+table+' VALUES (?,?)', (ident, dumps(value)))

    def mutate(self, event=None, event_id=None, settings=None):
        """原子增加、更正或删除流水，校验后更新修订号并保留审计记录。"""
        with self.lock, self.connect() as db:
            cfg = json.loads(db.execute('SELECT body FROM settings WHERE id=1').fetchone()[0])
            previous = None
            if settings:
                cfg.update(settings)
            elif event_id is not None:
                row = db.execute('SELECT body FROM events WHERE id=?', (event_id,)).fetchone()
                if not row:
                    raise ValueError('交易不存在')
                previous = json.loads(row[0])
                if event is None:
                    db.execute('DELETE FROM events WHERE id=?', (event_id,))
                    db.execute('DELETE FROM images WHERE event_id=?', (event_id,))
                else:
                    event['created_at'] = previous.get('created_at', now())
                    event['updated_at'] = now()
                    db.execute('UPDATE events SET body=? WHERE id=?', (dumps(event), event_id))
            elif event is not None:
                event['created_at'] = now()
                event_id = db.execute('INSERT INTO events(body) VALUES (?)', (dumps(event),)).lastrowid
            replay(cfg['initial'], self.events(db))
            cfg['revision'] += 1
            db.execute('UPDATE settings SET body=? WHERE id=1', (dumps(cfg),))
            db.execute('INSERT INTO audit(body) VALUES (?)', (dumps(dict(at=now(), event_id=event_id, previous=previous, new=event, settings=settings)),))
            return event_id

    def attach(self, event_id, content):
        from PIL import Image
        image = Image.open(io.BytesIO(content))
        if image.format not in ('PNG', 'JPEG', 'WEBP') or image.width * image.height > 24000000:
            raise ValueError('仅支持不超过 2400 万像素的 PNG/JPEG/WebP 截图')
        image.load()
        ident = secrets.token_hex(16)
        target = self.root / 'images' / (ident + '.png')
        with self.lock, self.connect() as db:
            if not db.execute('SELECT 1 FROM events WHERE id=?', (event_id,)).fetchone():
                raise ValueError('交易不存在')
            if db.execute('SELECT count(*) FROM images WHERE event_id=?', (event_id,)).fetchone()[0] >= 5:
                raise ValueError('每笔交易最多五张截图')
            image.convert('RGB').save(target, 'PNG')
            db.execute('INSERT INTO images VALUES (?,?,?)', (ident, event_id, target.name))
            cfg = json.loads(db.execute('SELECT body FROM settings WHERE id=1').fetchone()[0])
            cfg['revision'] += 1
            db.execute('UPDATE settings SET body=? WHERE id=1', (dumps(cfg),))
        return ident

    def market_all(self):
        with self.connect() as db:
            return {(code, kind): json.loads(body) for code, kind, body in db.execute('SELECT code,kind,body FROM market')}

    def save_market(self, code, kind, result):
        with self.lock, self.connect() as db:
            old = db.execute('SELECT body FROM market WHERE code=? AND kind=?', (code, kind)).fetchone()
            previous = json.loads(old[0]) if old else {}
            merged = {str(r['at']): r for r in previous.get('rows', [])}
            merged.update({str(r['at']): r for r in result.get('rows', [])})
            result['rows'] = [merged[k] for k in sorted(merged)]
            if not result.get('source'):
                result['source'] = previous.get('source')
            if result['rows']:
                result['coverage'] = [result['rows'][0]['at'], result['rows'][-1]['at']]
            db.execute('INSERT OR REPLACE INTO market VALUES (?,?,?)', (code, kind, dumps(result)))

    def quotes(self):
        result = {}
        for (code, kind), data in self.market_all().items():
            if kind not in ('daily', 'minute'):
                continue
            for row in data.get('rows', []):
                at = row['at'] if kind == 'minute' else row['at'][:10] + 'T15:00:00'
                # 未收盘的日线不假装已知收盘价。
                if at > now()[:19]:
                    continue
                if code not in result or at > result[code]['at']:
                    result[code] = dict(at=at, price=row['close'])
        return result

    def backup(self):
        """使用 SQLite 在线备份获取一致快照，再与被引用截图一起打包。"""
        with self.lock:
            temp = self.root / ('.backup-' + secrets.token_hex(8) + '.sqlite3')
            try:
                with self.connect() as src:
                    dest = sqlite3.connect(str(temp))
                    try:
                        src.backup(dest)
                    finally:
                        dest.close()
                output = io.BytesIO()
                with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
                    z.writestr('manifest.json', dumps({'version': 1, 'created': now()}))
                    z.write(temp, 'ledger.sqlite3')
                    for img in self.images():
                        z.write(self.root / 'images' / img['name'], 'images/' + img['name'])
                return output.getvalue()
            finally:
                temp.unlink(missing_ok=True)

    def restore(self, content):
        """校验备份后恢复；恢复前保存回滚备份，不解压任意用户路径。"""
        import tempfile
        import shutil
        import re
        with self.lock:
            if any(j.get('status') in ('running', 'pending') for j in self.list_rows('jobs')):
                raise ValueError('请等待行情/复盘任务结束后恢复')
            with zipfile.ZipFile(io.BytesIO(content)) as z, tempfile.TemporaryDirectory(dir=self.root) as work:
                names = z.namelist()
                if len(names) != len(set(names)) or sum(i.file_size for i in z.infolist()) > 250 * 1024 * 1024:
                    raise ValueError('备份重复条目或解压后超过 250 MB')
                if json.loads(z.read('manifest.json')).get('version') != 1:
                    raise ValueError('不支持此备份版本')
                if any(n not in ('manifest.json', 'ledger.sqlite3') and not re.fullmatch(r'images/[0-9a-f]{32}\.png', n) for n in names):
                    raise ValueError('备份包含不允许的路径')
                candidate = Path(work) / 'ledger.sqlite3'
                candidate.write_bytes(z.read('ledger.sqlite3'))
                db = sqlite3.connect(str(candidate))
                try:
                    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('数据库完整性校验失败')
                    expected = {'settings','events','images','market','jobs','reports','audit'}
                    schema = db.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
                    if {name for typ,name in schema if typ == 'table'} != expected or any(typ not in ('table','index') for typ,name in schema):
                        raise ValueError('备份数据库结构不受支持')
                    cfg = json.loads(db.execute('SELECT body FROM settings WHERE id=1').fetchone()[0])
                    replay(cfg['initial'], self.events(db))
                    imgs = db.execute('SELECT name FROM images').fetchall()
                    for (name,) in imgs:
                        if not re.fullmatch(r'[0-9a-f]{32}\.png', name) or 'images/'+name not in names:
                            raise ValueError('备份截图缺失或名称无效')
                    # 恢复的报告可查看，原任务不继续运行。
                    db.execute('DELETE FROM jobs')
                    db.commit()
                finally:
                    db.close()
                rollback = self.root / ('before-restore-' + secrets.token_hex(6) + '.zip')
                rollback.write_bytes(self.backup())
                for (name,) in imgs:
                    (self.root / 'images' / name).write_bytes(z.read('images/'+name))
                os.replace(candidate, self.path)
                return rollback.name
