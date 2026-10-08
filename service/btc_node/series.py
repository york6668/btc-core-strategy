"""Product-specific observations with an as-known clock and immutable revisions."""
from __future__ import annotations

from .codec import Conflict, encode, fingerprint, identity, millis
from .entities import Entities


class Series:
    def __init__(self, database):
        self.db = database

    def preserve_original(self, source_digest, provenance):
        original = provenance.get('original_digest')
        if original:
            self.db.execute('INSERT IGNORE INTO series_originals(source_digest,original_digest) VALUES(%s,%s)',
                            (source_digest, original))

    def backfill_originals(self):
        sources = self.db.rows('SELECT s.source_digest FROM '
            '(SELECT source_digest FROM observations UNION SELECT source_digest FROM series_retrievals) s '
            'LEFT JOIN series_originals r USING(source_digest) WHERE r.source_digest IS NULL')
        for row in sources:
            self.preserve_original(row['source_digest'], self.db.value(row['source_digest']))

    def instrument(self, product, identity_spec):
        required = {'market', 'symbol', 'product_type', 'quote_unit', 'source_identity'}
        if not isinstance(product, str) or not 0 < len(product) <= 191:
            raise ValueError('invalid product identity')
        if not required <= set(identity_spec) or any(not identity_spec[x] for x in required):
            raise ValueError('explicit product, venue, quote unit and source identity required')
        digest = self.db.put_blob(encode(identity_spec))
        row = self.db.one('SELECT identity_digest FROM instruments WHERE product=%s FOR UPDATE', (product,))
        if row and row['identity_digest'] != digest:
            raise Conflict('product identity/unit changed; register a distinct product')
        self.db.execute('INSERT IGNORE INTO instruments(product,identity_digest) VALUES(%s,%s)', (product, digest))
        return {'product': product, 'identity': identity_spec}

    def ingest(self, product, series, rows, provenance, *, interval_ms):
        if not self.db.one('SELECT product FROM instruments WHERE product=%s', (product,)):
            raise ValueError('register the verified product identity before observations')
        if not isinstance(series, str) or not 0 < len(series) <= 191 or not 0 < len(rows) <= 10000:
            raise ValueError('invalid series or batch size')
        if not isinstance(interval_ms, int) or interval_ms < 0:
            raise ValueError('invalid observation interval')
        source_digest = self.db.put_blob(encode(provenance))
        self.preserve_original(source_digest, provenance)
        # Re-verification is recorded once per retrieval. A new fetch time or
        # surrounding package does not revise every unchanged historical bar.
        def semantics(value):
            return {k:v for k,v in value.items() if k not in {'retrieval','retrieved_at','package_id','original_digest'}}
        current_source = semantics(provenance)
        source_cache = {source_digest:current_source}
        item_keys = list({str(row['item_key']) for row in rows})
        previous_rows = self.db.rows('SELECT * FROM (SELECT o.*,ROW_NUMBER() OVER(PARTITION BY item_key '
            'ORDER BY known_ms DESC,sequence DESC) AS rn FROM observations o WHERE product=%s AND series=%s AND item_key IN ('+
            ','.join('%s' for _ in item_keys)+')) ranked WHERE rn=1', (product,series,*item_keys))
        latest = {row['item_key']:row for row in previous_rows}
        visible = {}
        self.db.execute('INSERT IGNORE INTO series_retrievals(retrieval_key,product,series,known_ms,source_digest) VALUES(%s,%s,%s,%s,%s)',
                        (identity(product,series,source_digest),product,series,max(millis(row['known_at']) for row in rows),source_digest))
        changed, unchanged = 0, 0
        for item in rows:
            observed, known = millis(item['observed_at']), millis(item['known_at'])
            if observed > known or observed < 0 or known > millis():
                raise ValueError('observation uses future data or invalid timestamps')
            item_key = str(item['item_key'])
            if not 0 < len(item_key) <= 191:
                raise ValueError('invalid measurement identity')
            data = encode(item['payload'])
            digest = fingerprint(data)
            current = latest.get(item_key)
            previous = current
            if current and known < current['known_ms']:
                if known not in visible:
                    historical = self.db.rows('SELECT * FROM (SELECT o.*,ROW_NUMBER() OVER(PARTITION BY item_key '
                        'ORDER BY known_ms DESC,sequence DESC) AS rn FROM observations o WHERE product=%s AND series=%s '
                        'AND known_ms<=%s AND item_key IN ('+','.join('%s' for _ in item_keys)+')) ranked WHERE rn=1',
                        (product, series, known, *item_keys))
                    visible[known] = {row['item_key']: row for row in historical}
                previous = visible[known].get(item_key)
            if previous and previous['source_digest'] not in source_cache:
                source_cache[previous['source_digest']] = semantics(self.db.value(previous['source_digest']))
            if previous and previous['digest'] == digest and source_cache[previous['source_digest']] == current_source and previous['observed_ms'] == observed and known >= previous['known_ms']:
                unchanged += 1
                continue
            observation_key = identity(product, series, item_key, observed, known, digest, source_digest)
            self.db.put_blob(data)
            inserted = self.db.execute('INSERT IGNORE INTO observations(observation_key,product,series,item_key,'
                                       'observed_ms,known_ms,recorded_ms,interval_ms,digest,source_digest) '
                                       'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                                       (observation_key, product, series, item_key, observed, known, millis(),
                                        interval_ms, digest, source_digest))
            changed += inserted
            revision = {'digest':digest,'source_digest':source_digest,'observed_ms':observed,'known_ms':known}
            if inserted and (not current or known >= current['known_ms']):
                latest[item_key] = revision
            if inserted:
                for cutoff, values in visible.items():
                    prior = values.get(item_key)
                    if known <= cutoff and (not prior or known >= prior['known_ms']):
                        values[item_key] = revision
            if inserted and current and known >= current['known_ms']:
                sequence = self.db.one('SELECT sequence FROM observations WHERE observation_key=%s',
                                       (observation_key,))['sequence']
                Entities(self.db).invalidate(identity(product, series, item_key), 'OBSERVATION', sequence,
                                             'source observation revised; reassess affected conclusions')
        return {'inserted': changed, 'unchanged': unchanged}

    def products(self, *, after='', limit=100):
        if not 1 <= limit <= 500:
            raise ValueError('bounded product limit required')
        rows = self.db.rows('SELECT * FROM instruments WHERE product>%s ORDER BY product LIMIT %s', (after, limit+1))
        return {'products': [{'product':r['product'],'identity':self.db.value(r['identity_digest'])} for r in rows[:limit]],
                'next': rows[limit-1]['product'] if len(rows)>limit else None}

    def repair_seller_references(self, *, after='', limit=100):
        """Append reference corrections; never revise a numerical observation."""
        from .collector_series import SELLER, seller_source_ref
        if not isinstance(after, str) or not 1 <= limit <= 500:
            raise ValueError('bounded source continuation required')
        sources = self.db.rows('SELECT DISTINCT source_digest FROM observations '
            'WHERE series=%s AND source_digest>%s ORDER BY source_digest LIMIT %s',
            (SELLER, after, limit + 1))
        repaired, unchanged, gaps = 0, 0, []
        for row in sources[:limit]:
            digest = row['source_digest']
            name = 'seller.ref-repair:' + digest
            if self.db.metadata(name):
                unchanged += 1
                continue
            try:
                provenance = self.db.value(digest)
                original = provenance.get('original_digest')
                if not isinstance(original, str) or len(original) != 64:
                    raise ValueError('original source digest is missing')
                payloads = [self.db.value(r['digest']) for r in self.db.rows(
                    'SELECT digest FROM observations WHERE series=%s AND source_digest=%s', (SELLER, digest))]
                ref = seller_source_ref(original, self.db.value(original), payloads)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                gaps.append({'source_digest': digest, 'reason': str(exc)})
                continue
            old = provenance.get('retrieval', {}).get('raw_ref')
            if old == ref:
                unchanged += 1
                continue
            self.db.metadata(name, {'schema': 'SELLER_REFERENCE_CORRECTION_V1',
                'source_digest': digest, 'original_digest': original, 'known_ms': millis(),
                'original_ref': old, 'corrected_ref': ref,
                'reason': 'reference pointer must resolve against the stored original source shape'}, write=True)
            repaired += 1
        return {'repaired': repaired, 'unchanged': unchanged, 'gaps': gaps,
                'next': sources[limit-1]['source_digest'] if len(sources)>limit else None}

    def query(self, product, series, *, start, end, as_of, limit=1000, after=None):
        if not 1 <= limit <= 10000:
            raise ValueError('bounded query limit required')
        start_ms, end_ms, known_ms = millis(start), millis(end), millis(as_of)
        if start_ms > end_ms or known_ms > millis():
            raise ValueError('invalid query window')
        identity_row = self.db.one('SELECT identity_digest FROM instruments WHERE product=%s', (product,))
        if not identity_row:
            raise ValueError('unknown product')
        cursor = after or {'observed_ms': -1, 'item_key': ''}
        if set(cursor) != {'observed_ms', 'item_key'} or type(cursor['observed_ms']) is not int or not isinstance(cursor['item_key'], str):
            raise ValueError('invalid observation continuation cursor')
        rows = self.db.rows('SELECT * FROM (SELECT o.*,ROW_NUMBER() OVER('
                            'PARTITION BY item_key ORDER BY known_ms DESC,sequence DESC) AS rn '
                            'FROM observations o WHERE product=%s AND series=%s AND known_ms<=%s) visible '
                            'WHERE rn=1 AND observed_ms>=%s AND observed_ms<=%s '
                            'AND (observed_ms,item_key)>(%s,%s) '
                            'ORDER BY observed_ms,item_key LIMIT %s',
                            (product, series, known_ms, start_ms, end_ms, cursor['observed_ms'], cursor['item_key'], limit + 1))
        truncated = len(rows) > limit
        items = [{k: v for k, v in row.items() if k not in {'rn', 'digest', 'source_digest'}} |
                 {'payload': self.db.value(row['digest']), 'provenance': self.db.value(row['source_digest'])}
                 for row in rows[:limit]]
        corrections = {}
        if series == 'seller_exhaustion_daily':
            for row, item in zip(rows[:limit], items):
                digest = row['source_digest']
                if digest not in corrections:
                    corrections[digest] = self.db.metadata('seller.ref-repair:' + digest)
                correction = corrections[digest]
                if correction and correction['known_ms'] <= known_ms:
                    item['reference_correction'] = correction
        return {'product': product, 'identity': self.db.value(identity_row['identity_digest']),
                'series': series, 'as_of_ms': known_ms, 'observations': items, 'truncated': truncated,
                'next': {k:items[-1][k] for k in ('observed_ms','item_key')} if truncated else None}
