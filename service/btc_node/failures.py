"""Explicit fault closure; file age and a later unrelated run are not recovery."""
from pathlib import Path

from .codec import Conflict, millis


def is_receipt(path, value):
    name = Path(path).name
    return (name == 'failure-receipt.json' or name.endswith(('-failure.json', '-error.json'))) and (
        isinstance(value, dict) and (value.get('schema') == 'BTC_RUN_FAILURE_RECEIPT_V1' or
                                    value.get('ok') is False and bool(value.get('error'))))


def get(db, *, document_key):
    row = db.one('SELECT namespace,path,digest,retention_class,expires_ms FROM documents WHERE document_key=%s', (document_key,))
    saved = db.metadata('failure:' + document_key)
    if not row and not saved:
        raise ValueError('failure document does not exist')
    if row and not is_receipt(row['path'], db.value(row['digest'])):
        raise ValueError('document is not a recognized fault receipt')
    same = saved and (not row or row['digest'] == saved['digest'])
    return {'document_key':document_key, 'version':saved['version'] if saved else 0,
            'state':'CLOSED' if same else 'OPEN', 'document':row,
            'resolution':saved if same else None, 'prior_resolution':saved if saved and not same else None}


def listing(db, *, after=None, limit=100):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('failure page limit must be 1..500')
    keys = {r['document_key'] for r in db.rows(
        "SELECT document_key FROM documents WHERE kind='FILE' AND "
        "(path LIKE '%%failure-receipt.json' OR path LIKE '%%-failure.json' OR path LIKE '%%-error.json')")}
    keys.update(r['name'].split(':',1)[1] for r in db.rows("SELECT name FROM node_metadata WHERE name LIKE 'failure:%%'"))
    selected = sorted(key for key in keys if after is None or key > after)
    items = []
    for key in selected[:limit]:
        try: items.append(get(db, document_key=key))
        except ValueError: continue  # similarly named ordinary documents
    return {'failures':items, 'next':selected[limit-1] if len(selected)>limit else None}


def resolve(db, *, document_key, expected_digest, expected_version, resolution_operation_id, reason):
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError('expected_version must be a nonnegative integer')
    current = get(db, document_key=document_key)
    row = current['document']
    if not row or row['digest'] != expected_digest or current['version'] != expected_version:
        raise Conflict('failure document/version changed; reread before closing')
    if current['state'] == 'CLOSED':
        raise Conflict('failure already closed; do not move its retention window')
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('describe the actual fault recovery')
    operation = db.one('SELECT status,request_digest,result_digest FROM operations WHERE operation_id=%s', (resolution_operation_id,))
    if not operation or operation['status'] != 'COMMITTED' or not operation['result_digest']:
        raise ValueError('recovery operation must have a committed result')
    request, result = db.value(operation['request_digest']), db.value(operation['result_digest'])
    if not (request.get('argv') or request.get('method') == 'acceptance.repair'):
        raise ValueError('bind an actual core recovery or evidence-bound engineering repair')
    if not isinstance(result, dict) or result.get('returncode', 0) != 0 or result.get('ok') is False:
        raise ValueError('failed operation is not recovery evidence')
    closed_ms = millis()
    saved = {'document_key':document_key, 'namespace':row['namespace'], 'path':row['path'],
             'digest':expected_digest, 'version':expected_version+1, 'closed_ms':closed_ms,
             'reason':reason, 'resolution_operation_id':resolution_operation_id,
             'resolution_digest':operation['result_digest'], 'prior_resolution':current['prior_resolution']}
    db.metadata('failure:' + document_key, saved, write=True)
    db.execute("UPDATE documents SET retention_class='FAILURE_CLOSED',expires_ms=%s WHERE document_key=%s",
               (closed_ms+14*86400000, document_key))
    db.execute('DELETE FROM document_recycling WHERE document_key=%s', (document_key,))
    return get(db, document_key=document_key)
