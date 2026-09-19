"""Opt-in local file indexing. No network access or automatic model calls."""
import collections
import contextlib
import difflib
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile

MAX_BYTES = 5 * 1024 * 1024
MAX_TEXT = 200000
SUPPORTED = {'.txt','.md','.markdown','.rst','.csv','.json','.yaml','.yml','.html','.htm','.docx','.pptx','.pdf'}
STOP = set('the and for with that this from have your you are was were will not but can into they their our has its about then than also'.split())


def connect(root):
    root.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(root/'files.sqlite',timeout=30);db.row_factory=sqlite3.Row
    db.executescript('''
    CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY,hash TEXT,size INTEGER,mtime INTEGER,error TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS links(path TEXT,task_id TEXT,PRIMARY KEY(path,task_id));
    CREATE TABLE IF NOT EXISTS blobs(hash TEXT PRIMARY KEY,text TEXT,headings TEXT,keywords TEXT,summary TEXT,truncated INTEGER,ai_summary TEXT DEFAULT '');
    CREATE VIRTUAL TABLE IF NOT EXISTS file_search USING fts5(hash UNINDEXED,text,headings,keywords,summary,ai_summary);
    CREATE VIRTUAL TABLE IF NOT EXISTS file_vocab USING fts5vocab(file_search,'row');
    ''')
    return db


def setting(db,key,default='off'):
    row=db.execute('SELECT value FROM config WHERE key=?',(key,)).fetchone()
    return row[0] if row else default


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__();self.parts=[];self.headings=[];self.hidden=0;self.heading=None
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.hidden+=1
        if re.fullmatch('h[1-6]',tag):self.heading=[]
        if tag in ('p','div','br','li') or re.fullmatch('h[1-6]',tag):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
        if re.fullmatch('h[1-6]',tag) and self.heading is not None:
            self.headings.append(''.join(self.heading));self.heading=None;self.parts.append('\n')
    def handle_data(self,data):
        if not self.hidden:
            self.parts.append(data)
            if self.heading is not None:self.heading.append(data)


def extract(path,data):
    suffix=path.suffix.lower();headings=[]
    if suffix in ('.docx','.pptx'):
        import io
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names=[n for n in z.namelist() if n=='word/document.xml' or re.fullmatch(r'ppt/slides/slide\d+\.xml',n)]
            if sum(z.getinfo(n).file_size for n in names)>20*1024*1024:raise ValueError('Expanded document exceeds 20 MB limit')
            paragraphs=[]
            for name in sorted(names,key=lambda n:int(re.search(r'slide(\d+)',n)[1]) if re.search(r'slide(\d+)',n) else 0):
                raw=z.read(name)
                if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:raise ValueError('XML entity definitions are not supported')
                tree=ET.fromstring(raw)
                if suffix=='.pptx':
                    for shape in tree.iter():
                        if shape.tag.split('}')[-1]=='sp' and any(n.tag.split('}')[-1]=='ph' and n.attrib.get('type') in ('title','ctrTitle') for n in shape.iter()):
                            headings.append(''.join(n.text or '' for n in shape.iter() if n.tag.split('}')[-1]=='t'))
                for p in tree.iter():
                    if p.tag.split('}')[-1]!='p':continue
                    text=''.join(t.text or '' for t in p.iter() if t.tag.split('}')[-1]=='t')
                    if text:
                        paragraphs.append(text)
                        styles=[str(v) for node in p.iter() if node.tag.split('}')[-1]=='pStyle' for v in node.attrib.values()]
                        if any(s.lower().startswith(('heading','title')) for s in styles):headings.append(text)
            text='\n'.join(paragraphs)
    elif suffix=='.pdf':
        try:from pypdf import PdfReader
        except ImportError:raise ValueError('PDF indexing needs optional pypdf; no dependency was installed automatically') from None
        import io
        reader=PdfReader(io.BytesIO(data))
        if reader.is_encrypted:raise ValueError('Encrypted PDF skipped')
        if len(reader.pages)>300:raise ValueError('PDF exceeds 300-page limit')
        text='\n'.join(p.extract_text() or '' for p in reader.pages)
    else:
        text=data.decode('utf-8-sig')
        if '\x00' in text:raise ValueError('Binary or unsupported text encoding')
        if suffix in ('.html','.htm'):
            parser=HTMLText();parser.feed(text);text=''.join(parser.parts);headings=parser.headings
        elif suffix in ('.md','.markdown'):
            headings=re.findall(r'^#{1,6}\s+(.+)$',text,re.M)
    if not text.strip():raise ValueError('No extractable text; OCR is not included')
    truncated=len(text)>MAX_TEXT;text=text[:MAX_TEXT]
    words=[x.casefold() for x in re.findall(r'[^\W\d_]{3,}',text) if len(x)<=60 and x.casefold() not in STOP]
    keywords=[w for w,_ in collections.Counter(words).most_common(20)]
    # This is labeled an extract, never presented as an AI-written summary.
    summary='\n'.join(p.strip() for p in text.splitlines() if p.strip())[:1000]
    return text,[h.strip()[:200] for h in headings[:30]],keywords,summary,truncated


def refresh_index(db,digest):
    db.execute('DELETE FROM file_search WHERE hash=?',(digest,))
    db.execute('INSERT INTO file_search SELECT hash,text,headings,keywords,summary,ai_summary FROM blobs WHERE hash=?',(digest,))


def sync(root):
    if not (root/'files.sqlite').exists():return {'enabled':False,'changed':0,'warnings':[]}
    db=connect(root);report={'enabled':setting(db,'enabled')=='on','changed':0,'reused':0,'warnings':[]}
    try:
        if not report['enabled']:return report
        for row in db.execute('SELECT * FROM files').fetchall():
            path=Path(row['path'])
            try:
                if path.resolve()!=path:raise ValueError('Registered file now redirects to another path; re-register it explicitly')
                stat=path.stat()
                if stat.st_size>MAX_BYTES:raise ValueError('File exceeds 5 MB limit')
                if stat.st_size==row['size'] and stat.st_mtime_ns==row['mtime'] and not row['error']:continue
                with path.open('rb') as f:data=f.read(MAX_BYTES+1)
                if len(data)>MAX_BYTES:raise ValueError('File exceeds 5 MB limit')
                after=path.stat()
                if (stat.st_size,stat.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('File changed during read; retry later')
                digest=hashlib.sha256(b'v1:'+path.suffix.lower().encode()+b'\0'+data).hexdigest()
                with db:
                    if not db.execute('SELECT 1 FROM blobs WHERE hash=?',(digest,)).fetchone():
                        text,heads,words,summary,cut=extract(path,data)
                        db.execute('INSERT INTO blobs(hash,text,headings,keywords,summary,truncated) VALUES(?,?,?,?,?,?)',
                                   (digest,text,json.dumps(heads),json.dumps(words),summary,int(cut)))
                        refresh_index(db,digest)
                    else:report['reused']+=1
                    db.execute('UPDATE files SET hash=?,size=?,mtime=?,error=\'\' WHERE path=?',(digest,stat.st_size,stat.st_mtime_ns,str(path)))
                report['changed']+=1
            except Exception as exc:
                with db:db.execute('UPDATE files SET error=? WHERE path=?',(str(exc),str(path)))
                report['warnings'].append({'path':str(path),'error':str(exc)})
        # Old versions no longer referenced by any registered file are not searchable.
        with db:
            db.execute('DELETE FROM file_search WHERE hash NOT IN (SELECT hash FROM files WHERE hash IS NOT NULL)')
            db.execute('DELETE FROM blobs WHERE hash NOT IN (SELECT hash FROM files WHERE hash IS NOT NULL)')
        return report
    finally:db.close()


def search(root,query,limit=5,project=None,archived='all'):
    if not (root/'files.sqlite').exists():return []
    db=connect(root)
    try:
        if setting(db,'enabled')!='on':return []
        terms=re.findall(r'[^\W_]+',query)[:16]
        if not terms:return []
        expr=' OR '.join('"'+t+'"' for t in terms)
        def run(expression):
            return db.execute("SELECT hash,snippet(file_search,1,'[',']',' ... ',32) AS excerpt FROM file_search WHERE file_search MATCH ? ORDER BY bm25(file_search) LIMIT 200",(expression,)).fetchall()
        hits=run(expr);strategy='words'
        if not hits:
            words=[]
            for term in terms:
                vocabulary=[r[0] for r in db.execute('SELECT term FROM file_vocab WHERE length(term) BETWEEN ? AND ? LIMIT 15000',(max(1,len(term)-2),len(term)+2))]
                words.extend(difflib.get_close_matches(term.casefold(),vocabulary,n=2,cutoff=0.78))
            if words:hits=run(' OR '.join('"'+w+'"' for w in words));strategy='fuzzy words'
        found={r['hash'] for r in hits}
        hits=list(hits)
        for row in db.execute("SELECT path,hash FROM files WHERE error='' AND hash IS NOT NULL"):
            if row['hash'] not in found and all(t.casefold() in Path(row['path']).name.casefold() for t in terms):
                hits.append({'hash':row['hash'],'excerpt':'Filename match'});found.add(row['hash'])
        tasks={}
        if (root/'index.sqlite').exists():
            with contextlib.closing(sqlite3.connect((root/'index.sqlite').as_uri()+'?mode=ro',uri=True)) as source:
                source.row_factory=sqlite3.Row;tasks={r['id']:dict(r) for r in source.execute('SELECT id,title,project,archived FROM tasks')}
        result=[]
        for hit in hits:
            paths=[r[0] for r in db.execute("SELECT path FROM files WHERE hash=? AND error=''",(hit['hash'],))]
            linked=[]
            for p in paths:
                for row in db.execute('SELECT task_id FROM links WHERE path=?',(p,)):
                    task=tasks.get(row[0])
                    if task and (not project or project.casefold() in task['project'].casefold()) and (archived=='all' or bool(task['archived'])==(archived=='yes')):
                        linked.append(dict(task,url='codex://threads/'+task['id']))
            if not paths or ((project or archived!='all') and not linked):continue
            result.append({'hash':hit['hash'],'paths':paths,'excerpt':hit['excerpt'],'strategy':strategy,'tasks':list({t['id']:t for t in linked}.values())})
            if len(result)>=limit:break
        return result
    finally:db.close()


def command(root,args):
    db=connect(root)
    try:
        action=args.file_action
        if action in ('enable','disable','ai-on','ai-off'):
            key='ai' if action.startswith('ai-') else 'enabled'
            value='on' if action in ('enable','ai-on') else 'off'
            with db:db.execute('INSERT OR REPLACE INTO config VALUES(?,?)',(key,value))
            return {key:value,'note':'No files are added or sent to a model automatically.'}
        if action=='status':
            return {'enabled':setting(db,'enabled'),'ai_summaries':setting(db,'ai'),
                    'registered_files':db.execute('SELECT count(*) FROM files').fetchone()[0],
                    'unique_contents':db.execute('SELECT count(*) FROM blobs').fetchone()[0],
                    'errors':[dict(x) for x in db.execute("SELECT path,error FROM files WHERE error!=''")]}
        if action=='add':
            if setting(db,'enabled')!='on':raise ValueError('Enable file indexing first: files enable')
            path=Path(args.path).expanduser().resolve()
            if not path.exists():raise ValueError('Selected path does not exist')
            if path.is_dir() and not args.recursive:raise ValueError('Folder selection requires --recursive')
            if args.task:
                with contextlib.closing(sqlite3.connect((root/'index.sqlite').as_uri()+'?mode=ro',uri=True)) as source:
                    if not source.execute('SELECT 1 FROM tasks WHERE id=?',(args.task,)).fetchone():raise ValueError('Unknown task ID; sync the chat archive first')
            paths=list(path.rglob('*')) if path.is_dir() else [path]
            selected=[]
            for p in paths:
                resolved=p.resolve()
                if path.is_dir() and not resolved.is_relative_to(path):continue
                if p.is_file() and p.suffix.lower() in SUPPORTED and not any(part.startswith('.') for part in p.relative_to(path if path.is_dir() else path.parent).parts):selected.append(resolved)
            if len(selected)>200:raise ValueError('Scope exceeds 200 supported files; select a narrower folder')
            if not selected:raise ValueError('No supported files selected')
            with db:
                for p in selected:
                    db.execute('INSERT OR IGNORE INTO files(path) VALUES(?)',(str(p),))
                    if args.task:db.execute('INSERT OR IGNORE INTO links VALUES(?,?)',(str(p),args.task))
            return {'registered':len(selected),'sync':sync(root)}
        if action=='remove':
            path=str(Path(args.path).expanduser().resolve())
            with db:
                db.execute('DELETE FROM links WHERE path=?',(path,));db.execute('DELETE FROM files WHERE path=?',(path,))
                db.execute('DELETE FROM file_search WHERE hash NOT IN (SELECT hash FROM files WHERE hash IS NOT NULL)')
                db.execute('DELETE FROM blobs WHERE hash NOT IN (SELECT hash FROM files WHERE hash IS NOT NULL)')
            return {'removed':path}
        if action=='sync':return sync(root)
        if action in ('show','summary-input','summary-set'):
            row=db.execute('SELECT * FROM blobs WHERE hash=?',(args.digest,)).fetchone()
            if not row:raise ValueError('Unknown content hash')
            if action.startswith('summary-') and setting(db,'ai')!='on':raise ValueError('AI summaries are off; obtain user opt-in before files ai-on')
            if action=='summary-set':
                value=json.loads(Path(args.json_file).read_text(encoding='utf-8'))
                summary=value.get('summary','');keywords=value.get('keywords',[])
                if not isinstance(summary,str) or not 1<=len(summary)<=2000:raise ValueError('Summary must be 1 to 2000 characters')
                if not isinstance(keywords,list) or len(keywords)>20 or any(not isinstance(w,str) or len(w)>80 for w in keywords):raise ValueError('Use at most 20 short keywords')
                with db:
                    db.execute('UPDATE blobs SET ai_summary=? WHERE hash=?',(json.dumps({'summary':summary,'keywords':keywords}),args.digest));refresh_index(db,args.digest)
                return {'saved':args.digest}
            return {'hash':args.digest,'headings':json.loads(row['headings']),'keywords':json.loads(row['keywords']),
                    'extractive_summary':row['summary'],'ai_summary':row['ai_summary'],
                    'text':row['text'][:6000],'excerpt_truncated':len(row['text'])>6000,'extraction_truncated':bool(row['truncated'])}
        raise ValueError('Unknown file action')
    finally:db.close()


def add_parser(sub):
    p=sub.add_parser('files');actions=p.add_subparsers(dest='file_action',required=True)
    for name in ('enable','disable','ai-on','ai-off','status','sync'):actions.add_parser(name)
    p=actions.add_parser('add');p.add_argument('path');p.add_argument('--recursive',action='store_true');p.add_argument('--task')
    p=actions.add_parser('remove');p.add_argument('path')
    for name in ('show','summary-input','summary-set'):
        p=actions.add_parser(name);p.add_argument('digest')
        if name=='summary-set':p.add_argument('json_file')
