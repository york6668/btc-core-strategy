#!/usr/bin/env python3
"""Capture a quiescent node for import, without copying global Codex data."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile


def capture(project, destination):
    project, destination = Path(project).resolve(), Path(destination).absolute()
    if destination.exists(): raise FileExistsError(destination)
    names = ('资产配置.md', '操作记录.md', '纠正记录.md')
    facts = {name:(project/name).read_bytes() for name in names}
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.snapshot-',dir=destination.parent))
    try:
        with (project/'.strategy/v4/.writer.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if sys.platform == 'darwin':
                subprocess.run(['/bin/cp','-cR',str(project/'.strategy'),str(stage/'.strategy')],check=True)
            else:
                shutil.copytree(project/'.strategy',stage/'.strategy',symlinks=True)
            for path in (stage/'.strategy').rglob('*'):
                if not path.is_file() or path.is_symlink() or path.suffix not in {'.sqlite','.sqlite3','.db'}: continue
                original = project / path.relative_to(stage)
                temporary = path.with_name(path.name+'.snapshot')
                source = sqlite3.connect(original.as_uri()+'?mode=ro',uri=True)
                target = sqlite3.connect(temporary)
                try:
                    source.backup(target)
                    if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise ValueError('invalid SQLite snapshot')
                finally:
                    source.close();target.close()
                temporary.replace(path)
                for suffix in ('-wal','-shm','-journal'): Path(str(path)+suffix).unlink(missing_ok=True)
            if any((project/name).read_bytes()!=data for name,data in facts.items()):
                raise ValueError('confirmed facts changed during snapshot')
            for name,data in facts.items(): (stage/name).write_bytes(data)
        stage.rename(destination)
        return {'snapshot':str(destination),'fact_version':{name:'sha256:'+hashlib.sha256(data).hexdigest() for name,data in facts.items()}}
    finally:
        if stage.exists():shutil.rmtree(stage)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',required=True);p.add_argument('--destination',required=True)
    a=p.parse_args();print(json.dumps(capture(a.project,a.destination),ensure_ascii=False))
