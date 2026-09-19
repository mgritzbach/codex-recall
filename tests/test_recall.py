import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'skills/codex-recall/scripts/recall.py'
spec=importlib.util.spec_from_file_location('recall',SCRIPT)
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
ID='11111111-1111-1111-1111-111111111111'

def event(kind,text,key,phase=None):
    item={'type':kind,'id':key,'content':[{'type':'Text','text':text}]}
    if phase: item['phase']=phase
    return {'timestamp':'2026-09-19T12:00:00Z','type':'event_msg','payload':{'type':'item_completed','item':item}}

class RecallTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.home=self.base/'codex';self.root=self.base/'archive'
        self.path=self.home/'sessions'/f'rollout-{ID}.jsonl';self.path.parent.mkdir(parents=True)
        self.write([{'type':'session_meta','payload':{'id':ID,'cwd':'/work/example','source':'vscode'}},
                    event('UserMessage','Find the authentication problem','u1'),
                    event('AgentMessage','Use separate refresh tokens.','a1','final_answer')])

    def tearDown(self): self.tmp.cleanup()
    def write(self,rows):
        self.path.write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8')
    def append(self,row):
        with self.path.open('a',encoding='utf-8') as f:f.write(json.dumps(row)+'\n')
    def run_sync(self):return r.sync(self.home,self.root)
    def read(self):return (self.root/'tasks'/f'{ID}.md').read_text(encoding='utf-8')

    def test_requests_and_all_visible_answers(self):
        self.append(event('AgentMessage','PROGRESS_SECRET','c1','commentary'))
        self.append({'type':'response_item','payload':{'type':'message','role':'developer','content':[{'type':'input_text','text':'SYSTEM_SECRET'}]}})
        self.append({'type':'response_item','payload':{'type':'function_call_output','output':'TOOL_SECRET'}})
        self.append({'type':'response_item','payload':{'type':'reasoning','summary':'REASONING_SECRET'}})
        self.run_sync();text=self.read()
        self.assertIn('PROGRESS_SECRET',text)
        for x in ['SYSTEM_SECRET','TOOL_SECRET','REASONING_SECRET']:self.assertNotIn(x,text)
        self.assertIn('Saturday',text);self.assertIn('2026-09-19',text)
        self.assertIn('Use separate refresh tokens.',text)

    def test_noop_does_not_reread_or_rewrite(self):
        self.run_sync();p=self.root/'tasks'/f'{ID}.md';mtime=p.stat().st_mtime_ns
        report=self.run_sync();self.assertEqual(report['bytes_read'],0);self.assertEqual(report['files_changed'],0)
        self.assertEqual(p.stat().st_mtime_ns,mtime)

    def test_append_is_incremental(self):
        self.run_sync();old=self.path.stat().st_size
        self.append(event('UserMessage','What about travel?','u2'))
        report=self.run_sync();self.assertEqual(report['bytes_read'],self.path.stat().st_size-old)
        self.assertEqual(report['messages'],3);self.assertEqual(self.read().count('What about travel?'),1)

    def test_incomplete_last_record(self):
        self.run_sync();line=json.dumps(event('UserMessage','Split record','u2'))
        with self.path.open('a',encoding='utf-8') as f:f.write(line[:30])
        self.run_sync();self.assertNotIn('Split record',self.read())
        with self.path.open('a',encoding='utf-8') as f:f.write(line[30:]+'\n')
        self.run_sync();self.assertEqual(self.read().count('Split record'),1)

    def test_duplicate_item_is_not_duplicated(self):
        self.append(event('UserMessage','Find the authentication problem','u1'))
        self.run_sync();self.assertEqual(r.status(self.root)['messages'],2)

    def test_truncation_rebuild(self):
        self.run_sync();self.write([{'type':'session_meta','payload':{'id':ID}},event('UserMessage','Replacement','new')])
        self.run_sync();self.assertNotIn('separate refresh',self.read());self.assertIn('Replacement',self.read())

    def test_metadata_rename_and_archive_move(self):
        db=sqlite3.connect(self.home/'state_5.sqlite')
        db.executescript('CREATE TABLE projects(id TEXT,name TEXT); CREATE TABLE threads(id TEXT,title TEXT,cwd TEXT,archived INTEGER,rollout_path TEXT,source TEXT,project_id TEXT);')
        db.execute('INSERT INTO projects VALUES(?,?)',('p','Example Project'))
        db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?)',(ID,'Original','/work',0,str(self.path),'vscode','p'));db.commit()
        self.run_sync();db.execute('UPDATE threads SET title=?,archived=1',('Renamed',));db.commit();db.close()
        dest=self.home/'archived_sessions'/self.path.name;dest.parent.mkdir();self.path.rename(dest)
        report=self.run_sync();self.assertEqual(report['bytes_read'],0)
        self.assertIn('# Renamed',self.read());self.assertIn('Example Project',self.read());self.assertIn('Archived: yes',self.read())
        self.assertEqual(len(r.search(self.root,'refresh',archived='yes')['matches']),1)

    def test_fuzzy_search_and_links(self):
        self.run_sync();result=r.search(self.root,'authentcation')
        self.assertEqual(result['matches'][0]['url'],'codex://threads/'+ID)
        self.assertEqual(result['strategy'],'fuzzy words')

    def test_query_operators_are_literal(self):
        self.run_sync();result=r.search(self.root,'" OR * : NOT refresh')
        self.assertIsInstance(result['matches'],list)

    def test_legacy_events(self):
        self.write([{'type':'session_meta','payload':{'id':ID}},
                    {'type':'event_msg','payload':{'type':'user_message','message':'Legacy question'}},
                    {'type':'event_msg','payload':{'type':'agent_message','message':'Legacy answer'}}])
        self.run_sync();self.assertIn('Legacy answer',self.read())

    def test_response_fallback_warns_and_excludes_known_context(self):
        def response(role,text):return {'type':'response_item','payload':{'type':'message','role':role,'content':[{'type':'input_text','text':text}]}}
        self.write([{'type':'session_meta','payload':{'id':ID}},response('user','<environment_context>internal</environment_context>'),response('user','Real request'),response('assistant','Real answer')])
        report=self.run_sync();self.assertTrue(report['warnings']);self.assertNotIn('internal',self.read())
        self.assertIn('Real answer',self.read());self.assertTrue(self.run_sync()['warnings'])

    def test_subagents_excluded(self):
        self.write([{'type':'session_meta','payload':{'id':ID,'source':{'subagent':{'thread_spawn':{}}}}},event('UserMessage','Subagent instruction','u')])
        self.assertEqual(self.run_sync()['tasks'],0)

    def test_invalid_json_never_advances_past_corruption(self):
        self.run_sync()
        with self.path.open('a') as f:f.write('{bad}\n')
        self.append(event('UserMessage','Later text','u2'))
        result=self.run_sync();self.assertTrue(result['warnings']);self.assertNotIn('Later text',self.read())

    def test_missing_markdown_is_repaired(self):
        self.run_sync();(self.root/'tasks'/f'{ID}.md').unlink();self.run_sync()
        self.assertIn('refresh tokens',self.read())

    def test_context_is_bounded(self):
        self.append(event('AgentMessage','longword '*2000,'a2','final_answer'));self.run_sync()
        hit=r.search(self.root,'longword')['matches'][0];answer=r.context(self.root,ID,hit['message_id'],0)
        self.assertEqual(len(answer['messages'][0]['text']),4000);self.assertTrue(answer['messages'][0]['truncated'])

    def test_end_day_no_model(self):
        self.run_sync();result=r.day_report(self.root,'2026-09-19')
        self.assertEqual(result['tasks'],1);self.assertNotIn('refresh tokens',Path(result['file']).read_text())

    def test_hook_silent_success_and_advisory_failure(self):
        out=io.StringIO()
        with contextlib.redirect_stdout(out),unittest.mock.patch('sys.stdin',io.StringIO(json.dumps({'transcript_path':str(self.path)}))):
            code=r.main(['--home',str(self.home),'--archive',str(self.root),'hook'])
        self.assertEqual(code,0);self.assertEqual(json.loads(out.getvalue()),{})
        out=io.StringIO()
        with contextlib.redirect_stdout(out),unittest.mock.patch('sys.stdin',io.StringIO('{}')):
            code=r.main(['--home',str(self.home),'--archive',str(self.root),'hook'])
        self.assertEqual(code,0);self.assertIn('systemMessage',json.loads(out.getvalue()))

    def test_external_hook_path_rejected(self):
        with self.assertRaises(ValueError):r.sync(self.home,self.root,self.base/'elsewhere.jsonl')

    def test_current_title_and_canonical_rollout(self):
        db=sqlite3.connect(self.home/'state_5.sqlite')
        db.executescript('CREATE TABLE threads(id TEXT,title TEXT,name TEXT,cwd TEXT,archived INTEGER,rollout_path TEXT,source TEXT);')
        db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?)',(ID,'Old prompt','Visible task name','/work',0,str(self.path),'vscode'));db.commit();db.close()
        copy=self.path.with_name('duplicate-'+ID+'.jsonl');copy.write_bytes(self.path.read_bytes())
        self.run_sync();self.assertIn('# Visible task name',self.read())
        self.assertEqual(self.run_sync()['bytes_read'],0)

    def test_desktop_project_assignment(self):
        db=sqlite3.connect(self.home/'state_5.sqlite')
        db.executescript('CREATE TABLE threads(id TEXT,title TEXT,cwd TEXT,archived INTEGER,rollout_path TEXT,source TEXT);')
        db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?)',(ID,'Title','/work',0,str(self.path),'vscode'));db.commit();db.close()
        (self.home/'.codex-global-state.json').write_text(json.dumps({'local-projects':{'p':{'name':'Named project'}},'thread-project-assignments':{ID:{'projectId':'p'}}}))
        self.run_sync();self.assertIn('Project: Named project',self.read())

    def test_export_failure_is_repaired_after_committed_checkpoint(self):
        with unittest.mock.patch.object(r,'atomic_text',side_effect=OSError('Simulated full disk')):
            with self.assertRaises(OSError):self.run_sync()
        self.assertEqual(r.status(self.root)['pending_exports'],1)
        self.run_sync();self.assertEqual(r.status(self.root)['pending_exports'],0)
        self.assertIn('refresh tokens',self.read())

    def test_two_processes_do_not_duplicate_messages(self):
        import subprocess,sys
        args=[sys.executable,str(SCRIPT),'--home',str(self.home),'--archive',str(self.root),'sync']
        processes=[subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
        for p in processes:
            out,err=p.communicate(timeout=30);self.assertEqual(p.returncode,0,err)
        self.assertEqual(r.status(self.root)['messages'],2)

    def test_empty_source_is_not_reported_as_success(self):
        self.path.unlink();self.assertTrue(self.run_sync()['warnings'])

from unittest import mock
if __name__=='__main__':unittest.main()
