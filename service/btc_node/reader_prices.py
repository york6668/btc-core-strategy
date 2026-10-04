"""Project validated receipt history independently of model snapshot selection."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys

from .codec import Conflict, fingerprint, identity, millis
from .collector_series import ingest


def project(db, *, limit=32):
    root = db.settings.get('reader_data')
    if not root:
        return {'status': 'NOT_CONFIGURED', 'processed': 0, 'remaining': 0}
    root = Path(root)
    config, sidecar = root/'messages.sqlite3', root/'collector_evidence.sqlite3'
    if not config.is_file() or not sidecar.is_file():
        return {'status': 'NO_RECEIPTS', 'processed': 0, 'remaining': 0}
    node = db.settings.get('container_node_config')
    directory = (Path(json.loads(Path(node).read_text())['reader_code_dir']) if node else
                 Path(os.environ['BTC_COLLECTOR_CONTRACT_PATH']).parent)
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location('btc_receipt_contract', directory/'btc_evidence.py')
    contract = importlib.util.module_from_spec(spec); spec.loader.exec_module(contract)
    with sqlite3.connect(config.as_uri()+'?mode=ro', uri=True) as connection:
        selected = connection.execute('SELECT chat_id FROM reader_config WHERE singleton_id=1').fetchone()
    if selected is None:
        raise Conflict('reader receipt history has no configured channel')
    done = {r['document_key']: r['digest'] for r in db.rows(
        "SELECT document_key,digest FROM projection_sources WHERE outcome IN ('READER_PRICES_V1','READER_INVALID_V1')")}
    counts, gaps, processed = [], [], 0
    with sqlite3.connect(sidecar.as_uri()+'?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        # Metadata first: already projected raw blobs are never decoded again.
        rows = connection.execute('SELECT a.*,p.text AS parent_text FROM collector_attachments a '
            'LEFT JOIN collector_messages p ON p.chat_id=a.chat_id AND p.message_id=a.parent_id '
            'WHERE a.chat_id=? AND a.raw_digest IS NOT NULL ORDER BY a.received_at,a.message_id,a.revision',
            (selected[0],)).fetchall()
        pending = [(identity('reader-prices', row['chat_id'], row['message_id'], row['revision']), row) for row in rows]
        pending = [(key, row) for key, row in pending if done.get(key) != row['raw_digest']]
        for key, row in pending[:limit]:
            blob = connection.execute('SELECT raw FROM collector_blobs WHERE digest=?', (row['raw_digest'],)).fetchone()
            if blob is None or fingerprint(blob['raw']) != row['raw_digest']:
                raise Conflict('archived reader attachment checksum mismatch')
            digest = db.put_blob(blob['raw'])
            try:
                bundle = contract.decode_bundle(blob['raw'])
                observation = contract.auxiliary_observation(bundle, row['parent_text'])
            except (ValueError, TypeError, KeyError, UnicodeError):
                observation = None
            outcome = 'READER_PRICES_V1'
            if observation is None:
                outcome = 'READER_INVALID_V1'
                gaps.append({'message_id': row['message_id'], 'reason': 'invalid auxiliary attachment',
                             'original_digest': digest})
            else:
                asset = observation.get('asset_id') or bundle.get('asset_id', 'BTC')
                observation['transport'] = {'received_at': row['received_at'],
                    'text_message_id': row['parent_id'], 'document_message_id': row['message_id']}
                result = ingest(db, {'assets': {asset: observation}}, db.blobs.path(digest))
                counts.extend(result['counts'])
                gaps.extend({'message_id': row['message_id'], **gap} for gap in result['gaps'])
            db.execute('REPLACE INTO projection_sources(document_key,digest,projected_ms,outcome) VALUES(%s,%s,%s,%s)',
                       (key, digest, millis(), outcome))
            processed += 1
    result = {'status': 'PARTIAL' if gaps else 'PROJECTED', 'processed': processed,
              'remaining': max(0, len(pending)-processed), 'counts': counts, 'gaps': gaps}
    if processed:
        db.metadata('reader.price_projection', {'at_ms': millis(), **result}, write=True)
    return result
