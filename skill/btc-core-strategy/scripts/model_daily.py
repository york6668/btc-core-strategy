#!/usr/bin/env python3
"""BTC-wide Tokyo-day deep-model admission through the existing node authority."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


class Blocked(RuntimeError):
    pass


def sha256(path):
    source = Path(path)
    if not source.is_file() or source.is_symlink() or not source.stat().st_size:
        raise Blocked('nonempty regular proof file required')
    return 'sha256:' + hashlib.sha256(source.read_bytes()).hexdigest()


def request(method, params=None, operation_id=None):
    """Reads within core.execute use a separate DB connection; writes use the service."""
    params = params or {}
    if os.environ.get('BTC_IN_CONTAINER') == '1' and method in ('model.deep.status', 'model.defect.check'):
        from btc_node.database import Database
        from btc_node.deep_gate import check_scope, status
        settings = json.loads(Path(os.environ['BTC_SERVICE_CONFIG']).read_text())
        db = Database(settings)
        try:
            with db.transaction():
                return status(db) if method == 'model.deep.status' else check_scope(db, **params)
        finally:
            db.close()
    import node_config
    docker = node_config.load().get('docker_service') or {}
    if not docker.get('enabled'):
        raise Blocked('shared BTC node service is unavailable; no local fallback')
    command = [docker['binary'], 'compose', '-p', docker['project'],
               '--env-file', docker['env_file'], '-f', docker['compose_file']]
    for path in docker.get('override_files', []):
        command += ['-f', path]
    command += ['exec', '-T', 'core', 'python', '-m', 'btc_node', 'request']
    payload = {'method': method, 'params': params}
    if operation_id:
        payload['operation_id'] = operation_id
    try:
        process = subprocess.run(command, input=json.dumps(payload), text=True,
                                 capture_output=True, check=False)
        response = json.loads(process.stdout)
    except (OSError, ValueError, KeyError) as exc:
        raise Blocked('shared BTC node response unknown; inspect original operation') from exc
    if not response.get('ok'):
        raise Blocked(response.get('message') or 'shared BTC node rejected operation')
    return response['result']


def status():
    return request('model.deep.status')


def claim(issue_id, scope, phase, input_digest, model='gpt-6-astra', claim_id=None):
    claim_id = claim_id or 'btc-deep-' + uuid.uuid4().hex
    result = request('model.deep.claim', dict(claim_id=claim_id, issue_id=issue_id,
                     scope=scope, phase=phase, input_digest=input_digest, model=model),
                     'deep-claim-' + claim_id)
    if result['action'] != 'CLAIMED_NEW':
        raise Blocked('daily slot reused or claim response replayed; do not launch')
    return result


def finish(claim_id, outcome, result_ref=None, failure_kind=None, failure_ref=None):
    params = {'claim_id': claim_id, 'outcome': outcome}
    if result_ref is not None: params['result_ref'] = result_ref
    if failure_kind is not None: params['failure_kind'] = failure_kind
    if failure_ref is not None: params['failure_ref'] = failure_ref
    return request('model.deep.finish', params, 'deep-finish-' + claim_id)


def require_success(issue_id, scope, phase, claim_id, rollout_path):
    if not claim_id:
        raise Blocked('completed original daily deep claim required')
    current = status()
    if any(v['state'] == 'OPEN' and (v['scope'] == scope or scope.startswith(v['scope'] + '/'))
           for v in current['faults'].values()):
        raise Blocked('open defect blocks affected research registration')
    record = next((v for v in current['days'].values() if v['claim_id'] == claim_id), None)
    if (not record or record['state'] != 'COMPLETE' or not record.get('result_ref')
            or record['issue_id'] != issue_id or record['scope'] != scope
            or record['phase'] != phase or record['model'] != 'gpt-6-astra'
            or record['result_ref'] != sha256(rollout_path)):
        raise Blocked('completed original Astra claim for issue and phase required')
    return record


def launch_native(issue_id, scope, phase, input_digest, prompt, codex_binary, output,
                  *, resume=None, cwd=None, runner=subprocess.run):
    source = Path(prompt)
    if not source.is_file() or source.is_symlink() or not source.stat().st_size:
        raise Blocked('frozen prompt file required')
    binary = Path(codex_binary)
    if binary.name != 'codex' or not binary.is_file():
        raise Blocked('native Codex binary required')
    destination = Path(output)
    if destination.exists() or destination.is_symlink():
        raise Blocked('output must be new; preserve existing proof')
    admitted = claim(issue_id, scope, phase, input_digest)
    claim_id = admitted['claim']['claim_id']
    destination.parent.mkdir(parents=True, exist_ok=True)
    events = destination.with_suffix(destination.suffix + '.events.jsonl')
    errors = destination.with_suffix(destination.suffix + '.stderr.log')
    command = [str(binary), 'exec']
    if resume: command.append('resume')
    command += ['--skip-git-repo-check', '--json', '-m', 'gpt-6-astra',
                '-c', 'model_reasoning_effort="max"', '-c', 'approval_policy="never"',
                '-o', str(destination)]
    if resume: command.append(resume)
    command.append('-')
    try:
        with source.open('rb') as stdin, events.open('wb') as stdout, errors.open('wb') as stderr:
            done = runner(command, stdin=stdin, stdout=stdout, stderr=stderr,
                          cwd=str(cwd) if cwd else None, check=False)
        completed = False
        with events.open('rb') as stream:
            for line in stream:
                try: completed |= json.loads(line).get('type') == 'turn.completed'
                except ValueError: pass
        if done.returncode == 0 and completed and destination.is_file() and destination.stat().st_size:
            settled = finish(claim_id, 'COMPLETE', result_ref=sha256(events))
            return {'admission': admitted, 'settlement': settled, 'output': str(destination)}
        if not errors.stat().st_size:
            errors.write_text('native call failed or incomplete; exit=' + str(done.returncode))
        settled = finish(claim_id, 'FAILED', failure_kind='UNKNOWN', failure_ref=str(errors))
        return {'admission': admitted, 'settlement': settled, 'output': str(destination)}
    except BaseException:
        # Process kill or unknown result remains CLAIMED until reconciled.
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    p = sub.add_parser('claim')
    for name in ('issue-id','scope','phase','input-digest','claim-id'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--model', default='gpt-6-astra')
    p = sub.add_parser('finish')
    p.add_argument('--claim-id', required=True)
    p.add_argument('--outcome', required=True, choices=('COMPLETE','FAILED','UNKNOWN','CANCELED'))
    p.add_argument('--result-ref'); p.add_argument('--failure-kind'); p.add_argument('--failure-ref')
    p = sub.add_parser('launch-native')
    for name in ('issue-id','scope','phase','input-digest','prompt','codex-binary','output'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--resume'); p.add_argument('--cwd')
    args = parser.parse_args(argv)
    try:
        if args.command == 'status': result = status()
        elif args.command == 'claim':
            result = claim(args.issue_id,args.scope,args.phase,args.input_digest,args.model,args.claim_id)
        elif args.command == 'finish':
            result = finish(args.claim_id,args.outcome,args.result_ref,args.failure_kind,args.failure_ref)
        else:
            result = launch_native(args.issue_id,args.scope,args.phase,args.input_digest,
                                   args.prompt,args.codex_binary,args.output,resume=args.resume,cwd=args.cwd)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (Blocked, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'blocked': type(exc).__name__, 'reason': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
