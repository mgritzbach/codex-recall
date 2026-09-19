"""Check release structure and keep private runtime files out of the package."""
import ast
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1]
required=['.codex-plugin/plugin.json','skills/codex-recall/SKILL.md',
          'skills/codex-recall/agents/openai.yaml','README.md','LICENSE',
          'SECURITY.md','CONTRIBUTING.md','CHANGELOG.md','docs/scheduling.md',
          'docs/architecture.md','.github/workflows/test.yml']
for path in required:
    assert (root/path).is_file(),path
manifest=json.loads((root/required[0]).read_text(encoding='utf-8'))
assert manifest['name']=='codex-recall'
assert (root/manifest['skills']).is_dir()
for path in root.rglob('*'):
    if not path.is_file() or '.git' in path.parts or '__pycache__' in path.parts:continue
    assert not path.name.endswith(('.jsonl','.sqlite','.sqlite-wal','.sqlite-shm')),path
    if path.suffix in ('.md','.py','.json','.yaml','.yml','.ps1'):
        text=path.read_text(encoding='utf-8')
        assert chr(0x2014) not in text, f'Forbidden punctuation in {path}'
        assert str(Path.home()).replace('\\','/') not in text.replace('\\','/'),path
        assert ('[TO'+'DO:') not in text,path
        if path.suffix=='.py':ast.parse(text)
print('Package structure, Python syntax, and private-data exclusions passed.')
