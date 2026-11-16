"""Project validated receipt history independently of model snapshot selection."""
import importlib.util
import json
import os
from pathlib import Path
import sys

from .codec import Conflict, fingerprint, identity, millis
from .collector_series import ingest
from .reader_retention import PROJECTION_SUMMARY, projection_summary


def project(db, *, limit=32):
    from .reader_store import native
    if not native(db):
        return {'status': 'NOT_CONFIGURED', 'processed': 0, 'remaining': 0}
    config = db.one('SELECT chat_id FROM reader_config WHERE singleton_id=1')
    if not config:
        raise Conflict('Docker reader has no configured channel')
    rows = db.rows('SELECT a.*,p.text AS parent_text FROM collector_attachments a '
                   'LEFT JOIN collector_messages p ON p.chat_id=a.chat_id AND p.message_id=a.parent_id '
                   'WHERE a.chat_id=%s AND a.raw_digest IS NOT NULL ORDER BY a.received_at,a.message_id,a.revision',
                   (config['chat_id'],))
    return _project_receipts(db, rows, db.blobs.get, limit)


def _project_receipts(db, rows, read_raw, limit):
    node = db.settings.get('container_node_config')
    directory = (Path(json.loads(Path(node).read_text())['reader_code_dir']) if node else
                 Path(os.environ['BTC_COLLECTOR_CONTRACT_PATH']).parent)
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location('btc_receipt_contract', directory/'btc_evidence.py')
    contract = importlib.util.module_from_spec(spec); spec.loader.exec_module(contract)
    done = {r['document_key']: r['digest'] for r in db.rows(
        "SELECT document_key,digest FROM projection_sources WHERE outcome IN ('READER_PRICES_V1','READER_INVALID_V1')")}
    counts, gaps, processed, changed_receipts = [], [], 0, []
    # Metadata first: already projected raw blobs are never decoded again.
    pending = [(identity('reader-prices', row['chat_id'], row['message_id'], row['revision']), row) for row in rows]
    pending = [(key, row) for key, row in pending if done.get(key) != row['raw_digest']]
    for key, row in pending[:limit]:
        first_count = len(counts)
        raw = read_raw(row['raw_digest'])
        if raw is None or fingerprint(raw) != row['raw_digest']:
            raise Conflict('archived reader attachment checksum mismatch')
        digest = db.put_blob(raw)
        try:
            bundle = contract.decode_bundle(raw)
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
            available = row['raw_available_at'] if 'raw_available_at' in row.keys() else None
            # Old failed downloads have no successful-read clock. Their first
            # validated projection is a conservative bound, not a past fact.
            known = available if available is not None else (
                millis()/1000 if row['download_attempts'] else row['received_at'])
            observation['transport'] = {'received_at': row['received_at'], 'known_at': known,
                'raw_available_at': available,
                'known_time_basis': 'RAW_AVAILABLE_AT' if available is not None else (
                    'LEGACY_PROJECTION_UPPER_BOUND' if row['download_attempts'] else 'LEGACY_RECEIPT'),
                'text_message_id': row['parent_id'], 'document_message_id': row['message_id']}
            result = ingest(db, {'assets': {asset: observation}}, db.blobs.path(digest))
            counts.extend(result['counts'])
            gaps.extend({'message_id': row['message_id'], **gap} for gap in result['gaps'])
        db.execute('REPLACE INTO projection_sources(document_key,digest,projected_ms,outcome) VALUES(%s,%s,%s,%s)',
                   (key, digest, millis(), outcome))
        changed_receipts.append({'document_key': key, 'digest': digest,
                                 'inserted': sum(item['inserted'] for item in counts[first_count:]),
                                 'outcome': outcome})
        processed += 1
    result = {'status': 'PARTIAL' if gaps else 'PROJECTED', 'processed': processed,
              'remaining': max(0, len(pending)-processed), 'counts': counts, 'gaps': gaps,
              'changed_receipts': changed_receipts}
    if processed:
        db.metadata(PROJECTION_SUMMARY, projection_summary({'at_ms': millis(), **result}), write=True)
    return result
