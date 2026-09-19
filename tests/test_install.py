import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

path=Path(__file__).resolve().parents[1]/'scripts/install.py'
spec=importlib.util.spec_from_file_location('install',path)
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class InstallTests(unittest.TestCase):
    def test_preserves_other_hooks_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)/"codex space's";home.mkdir()
            original={'hooks':{'Stop':[{'hooks':[{'type':'command','command':'echo other'}]}]},'description':'existing'}
            config=home/'hooks.json';config.write_text(json.dumps(original))
            installer.install(home,True);installer.install(home,True)
            data=json.loads(config.read_text());self.assertEqual(len(data['hooks']['Stop']),2)
            self.assertEqual(data['hooks']['Stop'][0],original['hooks']['Stop'][0])
            self.assertEqual(data['description'],'existing')
            cmd=data['hooks']['Stop'][1]['hooks'][0]['commandWindows']
            self.assertIn("space''s",cmd)
            installer.install(home,remove_hook=True)
            self.assertEqual(json.loads(config.read_text()),original)
            self.assertTrue(list((home/'chat-recall/config-backups').glob('*.json')))

    def test_existing_unmanaged_skill_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp);(home/'skills/codex-recall').mkdir(parents=True)
            with self.assertRaises(ValueError):installer.install(home)

if __name__=='__main__':unittest.main()
