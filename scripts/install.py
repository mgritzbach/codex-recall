#!/usr/bin/env python3
"""Install the standalone skill and optionally configure an end-of-turn hook."""
import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import sys

REPO=Path(__file__).resolve().parents[1]
SOURCE=REPO/'skills/codex-recall'

def install(home, enable_hook=False, remove_hook=False):
    home=Path(home).expanduser().resolve()
    target=home/'skills/codex-recall'
    marker=target/'.codex-recall-managed'
    if target.exists() and not marker.exists():
        raise ValueError('Skill directory already exists and is not managed by this installer')
    if not remove_hook:
        target.mkdir(parents=True,exist_ok=True)
        shutil.copytree(SOURCE,target,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        marker.write_text('codex-recall\n',encoding='utf-8')
        (target/'installation.json').write_text(json.dumps({'source_repository':str(REPO)},indent=2)+'\n',encoding='utf-8')
    result={'skill':str(target),'hook_configured':False,'trust_required':False}
    if enable_hook or remove_hook:
        script=target/'scripts/recall.py'
        spec=importlib.util.spec_from_file_location('recall',SOURCE/'scripts/recall.py')
        recall=importlib.util.module_from_spec(spec);spec.loader.exec_module(recall)
        config=home/'hooks.json'
        with recall.writer_lock(home/'chat-recall'):
            current=json.loads(config.read_text(encoding='utf-8-sig')) if config.exists() else {}
            groups=current.setdefault('hooks',{}).setdefault('Stop',[])
            kept=[]
            for group in groups:
                handlers=group.get('hooks',[])
                remaining=[h for h in handlers if h.get('statusMessage')!='Updating Codex Recall archive']
                if len(remaining)==len(handlers): kept.append(group)
                elif remaining: kept.append(dict(group,hooks=remaining))
            groups[:]=kept
            if enable_hook:
                args=[sys.executable,str(script),'--home',str(home),'hook']
                ps=lambda v:"'"+v.replace("'","''")+"'"
                groups.append({'hooks':[{'type':'command','command':shlex.join(args),
                              'commandWindows':'& '+' '.join(ps(v) for v in args),
                              'timeout':120,'async':True,'statusMessage':'Updating Codex Recall archive'}]})
            if config.exists():
                stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
                backup=home/'chat-recall'/'config-backups'/('hooks-'+stamp+'.json')
                backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(config,backup)
            recall.atomic_text(config,json.dumps(current,indent=2)+'\n')
        result.update(hook_configured=enable_hook,trust_required=enable_hook,hook_file=str(config))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--home',type=Path,default=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))))
    g=p.add_mutually_exclusive_group();g.add_argument('--enable-hook',action='store_true');g.add_argument('--remove-hook',action='store_true')
    a=p.parse_args()
    try:print(json.dumps(install(a.home,a.enable_hook,a.remove_hook),indent=2))
    except Exception as e:print(str(e),file=sys.stderr);sys.exit(1)
