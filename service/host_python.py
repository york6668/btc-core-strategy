"""Native interpreter adapter, including immutable releases predating Docker.

The transport belongs to the deployed node. The requested strategy script and
its pinned methods are executed unchanged in the container.
"""
import json
import os
from pathlib import Path
import sys


def main():
    args=sys.argv[1:]
    node_path=os.environ.get('BTC_NODE_CONFIG')
    # btc-core invokes release.py with this argument before enter has emitted
    # the pinned environment. The adapter must also work at that first entry.
    if not node_path and '--node-config' in args:
        position=args.index('--node-config')+1
        if position<len(args):node_path=args[position]
    if not node_path:raise ValueError('BTC_NODE_CONFIG is required by the native interpreter adapter')
    node=json.loads(Path(node_path).read_text())
    native=node['native_python']
    index=0
    while index<len(args) and args[index] in ('-B','-u'):index+=1
    if index<len(args):
        script=Path(args[index])
        if script.name=='strategy_v4.py' and script.is_file():
            release=script.resolve().parents[3]
            if (release/'RELEASE.json').is_file():
                allowed=Path(node['release_root']).resolve()/'releases'
                if release.parent!=allowed:raise ValueError('strategy belongs to another release store')
                source=Path(__file__).resolve().parents[1]/'skill/btc-core-strategy/scripts'
                sys.path.insert(0,str(source))
                from node_bridge import forward_if_enabled
                forward_if_enabled(args[index+1:],requested_release=release)
    os.execv(native,[native,*args])


if __name__=='__main__':main()
