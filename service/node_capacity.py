"""Native, read-only measurement of this node's actual Docker storage usage."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo


def capture(node_path):
    node=json.loads(Path(node_path).read_text());docker=node['docker_service']
    if not docker.get('enabled'):raise ValueError('measure the activated node configuration')
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
        'log_physical_limit_per_container_bytes':30*1024*1024,
        'meaning':'MySQL du allocated blocks; retained log stream excludes driver framing. File/CAS/backup allocation is measured by storage.status.'}
    saved=subprocess.run(command+['exec','-T','core','python','-m','btc_node','request'],
        input=json.dumps({'method':'host.capacity','params':value}),text=True,capture_output=True,timeout=60)
    if saved.returncode or not json.loads(saved.stdout).get('ok'):raise RuntimeError('capacity record failed')
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--node',required=True)
    print(json.dumps(capture(parser.parse_args().node),ensure_ascii=False))
