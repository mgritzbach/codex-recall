import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as Args
import unittest
from unittest.mock import patch
import zipfile

SCRIPT=Path(__file__).resolve().parents[1]/'skills/codex-recall/scripts/file_index.py'
spec=importlib.util.spec_from_file_location('file_index',SCRIPT)
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)

class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.root=self.base/'archive'
        self.path=self.base/'notes.md';self.path.write_text('# Telescope plan\n\nBuild a telescope observatory.\n',encoding='utf-8')
    def tearDown(self):self.temp.cleanup()
    def cmd(self,action,**kwargs):return f.command(self.root,Args(file_action=action,**kwargs))
    def add(self,path=None):return self.cmd('add',path=str(path or self.path),recursive=False,task=None)
    def digest(self):return f.search(self.root,'telescope')[0]['hash']
    def test_off_by_default_and_explicit_scope(self):
        self.assertEqual(self.cmd('status')['enabled'],'off')
        with self.assertRaises(ValueError):self.add()
        self.cmd('enable');self.assertEqual(self.cmd('sync')['changed'],0)
        self.add();self.assertEqual(len(f.search(self.root,'telescope')),1)
        self.cmd('disable');self.assertEqual(f.search(self.root,'telescope'),[])
    def test_text_headings_keywords_and_extract(self):
        self.cmd('enable');self.add();out=self.cmd('show',digest=self.digest())
        self.assertEqual(out['headings'],['Telescope plan']);self.assertIn('telescope',out['keywords'])
        self.assertIn('observatory',out['extractive_summary']);self.assertEqual(out['ai_summary'],'')
    def test_hash_reuse_and_changed_file(self):
        self.cmd('enable');self.add();copy=self.base/'copy.md';copy.write_bytes(self.path.read_bytes())
        self.assertEqual(self.add(copy)['sync']['reused'],1)
        self.assertEqual(self.cmd('status')['unique_contents'],1)
        with patch.object(f,'extract',side_effect=AssertionError('No repeat extraction')):self.assertEqual(self.cmd('sync')['changed'],0)
        self.path.write_text('# Submarine\nOcean exploration.',encoding='utf-8');self.cmd('sync')
        self.assertEqual(self.cmd('status')['unique_contents'],2)
    def test_ai_opt_in_cache_and_invalidation(self):
        self.cmd('enable');self.add();digest=self.digest()
        with self.assertRaises(ValueError):self.cmd('summary-input',digest=digest)
        self.cmd('ai-on');self.assertIn('text',self.cmd('summary-input',digest=digest))
        p=self.base/'summary.json';p.write_text(json.dumps({'summary':'A plan for astronomy.','keywords':['stargazing']}))
        self.cmd('summary-set',digest=digest,json_file=str(p));self.assertTrue(f.search(self.root,'stargazing'))
        self.path.write_text('Changed text for tomorrow.',encoding='utf-8');self.cmd('sync')
        self.assertFalse(f.search(self.root,'stargazing'))
    def test_ai_size_validation(self):
        self.cmd('enable');self.add();self.cmd('ai-on');p=self.base/'summary.json';p.write_text(json.dumps({'summary':'x'*2001}))
        with self.assertRaises(ValueError):self.cmd('summary-set',digest=self.digest(),json_file=str(p))
    def test_remove_purges_unreferenced_content(self):
        self.cmd('enable');self.add();self.cmd('remove',path=str(self.path))
        self.assertEqual(self.cmd('status')['unique_contents'],0);self.assertEqual(f.search(self.root,'telescope'),[])
    def test_deleted_files_are_not_returned_as_current(self):
        self.cmd('enable');self.add();self.path.unlink();self.assertTrue(self.cmd('sync')['warnings'])
        self.assertEqual(f.search(self.root,'telescope'),[])
    def test_folder_requires_flag_and_skips_hidden(self):
        folder=self.base/'scope';folder.mkdir();(folder/'file.md').write_text('hello');(folder/'.secret.md').write_text('hidden')
        self.cmd('enable')
        with self.assertRaises(ValueError):self.cmd('add',path=str(folder),recursive=False,task=None)
        self.assertEqual(self.cmd('add',path=str(folder),recursive=True,task=None)['registered'],1)
    def test_html_does_not_index_script(self):
        text,heads,*_=f.extract(Path('x.html'),b'<h1>Heading</h1><script>SECRET</script><p>Useful text</p>')
        self.assertNotIn('SECRET',text);self.assertEqual(heads,['Heading'])
    def test_docx_text_and_heading(self):
        p=self.base/'x.docx'
        with zipfile.ZipFile(p,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="urn:w"><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Overview</w:t></w:r></w:p></w:document>')
        text,heads,*_=f.extract(p,p.read_bytes());self.assertEqual(text,'Overview');self.assertEqual(heads,['Overview'])
    def test_large_text_has_explicit_truncation(self):
        out=f.extract(Path('x.txt'),b'a'*(f.MAX_TEXT+1));self.assertTrue(out[-1]);self.assertEqual(len(out[0]),f.MAX_TEXT)
    def test_file_typo_and_filename_search(self):
        self.cmd('enable');self.add()
        self.assertTrue(f.search(self.root,'telescoep'));self.assertTrue(f.search(self.root,'notes'))
    def test_task_link_and_project_filter(self):
        import sqlite3
        self.root.mkdir();db=sqlite3.connect(self.root/'index.sqlite')
        db.execute('CREATE TABLE tasks(id TEXT,title TEXT,project TEXT,archived INTEGER)')
        db.execute('INSERT INTO tasks VALUES(?,?,?,?)',('test-task','Test task','Astronomy',1));db.commit();db.close()
        self.cmd('enable');self.cmd('add',path=str(self.path),recursive=False,task='test-task')
        result=f.search(self.root,'telescope',project='Astronomy',archived='yes')
        self.assertEqual(result[0]['tasks'][0]['url'],'codex://threads/test-task')
        self.assertFalse(f.search(self.root,'telescope',project='Other'))
    def test_format_part_of_cache_identity(self):
        self.cmd('enable');self.add();p=self.base/'copy.txt';p.write_bytes(self.path.read_bytes());self.add(p)
        self.assertEqual(self.cmd('status')['unique_contents'],2)
    def test_oversized_file_reports_error(self):
        self.cmd('enable');self.path.write_bytes(b'a'*(f.MAX_BYTES+1));self.assertTrue(self.add()['sync']['warnings'])
        self.assertEqual(f.search(self.root,'telescope'),[])

if __name__=='__main__':unittest.main()
