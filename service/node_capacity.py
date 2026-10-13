"""Measure this node's storage and rotate its explicitly configured native logs."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
from stat import S_ISREG, S_ISLNK
import subprocess
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo


MAX_INODE_KEYS = 65536


def native_logs(entries):
    results=[]
    for entry in entries:
        path=Path(entry['path'])
        if not path.is_absolute() or path.is_symlink():
            raise ValueError('native logs must name explicit regular absolute paths')
        limit=int(entry.get('max_bytes',10*1024*1024)); keep=int(entry.get('keep_files',3))
        if limit<1 or not 1<=keep<=30:raise ValueError('invalid native log retention')
        label=entry['launchd_label']
        state=subprocess.run(['launchctl','print','gui/'+str(os.getuid())+'/'+label],capture_output=True,text=True)
        # launchd opens these files for each invocation. Rotate only while its
        # owner is idle; an active writer must retain the inode it opened.
        idle=state.returncode==0 and any(line.strip()=='state = not running' for line in state.stdout.splitlines())
        rotated=False
        if path.exists() and path.stat().st_size>=limit and idle:
            Path(str(path)+'.'+str(keep)).unlink(missing_ok=True)
            for index in range(keep-1,0,-1):
                old=Path(str(path)+'.'+str(index))
                if old.exists():old.replace(Path(str(path)+'.'+str(index+1)))
            permissions=path.stat().st_mode & 0o777
            path.replace(Path(str(path)+'.1'));path.touch(mode=permissions)
            rotated=True
        paths=[path]+[Path(str(path)+'.'+str(i)) for i in range(1,keep+1)]
        results.append({'path':str(path),'rotated':rotated,'owner_idle':idle,
                        'allocated_bytes':sum(p.stat().st_blocks*512 for p in paths if p.is_file())})
    return results


def allocated(paths):
    seen=set();total=0
    spill=connection=cursor=None
    def unreadable(error):
        raise error
    try:
        for raw in paths:
            root=Path(raw)
            try:root_stat=root.lstat()
            except FileNotFoundError:continue
            # rglob suppresses some traversal errors. An unattended launchd
            # job must report denied access rather than count it as zero.
            files=([root] if S_ISREG(root_stat.st_mode) else
                   (Path(directory)/name for directory,_,names in os.walk(root,onerror=unreadable) for name in names))
            for path in files:
                try:metadata=path.lstat()
                except FileNotFoundError:continue
                if S_ISLNK(metadata.st_mode) or not S_ISREG(metadata.st_mode):continue
                key=(metadata.st_dev,metadata.st_ino)
                if connection is None:
                    if key in seen:continue
                    seen.add(key);total+=metadata.st_blocks*512
                    if len(seen)==MAX_INODE_KEYS:
                        spill=TemporaryDirectory(prefix='btc-host-capacity-')
                        connection=sqlite3.connect(Path(spill.name)/'inodes.sqlite3')
                        connection.execute('PRAGMA journal_mode=OFF')
                        connection.execute('PRAGMA synchronous=OFF')
                        connection.execute('PRAGMA cache_size=-4096')
                        connection.execute('CREATE TABLE inodes(identity TEXT PRIMARY KEY) WITHOUT ROWID')
                        cursor=connection.cursor()
                        cursor.executemany('INSERT INTO inodes VALUES (?)',
                            ((str(dev)+':'+str(ino),) for dev,ino in seen))
                        seen.clear()
                elif cursor.execute('INSERT OR IGNORE INTO inodes VALUES (?)',
                                    (str(key[0])+':'+str(key[1]),)).rowcount:
                    total+=metadata.st_blocks*512
    finally:
        if connection is not None:connection.close()
        if spill is not None:spill.cleanup()
    return total


def capture(node_path):
    node=json.loads(Path(node_path).read_text());docker=node['docker_service']
    if not docker.get('enabled'):raise ValueError('measure the activated node configuration')
    native=native_logs(node.get('native_logs',[]))
    command=[docker['binary'],'compose','-p',docker['project'],'--env-file',docker['env_file'],'-f',docker['compose_file']]
    for path in docker.get('override_files',[]):command+=['-f',path]
    mysql=subprocess.check_output(command+['ps','-q','mysql'],text=True,timeout=30).strip()
    if not mysql:raise ValueError('MySQL container is not available')
    result=subprocess.check_output([docker['binary'],'exec',mysql,'du','-sk','/var/lib/mysql'],text=True,timeout=60)
    size=int(result.split()[0])*1024
    containers=subprocess.check_output(command+['ps','-q'],text=True,timeout=30).split()
    retained=0
    for container in containers:
        # Docker's retained stream is measured separately from allocated disk
        # blocks; it excludes json-file framing and is explicitly labelled.
        logs=subprocess.run([docker['binary'],'logs',container],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60,check=True)
        retained+=len(logs.stdout)
    value={'day':datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat(),
        'measured_at':datetime.now().astimezone().isoformat(),'compose_project':docker['project'],
        'mysql_allocated_bytes':size,'retained_log_stream_bytes':retained,
        'native_logs':native,'native_log_allocated_bytes':sum(r['allocated_bytes'] for r in native),
        'node_extra_allocated_bytes':allocated(node.get('capacity_extra_paths',[])),
        'log_physical_limit_per_container_bytes':30*1024*1024,
        'meaning':'MySQL du allocated blocks; retained log stream excludes driver framing. Native logs and explicit node extras are separate; storage.status reports the timestamped last exact File/CAS/backup allocation audit, which may predate today.'}
    saved=subprocess.run(command+['exec','-T','core','python','-m','btc_node','request'],
        input=json.dumps({'method':'host.capacity','params':value}),text=True,capture_output=True,timeout=60)
    if saved.returncode or not json.loads(saved.stdout).get('ok'):raise RuntimeError('capacity record failed')
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--node',required=True)
    print(json.dumps(capture(parser.parse_args().node),ensure_ascii=False))
