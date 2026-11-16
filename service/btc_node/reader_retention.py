"""Retire receipt payloads without changing the current report or price history."""

from .codec import identity
from .reader_store import native


PROJECTION_SUMMARY = 'reader.price_projection'


def projection_summary(result):
    return {**{k: result[k] for k in ('at_ms', 'status', 'processed', 'remaining') if k in result},
            'gap_count': len(result['gaps'])}


def expire_receipts(db, *, cutoff_ms, apply):
    result = {'eligible': 0, 'eligible_bytes': 0, 'retired': 0, 'unlinked_blobs': 0, 'unlinked_bytes': 0}
    if not native(db):
        return result
    if apply:
        # Older diagnostic summaries accidentally pinned every receipt from
        # the last batch. The projection and formal citations have separate
        # durable records; the operational summary needs no content pointers.
        previous = db.metadata(PROJECTION_SUMMARY)
        if previous and 'gaps' in previous:
            db.metadata(PROJECTION_SUMMARY, projection_summary(previous), write=True)

    # Require the exact payload to have been handled by the history projector.
    # Leave unresolved current orphans alone: their payload may be the only
    # way the reader identifies their asset and prevents an older fallback.
    cutoff = cutoff_ms / 1000
    rows = db.rows(
        'SELECT a.chat_id,a.message_id,a.revision,a.raw_digest,a.download_attempts,a.raw_available_at,'
        'b.byte_length FROM collector_attachments a JOIN blobs b ON b.digest=a.raw_digest '
        'LEFT JOIN collector_messages p ON p.chat_id=a.chat_id AND p.message_id=a.parent_id '
        'LEFT JOIN collector_messages m ON m.chat_id=a.chat_id AND m.message_id=a.message_id AND m.revision=a.revision '
        'WHERE a.received_at<%s AND (a.raw_available_at IS NULL OR a.raw_available_at<%s) '
        'AND (p.text IS NOT NULL OR m.message_id IS NULL) '
        'AND NOT EXISTS(SELECT 1 FROM series_originals s WHERE s.original_digest=a.raw_digest) '
        "AND NOT EXISTS(SELECT 1 FROM dependencies d WHERE d.target_type='BLOB' AND d.target_key=a.raw_digest)",
        (cutoff, cutoff))
    if not rows:
        return result
    projected = {r['document_key']: r for r in db.iter_rows(
        'SELECT document_key,digest,projected_ms FROM projection_sources '
        "WHERE outcome IN ('READER_PRICES_V1','READER_INVALID_V1')")}
    retired_blobs = {}
    for row in rows:
        key = (row['chat_id'], row['message_id'], row['revision'])
        proof = projected.get(identity('reader-prices', *key))
        if not proof or proof['digest'] != row['raw_digest']:
            continue
        if row['download_attempts'] and row['raw_available_at'] is None and proof['projected_ms'] >= cutoff_ms:
            continue  # For a delayed legacy download, projection bounds first availability.
        result['eligible'] += 1
        retired_blobs[row['raw_digest']] = row['byte_length']
        if apply:
            # The receipt remains as a tombstone. No download retry, no older
            # attachment substituted for this report, and no change to cursor.
            result['retired'] += db.execute(
                'UPDATE collector_attachments SET raw_digest=NULL,file_id=\'\',file_unique_id=NULL,'
                "download_status='expired',download_error=NULL,download_retry_at=0 "
                'WHERE chat_id=%s AND message_id=%s AND revision=%s AND raw_digest=%s',
                (*key, row['raw_digest']))
    result['eligible_bytes'] = sum(retired_blobs.values())
    if not apply:
        return result
    for digest, size in retired_blobs.items():
        if db.execute('DELETE FROM collector_blobs WHERE digest=%s AND NOT EXISTS('
                      'SELECT 1 FROM collector_attachments WHERE raw_digest=%s)', (digest, digest)):
            # The global CAS collector below checks other references, backs
            # off for the recycling grace, and physically unlinks the file.
            result['unlinked_blobs'] += 1
            result['unlinked_bytes'] += size
    return result
