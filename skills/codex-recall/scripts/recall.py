#!/usr/bin/env python3
"""Local Codex transcript archive and search. Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
import collections
import contextlib
import datetime as dt
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import file_index

VERSION = '0.2.0'
SCHEMA = 2
FINAL = {None, '', 'final', 'final_answer', 'commentary'}
ID_RE = re.compile(r'[a-zA-Z0-9_-]{1,100}\Z')
STOPWORDS = set('a an the is are was were i we you my our me to of in on for with and or find search chat task where about that it discussed'.split())


def codex_home():
    return Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).expanduser().resolve()


def data_home():
    return Path(os.environ.get('CODEX_RECALL_HOME', str(codex_home() / 'chat-recall'))).expanduser().resolve()


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(text, encoding='utf-8', newline='\n')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextlib.contextmanager
def writer_lock(root, timeout=30):
    """OS lock is released on crash; lock-file existence is not lock ownership."""
    root.mkdir(parents=True, exist_ok=True)
    f = (root / '.writer.lock').open('a+b')
    f.seek(0, 2)
    if f.tell() == 0:
        f.write(b'0'); f.flush()
    start = time.monotonic()
    while True:
        try:
            f.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() - start >= timeout:
                f.close()
                raise TimeoutError('Archive busy. Retry sync; no source data was changed.')
            time.sleep(0.1)
    try:
        yield
    finally:
        f.seek(0)
        if os.name == 'nt':
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def connect(root):
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'index.sqlite', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version not in (0, 1, SCHEMA):
        raise RuntimeError('Unsupported archive schema. Use the matching release.')
    if version == 1:
        db.executescript('DROP TABLE IF EXISTS vocabulary; DROP TABLE IF EXISTS search_index;')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS tasks(
        id TEXT PRIMARY KEY, title TEXT, project TEXT, cwd TEXT, archived INTEGER,
        source_path TEXT, offset INTEGER DEFAULT 0, size INTEGER DEFAULT 0,
        mtime INTEGER DEFAULT 0, anchor TEXT, mode TEXT, dirty INTEGER DEFAULT 1,
        synced_at TEXT, problem TEXT DEFAULT '');
      CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY, task_id TEXT, event_key TEXT, ordinal INTEGER,
        timestamp TEXT, role TEXT, text TEXT, UNIQUE(task_id,event_key));
      CREATE INDEX IF NOT EXISTS messages_task ON messages(task_id,ordinal);
      CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
        title, project, text, task_id UNINDEXED, message_id UNINDEXED,
        tokenize='unicode61');
      CREATE VIRTUAL TABLE IF NOT EXISTS vocabulary USING fts5vocab(search_index,'row');
      CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
    ''')
    if version == 1:
        db.execute('INSERT INTO search_index(rowid,title,project,text,task_id,message_id) '
                   'SELECT m.id,t.title,t.project,m.text,t.id,m.id FROM messages m JOIN tasks t ON m.task_id=t.id')
    db.execute(f'PRAGMA user_version={SCHEMA}')
    db.commit()
    return db


def metadata(home):
    """Read app metadata without writing to Codex databases."""
    result = {}; warnings = []; projects = {}
    candidates = sorted(home.glob('state_*.sqlite'), key=lambda p: int(re.search(r'_(\d+)', p.name)[1]), reverse=True)
    if candidates:
        try:
            with contextlib.closing(sqlite3.connect(candidates[0].as_uri() + '?mode=ro', uri=True, timeout=5)) as src:
                src.row_factory = sqlite3.Row
                cols = {r[1] for r in src.execute('PRAGMA table_info(threads)')}
                required = {'id', 'title', 'cwd', 'archived', 'rollout_path', 'source'}
                if not required <= cols:
                    raise ValueError('Unknown threads schema')
                names = ['id','title','cwd','archived','rollout_path','source']
                if 'project_id' in cols: names.append('project_id')
                if 'name' in cols: names.append('name')
                if 'project_id' in cols:
                    projects = dict(src.execute('SELECT id,name FROM projects'))
                for row in src.execute('SELECT ' + ','.join(names) + ' FROM threads'):
                    r = dict(row)
                    r['title'] = r.get('name') or r['title']
                    r['project'] = projects.get(r.get('project_id'), 'No project')
                    result[r['id']] = r
        except (sqlite3.Error, ValueError) as e:
            warnings.append('Codex metadata unavailable: ' + str(e))
    global_path = home / '.codex-global-state.json'
    if global_path.exists():
        try:
            state = json.loads(global_path.read_text(encoding='utf-8'))
            local_projects = state.get('local-projects', {})
            assignments = state.get('thread-project-assignments', {})
            migrations = state.get('app-server-project-id-by-legacy-project-id-by-host', {})
            migrated = {}
            for host_map in migrations.values():
                if isinstance(host_map, dict): migrated.update(host_map)
            for ident, assignment in assignments.items():
                if ident not in result or not isinstance(assignment, dict): continue
                pid = assignment.get('projectId')
                local = local_projects.get(pid, {}) if isinstance(local_projects,dict) else {}
                name = local.get('name') or projects.get(migrated.get(pid)) or projects.get(pid)
                if name: result[ident]['project'] = name
        except (ValueError, AttributeError, TypeError) as e:
            warnings.append('Desktop project metadata unavailable: ' + str(e))
    index = home / 'session_index.jsonl'
    if index.exists():
        for line in index.open(encoding='utf-8'):
            try:
                row = json.loads(line); ident = row.get('id') or row.get('session_id')
                if ident and ident not in result:
                    result[ident] = {'title': row.get('thread_name') or row.get('title') or ident}
            except ValueError:
                continue
    return result, warnings


def text_content(content):
    if isinstance(content, str): return content
    if not isinstance(content, list): return ''
    return '\n'.join(c['text'] for c in content if isinstance(c, dict)
                     and str(c.get('type', '')).lower() in ('text','input_text','output_text')
                     and isinstance(c.get('text'), str))


def timestamp(value):
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc).isoformat()
    if isinstance(value, str):
        try: return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).isoformat()
        except ValueError: pass
    return 'Unknown'


def injected(text):
    s = text.lstrip()
    return s.startswith(('<environment_context>', '<permissions instructions>', '<INSTRUCTIONS>',
                         '<recommended_plugins>', '# AGENTS.md instructions',
                         'Warning: you have', '<subagent_notification>'))


def extract(record, position):
    """Return a candidate with its schema family, never tool or reasoning text."""
    p = record.get('payload', {})
    if not isinstance(p, dict): return None
    outer = record.get('type'); kind = p.get('type'); item = None; family = None
    role = None; phase = None; text = ''; key = str(position)
    stamp = record.get('timestamp')
    if outer == 'event_msg' and kind == 'item_completed':
        item = p.get('item', {})
        typ = str(item.get('type', '')).lower()
        if typ == 'usermessage': role = 'user'
        elif typ == 'agentmessage': role = 'assistant'
        else: return None
        family = 'items'; phase = item.get('phase')
        text = item.get('text') or text_content(item.get('content'))
        key = str(item.get('id') or position)
        stamp = p.get('completed_at_ms') or stamp
    elif outer == 'event_msg' and kind in ('user_message','agent_message'):
        family = 'events'; role = 'user' if kind == 'user_message' else 'assistant'
        phase = p.get('phase'); text = p.get('message', '')
    elif outer == 'response_item' and kind == 'message' and p.get('role') in ('user','assistant'):
        family = 'responses'; role = p['role']; phase = p.get('phase')
        text = text_content(p.get('content')); key = str(p.get('id') or position)
        if role == 'user' and injected(text): return None
    else: return None
    if role == 'assistant' and phase not in FINAL: return None
    if not isinstance(text, str) or not text.strip(): return None
    return family, key, position, timestamp(stamp), role, text


def read_tail(path, offset):
    candidates = []; meta = {}; problems = []; end = offset; read_bytes = 0
    with path.open('rb') as f:
        f.seek(offset)
        while True:
            position = f.tell(); line = f.readline()
            if not line: break
            read_bytes += len(line)
            # A writer can be halfway through a JSON line. Retry it on the next run.
            if not line.endswith(b'\n'): break
            try: record = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                problems.append(f'Invalid JSON at byte {position}; cursor stopped for repair')
                break
            end = f.tell()
            if record.get('type') == 'session_meta': meta = record.get('payload', {})
            candidate = extract(record, position)
            if candidate: candidates.append(candidate)
    return candidates, meta, problems, end, read_bytes


def anchor(path, offset):
    with path.open('rb') as f:
        f.seek(max(0, offset - 256))
        return hashlib.sha256(f.read(min(256, offset))).hexdigest()


def subagent(source):
    if isinstance(source, dict): return 'subagent' in source
    return 'subagent' in str(source).lower()


def render_task(db, root, task_id):
    task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    clean = lambda s: str(s).replace('\r',' ').replace('\n',' ')
    lines = ['# ' + clean(task['title']), '', 'Project: ' + clean(task['project']),
             'Task ID: ' + task_id, 'Archived: ' + ('yes' if task['archived'] else 'no'),
             'Task link: codex://threads/' + task_id,
             'Timestamps: ISO 8601 with timezone; weekday shown in UTC.', '']
    for row in db.execute('SELECT * FROM messages WHERE task_id=? ORDER BY ordinal,id', (task_id,)):
        stamp = row['timestamp']; day = ''
        if stamp != 'Unknown':
            day = dt.datetime.fromisoformat(stamp).astimezone(dt.timezone.utc).strftime('%A') + ', '
        lines.extend(['## ' + ('You' if row['role']=='user' else 'Codex') + ' | ' + day + stamp,
                      '', row['text'], ''])
    atomic_text(root / 'tasks' / (task_id + '.md'), '\n'.join(lines))
    db.execute('UPDATE tasks SET dirty=0 WHERE id=?', (task_id,))


def sync(home, root, only=None, rebuild=False):
    started = time.monotonic(); report = {'files_checked':0,'files_changed':0,'bytes_read':0,'messages_added':0,'warnings':[]}
    with writer_lock(root):
        db = connect(root)
        try:
            metas, warnings = metadata(home); report['warnings'].extend(warnings)
            if only:
                p = Path(only).expanduser().resolve()
                allowed = [(home / x).resolve() for x in ('sessions','archived_sessions')]
                if not any(p.is_relative_to(a) for a in allowed):
                    raise ValueError('Transcript must be inside CODEX_HOME sessions or archived_sessions')
                paths = [p]
            else:
                paths = sorted(set(p.resolve() for folder in ('sessions','archived_sessions')
                                   for p in (home / folder).rglob('*.jsonl')))
            if not paths:
                report['warnings'].append('No local transcripts found in the configured Codex home')
            for path in paths:
                report['files_checked'] += 1
                match = re.search(r'([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})', path.stem)
                ident = match[1] if match else None
                # The first metadata record also supports non-UUID synthetic sessions.
                known = db.execute('SELECT id FROM tasks WHERE id=?', (ident,)).fetchone() if ident else None
                sm = {}
                if not known:
                    with path.open('rb') as f:
                        try:
                            first = json.loads(f.readline())
                            sm = first.get('payload', {}) if first.get('type')=='session_meta' else {}
                        except ValueError: sm = {}
                ident = sm.get('id') or sm.get('session_id') or ident
                if not ident or not ID_RE.fullmatch(ident):
                    report['warnings'].append('Unrecognized session identity: ' + path.name); continue
                info = metas.get(ident, {})
                canonical = info.get('rollout_path')
                if canonical:
                    # Windows extended-length path prefixes can differ for the same file.
                    canonical_path = Path(canonical.removeprefix('\\\\?\\')).resolve()
                    if canonical_path.exists() and canonical_path != path:
                        continue
                if subagent(info.get('source', sm.get('source'))): continue
                stat = path.stat(); previous = db.execute('SELECT * FROM tasks WHERE id=?', (ident,)).fetchone()
                title = info.get('title') or (previous['title'] if previous else ident)
                project = info.get('project') or (previous['project'] if previous else 'Unknown project')
                cwd = info.get('cwd') or sm.get('cwd') or (previous['cwd'] if previous else '')
                archived = int(info.get('archived', 'archived_sessions' in path.parts))
                meta_changed = not previous or str(path) != previous['source_path'] or (title,project,cwd,archived) != tuple(previous[x] for x in ('title','project','cwd','archived'))
                changed = rebuild or not previous or stat.st_size != previous['size'] or stat.st_mtime_ns != previous['mtime']
                if not changed and not meta_changed and (root/'tasks'/f'{ident}.md').exists(): continue
                offset = previous['offset'] if previous else 0
                reset = bool(previous and (rebuild or stat.st_size < offset or anchor(path, offset) != previous['anchor']
                             or (stat.st_size == previous['size'] and stat.st_mtime_ns != previous['mtime'])))
                if reset: offset = 0
                items, sm_tail, problems, end, nbytes = read_tail(path, offset) if changed else ([],{},[],offset,0)
                report['bytes_read'] += nbytes
                mode = previous['mode'] if previous and not reset else None
                found = {x[0] for x in items}
                best = next((x for x in ('items','events','responses') if x in found), mode)
                if mode and best and ('items','events','responses').index(best) < ('items','events','responses').index(mode):
                    items, sm_tail, problems, end, nb = read_tail(path, 0); report['bytes_read'] += nb; reset = True; mode = best
                mode = mode or best
                selected = [x for x in items if x[0] == mode]
                content_changed = bool(selected or reset or meta_changed)
                needs_render = content_changed or not (root/'tasks'/f'{ident}.md').exists()
                if title == ident:
                    first_user = next((x[5] for x in selected if x[4]=='user'), '')
                    if first_user: title = first_user.splitlines()[0][:100]
                with db:
                    if not previous:
                        db.execute('INSERT INTO tasks(id) VALUES(?)', (ident,))
                    if content_changed:
                        db.execute('DELETE FROM search_index WHERE rowid IN (SELECT id FROM messages WHERE task_id=?)', (ident,))
                    if reset: db.execute('DELETE FROM messages WHERE task_id=?', (ident,))
                    for family,key,pos,stamp,role,text in selected:
                        before = db.total_changes
                        db.execute('INSERT INTO messages(task_id,event_key,ordinal,timestamp,role,text) VALUES(?,?,?,?,?,?) '
                                   'ON CONFLICT(task_id,event_key) DO UPDATE SET timestamp=excluded.timestamp,text=excluded.text',
                                   (ident, family+':'+key, pos, stamp, role, text))
                        report['messages_added'] += db.total_changes - before
                    problem = '; '.join(problems)
                    if mode == 'responses': problem = (problem + '; Response-only fallback: review for injected user context').strip('; ')
                    db.execute('UPDATE tasks SET title=?,project=?,cwd=?,archived=?,source_path=?,offset=?,size=?,mtime=?,anchor=?,mode=?,dirty=max(dirty,?),synced_at=?,problem=? WHERE id=?',
                               (title,project,cwd,archived,str(path),end,stat.st_size,stat.st_mtime_ns,anchor(path,end),mode,int(needs_render),
                                dt.datetime.now(dt.timezone.utc).isoformat(),problem,ident))
                    if content_changed:
                        db.execute('INSERT INTO search_index(rowid,title,project,text,task_id,message_id) SELECT id,?,?,text,task_id,id FROM messages WHERE task_id=?',
                                   (title,project,ident))
                report['files_changed'] += 1
                report['warnings'].extend(f'{ident}: {p}' for p in problems)
                if report['files_changed'] % 100 == 0 and sys.stderr.isatty():
                    print(f"Indexed {report['files_changed']} tasks", file=sys.stderr)
            # Dirty flag survives a crash between DB commit and atomic Markdown replace.
            for row in db.execute('SELECT id FROM tasks WHERE dirty=1').fetchall():
                render_task(db, root, row[0]); db.commit()
            with db:
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('last_sync',dt.datetime.now(dt.timezone.utc).isoformat()))
            report['tasks'] = db.execute('SELECT count(*) FROM tasks').fetchone()[0]
            report['messages'] = db.execute('SELECT count(*) FROM messages').fetchone()[0]
            report['warnings'] = list(dict.fromkeys(report['warnings'] +
                [r['id']+': '+r['problem'] for r in db.execute("SELECT id,problem FROM tasks WHERE problem!=''")]))
        finally: db.close()
    with writer_lock(root):
        report['files'] = file_index.sync(root)
    report['warnings'].extend(report['files']['warnings'])
    report['seconds'] = round(time.monotonic()-started,3)
    return report


def tokens(query):
    return list(dict.fromkeys(t.casefold() for t in re.findall(r'[^\W_]+', query, re.UNICODE) if t.casefold() not in STOPWORDS))[:16]


def search(root, query, limit=5, project=None, archived='all', since=None, related=()):
    db = connect(root)
    try:
        terms = tokens(query)
        if not terms: return {'mode':'quick','matches':[],'note':'Enter a distinctive word or phrase.'}
        conditions = []; values = []
        if project: conditions.append('t.project LIKE ? ESCAPE "\\"'); values.append('%'+project.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
        if archived != 'all': conditions.append('t.archived=?'); values.append(int(archived=='yes'))
        if since:
            dt.date.fromisoformat(since)
            conditions.append("m.timestamp != 'Unknown' AND m.timestamp>=?"); values.append(since)
        where = (' AND ' + ' AND '.join(conditions)) if conditions else ''
        def run(expression):
            return db.execute('SELECT t.id,t.title,t.project,t.archived,m.timestamp,m.id AS message_id,m.role,'
                              "snippet(search_index,2,'[',']',' ... ',36) AS excerpt,"
                              'bm25(search_index,6,3,1) AS rank FROM search_index '
                              'JOIN tasks t ON t.id=search_index.task_id JOIN messages m ON m.id=search_index.message_id '
                              'WHERE search_index MATCH ?' + where + ' ORDER BY rank LIMIT 120', [expression]+values).fetchall()
        quote = lambda s: '"' + s.replace('"','""') + '"'
        expression = ' AND '.join(quote(t) for t in terms)
        rows = run(expression); strategy = 'all words'; corrections = {}
        if not rows:
            groups = []
            for t in terms:
                vocab = [r[0] for r in db.execute('SELECT term FROM vocabulary WHERE term GLOB ? AND length(term) BETWEEN ? AND ? LIMIT 15000', (t[0]+'*',max(1,len(t)-2),len(t)+2))]
                close = difflib.get_close_matches(t,vocab,n=2,cutoff=0.78) if len(t)>=4 else []
                alternatives = list(dict.fromkeys([t]+close))
                if len(alternatives)>1: corrections[t] = alternatives[1:]
                groups.append('('+' OR '.join(quote(x) for x in alternatives)+')')
            rows = run(' AND '.join(groups)); strategy = 'fuzzy words'
        if not rows:
            rows = run(' OR '.join(quote(t) for t in terms)); strategy = 'some words'
        if related:
            more = [t for phrase in related for t in tokens(phrase)]
            if more: rows = list(rows) + list(run(' OR '.join(quote(t) for t in more)))
        results = []; seen = set()
        for row in sorted(rows,key=lambda r:r['rank']):
            if row['id'] in seen: continue
            seen.add(row['id']); d = dict(row); d.pop('rank')
            d['url'] = 'codex://threads/'+d['id']; d['file'] = str(root/'tasks'/(d['id']+'.md'))
            results.append(d)
            if len(results)>=limit: break
        preference = db.execute("SELECT value FROM settings WHERE key='search_preference'").fetchone()
        last = db.execute("SELECT value FROM settings WHERE key='last_sync'").fetchone()
        return {'mode':'quick','strategy':strategy,'corrections':corrections,'matches':results,
                'file_matches':file_index.search(root,query,limit,project,archived) if not since else [],
                'last_sync':last[0] if last else None, 'preference':preference[0] if preference else 'ask',
                'ai_fallback':'Ask before AI-assisted expansion unless the user has opted in. No model was called.'}
    finally: db.close()


def context(root, task_id, message_id, radius=1):
    db = connect(root)
    try:
        rows = db.execute('SELECT id,timestamp,role,text FROM messages WHERE task_id=? ORDER BY ordinal,id',(task_id,)).fetchall()
        index = next((i for i,r in enumerate(rows) if r['id']==message_id),None)
        if index is None: raise ValueError('Message does not belong to that task')
        result = []
        selected = rows[max(0,index-radius):index+radius+1]
        per_message = min(4000,6000//len(selected))
        for row in selected:
            d = dict(row);d['truncated'] = len(d['text'])>per_message;d['text'] = d['text'][:per_message];result.append(d)
        return {'task_id':task_id,'messages':result}
    finally: db.close()


def status(root):
    db = connect(root)
    try:
        return {'version':VERSION,'archive':str(root),'tasks':db.execute('SELECT count(*) FROM tasks').fetchone()[0],
                'messages':db.execute('SELECT count(*) FROM messages').fetchone()[0],
                'pending_exports':db.execute('SELECT count(*) FROM tasks WHERE dirty=1').fetchone()[0],
                'warnings':[dict(x) for x in db.execute("SELECT id,problem FROM tasks WHERE problem!=''")],
                'settings':dict(db.execute('SELECT key,value FROM settings')),
                'markdown_bytes':sum(p.stat().st_size for p in (root/'tasks').glob('*.md'))}
    finally: db.close()


def day_report(root, day):
    dt.date.fromisoformat(day)
    db = connect(root)
    try:
        counts = collections.Counter()
        date = dt.date.fromisoformat(day)
        start = (date-dt.timedelta(days=1)).isoformat()
        end = (date+dt.timedelta(days=2)).isoformat()
        for row in db.execute("SELECT task_id,timestamp FROM messages WHERE timestamp>=? AND timestamp<?",(start,end)):
            if dt.datetime.fromisoformat(row['timestamp']).astimezone().date() == date:
                counts[row['task_id']] += 1
        rows = [dict(row, messages=counts[row['id']]) for row in db.execute('SELECT id,title,project,archived FROM tasks ORDER BY title') if row['id'] in counts]
        # No copies of transcript bodies in daily reports.
        lines = ['# Codex activity: '+day+' (computer local time)', '', '| Task | Project | Messages | Archived |','| --- | --- | ---: | --- |']
        for row in rows:
            clean=lambda v:str(v).replace('|','/').replace('\n',' ').replace('[','(').replace(']',')')
            lines.append(f"| [{clean(row['title'])}](codex://threads/{row['id']}) | {clean(row['project'])} | {row['messages']} | {'yes' if row['archived'] else 'no'} |")
        path=root/'daily'/(day+'.md');atomic_text(path,'\n'.join(lines)+'\n')
        return {'date':day,'timezone':'computer local time','tasks':len(rows),'file':str(path)}
    finally: db.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',type=Path,default=codex_home(),help='Codex data directory (read only)')
    parser.add_argument('--archive',type=Path,default=data_home(),help='Private output directory')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('sync');p.add_argument('--transcript',type=Path);p.add_argument('--rebuild',action='store_true')
    p=sub.add_parser('search');p.add_argument('query');p.add_argument('--limit',type=int,default=5)
    p.add_argument('--project');p.add_argument('--archived',choices=['all','yes','no'],default='all');p.add_argument('--since')
    p.add_argument('--related',action='append',default=[])
    p=sub.add_parser('context');p.add_argument('task_id');p.add_argument('message_id',type=int);p.add_argument('--radius',type=int,default=1)
    sub.add_parser('status')
    p=sub.add_parser('end-day');p.add_argument('--date',default=dt.datetime.now().date().isoformat())
    p=sub.add_parser('prefer');p.add_argument('mode',choices=['ask','quick','ai'])
    sub.add_parser('hook')
    file_index.add_parser(sub)
    args=parser.parse_args(argv);home=args.home.expanduser().resolve();root=args.archive.expanduser().resolve()
    try:
        if args.command=='sync': result=sync(home,root,args.transcript,args.rebuild)
        elif args.command=='files':
            with writer_lock(root):result=file_index.command(root,args)
        elif args.command=='search': result=search(root,args.query,max(1,min(args.limit,20)),args.project,args.archived,args.since,args.related)
        elif args.command=='context': result=context(root,args.task_id,args.message_id,max(0,min(args.radius,2)))
        elif args.command=='status': result=status(root)
        elif args.command=='prefer':
            with writer_lock(root):
                db=connect(root)
                with db: db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('search_preference',args.mode))
                db.close()
            result={'preference':args.mode,'note':'AI preference permits Codex-assisted search, never automatic API calls.'}
        elif args.command=='end-day': result={'sync':sync(home,root),'report':day_report(root,args.date)}
        else:
            payload=json.load(sys.stdin)
            path=payload.get('transcript_path')
            if not path: raise ValueError('Hook supplied no transcript_path; run sync to catch up')
            result=sync(home,root,path)
            if result['warnings']: raise RuntimeError('; '.join(str(w) for w in result['warnings'])[:500])
            print('{}');return 0
        print(json.dumps(result,ensure_ascii=True,indent=2))
        return 1 if isinstance(result,dict) and (result.get('warnings') or result.get('sync',{}).get('warnings')) else 0
    except Exception as exc:
        if args.command=='hook':
            print(json.dumps({'systemMessage':'Codex Recall could not update the archive: '+str(exc)[:500]}))
            return 0  # Never use exit 2: it would request another model turn.
        print(json.dumps({'error':str(exc)}),file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
