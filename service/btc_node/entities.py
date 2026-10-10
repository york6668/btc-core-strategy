"""Versioned business objects; no indicator-to-decision rules live here."""
from __future__ import annotations

from .codec import Conflict, encode, identity, millis


KINDS = {'REGION', 'PRICE_EVENT', 'HYPOTHESIS', 'CYCLE', 'FACTOR', 'ALLOCATION',
         'PLAN', 'RESEARCH', 'DELIVERY', 'FACT_PROJECTION', 'PROFILE', 'DECISION',
         'REVIEW', 'CORRECTION'}
PERSONAL = {'ALLOCATION', 'FACT_PROJECTION', 'PROFILE'}


def key(kind, object_id):
    if kind not in KINDS or not isinstance(object_id, str) or not 0 < len(object_id) <= 512:
        raise ValueError('invalid business object identity')
    return identity(kind, object_id)


class Entities:
    def __init__(self, database):
        self.db = database

    def get(self, kind, object_id, *, as_of=None, version=None):
        entity_key = key(kind, object_id)
        row = self.db.one('SELECT * FROM entities WHERE entity_key=%s', (entity_key,))
        if row is None:
            return None
        if version is not None:
            revision = self.db.one('SELECT * FROM entity_versions WHERE entity_key=%s AND version=%s',
                                   (entity_key, version))
        elif as_of is not None:
            revision = self.db.one('SELECT * FROM entity_versions WHERE entity_key=%s AND known_ms<=%s '
                                   'ORDER BY known_ms DESC,version DESC LIMIT 1', (entity_key, millis(as_of)))
        else:
            revision = self.db.one('SELECT * FROM entity_versions WHERE entity_key=%s AND version=%s',
                                   (entity_key, row['current_version']))
        if revision is None:
            return None
        state = self.db.value(revision['digest'])
        return {'kind': kind, 'object_id': object_id, 'scope': row['scope'],
                'product': row['product'], 'first_identified_ms': row['first_identified_ms'],
                **revision, **state}

    def put(self, spec, *, expected_version, trusted_projection=False):
        # A review records what was checked at a particular version. Its subject
        # owns any remaining work; the record must not become another live plan.
        if spec['kind'] == 'REVIEW':
            spec = {**spec, 'active': False, 'review_at': None, 'valid_until': None}
        kind, object_id = spec['kind'], spec['object_id']
        entity_key = key(kind, object_id)
        scope = spec['scope']
        if scope not in {'PUBLIC', 'PERSONAL'} or (kind in PERSONAL and scope != 'PERSONAL'):
            raise ValueError('invalid object scope')
        if kind in {'FACT_PROJECTION', 'PROFILE'} and not trusted_projection:
            raise ValueError('facts and preferences require the confirmed local-file importer')
        for field in ('change_reason', 'source_ref', 'method_version'):
            if not isinstance(spec.get(field), str) or not spec[field].strip():
                raise ValueError(field + ' is required')
        observed, known = millis(spec['observed_at']), millis(spec['known_at'])
        if observed > known or known > millis():
            raise ValueError('invalid observed/known time ordering')
        current = self.db.one('SELECT * FROM entities WHERE entity_key=%s FOR UPDATE', (entity_key,))
        actual_version = current['current_version'] if current else 0
        if expected_version != actual_version:
            raise Conflict('stale object version: expected %s, current %s' % (expected_version, actual_version))
        if current and (current['scope'] != scope or current['product'] != spec.get('product')):
            raise Conflict('object scope or product identity cannot be rewritten')
        previous = self.get(kind, object_id) if current else None
        if previous and not previous['active'] and spec.get('active', True):
            raise Conflict('retired object remains historical; create an explicitly related replacement')
        if previous and previous.get('valid_until') != spec.get('valid_until') and previous.get('valid_until'):
            if millis(previous['valid_until']) <= millis():
                review = spec.get('outcome_review')
                completed = self.get('REVIEW', review) if review else None
                if (not completed or completed['payload'].get('subject') != {'kind': kind, 'object_id': object_id, 'version': actual_version}
                        or completed['payload'].get('outcome') not in {'REALIZED', 'FAILED', 'REVISE'}
                        or not str(completed['payload'].get('explanation', '')).strip()
                        or not completed['dependencies'] or completed['known_ms'] > known):
                    raise Conflict('expired horizon requires an outcome review of the original version')
        if previous and (known < previous['known_ms'] or
                         (observed < previous['observed_ms'] and spec.get('correction_of') != actual_version)):
            raise Conflict('late result cannot replace current judgement; bind an explicit correction')
        dependencies = []
        resolved = []
        for supplied in spec.get('dependencies', []):
            dependency = dict(supplied)
            target_type = dependency['type']
            if target_type == 'ENTITY':
                target = key(dependency['kind'], dependency['object_id'])
                owner = self.get(dependency['kind'], dependency['object_id'],
                                 as_of=spec['known_at'], version=dependency.get('version'))
                if owner is None or owner['known_ms'] > known:
                    raise ValueError('dependency was not known at this judgement time')
                if scope == 'PUBLIC' and owner['scope'] == 'PERSONAL':
                    raise ValueError('public market judgement cannot depend on personal inputs')
                if dependency.get('digest', owner['digest']) != owner['digest']:
                    raise Conflict('dependency version and content do not match')
                dependency.update(version=owner['version'], digest=owner['digest'])
            elif target_type == 'BLOB':
                target = dependency['digest'].removeprefix('sha256:')
                if not self.db.one('SELECT digest FROM blobs WHERE digest=%s', (target,)):
                    raise ValueError('evidence blob is not stored')
            elif target_type == 'OBSERVATION':
                target = identity(dependency['product'], dependency['series'], dependency['item_key'])
                revision = self.db.one('SELECT observation_key,digest,known_ms FROM observations WHERE product=%s AND series=%s '
                                       'AND item_key=%s AND known_ms<=%s ORDER BY known_ms DESC,sequence DESC LIMIT 1',
                                       (dependency['product'], dependency['series'], dependency['item_key'], known))
                if not revision:
                    raise ValueError('observation dependency was not yet known')
                requested = dependency.get('observation_key')
                if requested:
                    revision = self.db.one('SELECT observation_key,digest,known_ms FROM observations '
                                           'WHERE observation_key=%s AND product=%s AND series=%s AND item_key=%s',
                                           (requested, dependency['product'], dependency['series'], dependency['item_key']))
                    if not revision or revision['known_ms'] > known:
                        raise ValueError('observation revision was not yet known')
                dependency.update(observation_key=revision['observation_key'], digest=revision['digest'])
            else:
                raise ValueError('unsupported dependency type')
            relation = dependency.get('relation', 'EVIDENCE')
            if not isinstance(relation, str) or not 0 < len(relation) <= 48:
                raise ValueError('invalid dependency relation')
            resolved.append((target, target_type, relation))
            dependencies.append(dependency)
        state = {'payload': spec['payload'], 'active': bool(spec.get('active', True)),
                 'review_at': spec.get('review_at'), 'valid_until': spec.get('valid_until'),
                 'dependencies': dependencies}
        digest = self.db.put_blob(encode(state))
        if previous and previous['digest'] == digest:
            self.db.execute('UPDATE entities SET last_checked_ms=%s WHERE entity_key=%s', (known, entity_key))
            return {'object_id': object_id, 'version': actual_version, 'changed': False}
        identified = millis(spec.get('identified_at', spec['known_at']))
        if identified > known:
            raise ValueError('first identification cannot be in the future')
        version = actual_version + 1
        review_at = millis(state['review_at']) if state['review_at'] else None
        valid_until = millis(state['valid_until']) if state['valid_until'] else None
        if current:
            if 'identified_at' in spec and identified != current['first_identified_ms']:
                raise Conflict('first identification time must survive subsequent observations')
            self.db.execute('UPDATE entities SET current_version=%s,last_checked_ms=%s,active=%s,'
                            'review_at_ms=%s,valid_until_ms=%s WHERE entity_key=%s',
                            (version, known, state['active'], review_at, valid_until, entity_key))
        else:
            self.db.execute('INSERT INTO entities(entity_key,object_id,kind,scope,product,current_version,'
                            'first_identified_ms,last_checked_ms,active,review_at_ms,valid_until_ms) '
                            'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                            (entity_key, object_id, kind, scope, spec.get('product'), version, identified,
                             known, state['active'], review_at, valid_until))
        self.db.execute('INSERT INTO entity_versions(entity_key,version,digest,observed_ms,known_ms,'
                        'recorded_ms,change_reason,source_ref,method_version) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (entity_key, version, digest, observed, known, millis(), spec['change_reason'],
                         spec['source_ref'], spec['method_version']))
        for target, target_type, relation in set(resolved):
            self.db.execute('INSERT INTO dependencies(source_key,source_version,target_key,target_type,relation) '
                            'VALUES(%s,%s,%s,%s,%s)', (entity_key, version, target, target_type, relation))
        self.invalidate(entity_key, 'ENTITY', version, spec['change_reason'])
        return {'object_id': object_id, 'version': version, 'changed': True}

    def invalidate(self, cause, cause_type, cause_version, reason):
        pending, visited = [(cause, cause_type)], {cause}
        affected = []
        while pending:
            target, target_type = pending.pop()
            rows = self.db.rows('SELECT e.entity_key,e.object_id,e.kind FROM dependencies d JOIN entities e '
                                'ON e.entity_key=d.source_key AND e.current_version=d.source_version '
                                "WHERE d.target_key=%s AND d.target_type=%s AND (e.active=1 OR e.kind IN ('REVIEW','DECISION'))",
                                (target, target_type))
            for row in rows:
                source = row['entity_key']
                if source in visited:
                    continue
                visited.add(source)
                if row['kind'] in {'REVIEW', 'DECISION'}:
                    # Immutable judgements/reviews bridge changes to active
                    # consumers; the historical record itself is not fresh work.
                    pending.append((source, 'ENTITY'))
                    continue
                requirement = identity(source, cause, cause_version)
                self.db.execute('INSERT IGNORE INTO review_requirements(requirement_key,entity_key,cause_key,'
                                'cause_version,reason,opened_ms) VALUES(%s,%s,%s,%s,%s,%s)',
                                (requirement, source, cause, cause_version, reason, millis()))
                affected.append({'kind': row['kind'], 'object_id': row['object_id'], 'requirement': requirement})
                pending.append((source, 'ENTITY'))
        return affected

    def list(self, *, scope, kind=None, product=None, active=True, limit=100, after=None):
        if scope not in {'PUBLIC', 'PERSONAL'} or not 1 <= limit <= 500:
            raise ValueError('scope and bounded limit required')
        clauses, args = ['scope=%s'], [scope]
        if active is True and kind is None:
            # Old releases wrote active REVIEW rows. Preserve those versions,
            # but do not let their legacy flag pollute the maintenance index.
            clauses.append("kind<>'REVIEW'")
        for column, value in (('kind', kind), ('product', product), ('active', active)):
            if value is not None:
                clauses.append(column + '=%s'); args.append(value)
        if after:
            clauses.append('entity_key>%s'); args.append(after)
        args.append(limit)
        rows = self.db.rows('SELECT * FROM entities WHERE ' + ' AND '.join(clauses) +
                            ' ORDER BY entity_key LIMIT %s', tuple(args))
        cursor = rows[-1]['entity_key'] if len(rows) == limit else None
        if active is True and kind is None:
            visible = []
            for row in rows:
                if row['kind'] == 'DELIVERY':
                    recovery = self.get('DELIVERY', row['object_id'])['payload'].get('recovery') or {}
                    attempts = recovery.get('attempts', [])
                    if (attempts and not recovery.get('actions')
                            and all(v.get('next_action') == 'WAIT_FOR_NEW_EVIDENCE' for v in attempts)):
                        continue
                visible.append(row)
            rows = visible
        # Cursor follows scanned rows even when a waiting delivery is omitted.
        return {'objects': rows, 'next': cursor}

    def history(self, kind, object_id, *, after=0, limit=100):
        if not 1 <= limit <= 500:
            raise ValueError('bounded history limit required')
        rows = self.db.rows('SELECT version,observed_ms,known_ms,recorded_ms,change_reason,source_ref,digest '
                            'FROM entity_versions WHERE entity_key=%s AND version>%s ORDER BY version LIMIT %s',
                            (key(kind, object_id), after, limit))
        return {'versions': rows, 'next': rows[-1]['version'] if len(rows) == limit else None}

    def due(self, *, scope, at=None, limit=100, after=None):
        if scope not in {'PUBLIC', 'PERSONAL'} or not 1 <= limit <= 500:
            raise ValueError('scope and bounded limit required')
        now_ms = millis(at)
        rows = self.db.rows('SELECT e.*,EXISTS(SELECT 1 FROM review_requirements r '
                            'WHERE r.entity_key=e.entity_key AND r.resolved_ms IS NULL) AS dependency_changed '
                            "FROM entities e WHERE e.scope=%s AND e.kind<>'REVIEW' AND "
                            '((e.active=1 AND (e.review_at_ms<=%s OR e.valid_until_ms<=%s)) OR EXISTS '
                            '(SELECT 1 FROM review_requirements r WHERE r.entity_key=e.entity_key '
                            'AND r.resolved_ms IS NULL)) AND e.entity_key>%s ORDER BY e.entity_key LIMIT %s',
                            (scope, now_ms, now_ms, after or '', limit + 1))
        truncated = len(rows) > limit
        rows = rows[:limit]
        return {'objects': rows, 'as_of_ms': now_ms, 'truncated': truncated,
                'next': rows[-1]['entity_key'] if truncated else None}

    def resolve(self, requirement, resolution_ref):
        obligation = self.db.one('SELECT * FROM review_requirements WHERE requirement_key=%s', (requirement,))
        review = self.get('REVIEW', resolution_ref)
        if not obligation or not review or requirement not in review['payload'].get('requirements', []):
            raise ValueError('stored review must explicitly address this requirement')
        if (not review['payload'].get('outcome') or
                review['payload']['outcome'] in {'PENDING', 'EVIDENCE_LIMITED'} or
                not str(review['payload'].get('explanation', '')).strip() or not review['dependencies']):
            raise ValueError('an unresolved or unevidenced review cannot discharge the subject requirement')
        subject = review['payload'].get('subject', {})
        if key(subject.get('kind'), subject.get('object_id')) != obligation['entity_key']:
            raise ValueError('review concerns a different object')
        current = self.get(subject['kind'], subject['object_id'])
        if subject.get('version') != current['version'] or review['known_ms'] < obligation['opened_ms']:
            raise Conflict('review is stale')
        self.db.execute('UPDATE review_requirements SET resolved_ms=%s,resolution_ref=%s '
                        'WHERE requirement_key=%s AND resolved_ms IS NULL',
                        (millis(), resolution_ref, requirement))
