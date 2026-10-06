"""Single service boundary used by the socket client, CLI and stdio MCP."""
from __future__ import annotations

import base64

from .codec import Conflict, encode, fingerprint, millis
from .database import Database
from .entities import Entities
from .series import Series


READS = {'status', 'object.get', 'object.list', 'object.history', 'conditions.due',
         'prices.query', 'prices.products', 'context.public', 'context.personal', 'storage.status',
         'acceptance.window', 'acceptance.status', 'failure.get', 'failure.list'}
WRITES = {'object.put', 'review.resolve', 'prices.register', 'prices.ingest',
          'evidence.put', 'market.freeze', 'facts.project', 'acceptance.start', 'acceptance.record', 'acceptance.defect', 'acceptance.repair', 'failure.resolve'}
HOST_METHODS = {'core.execute', 'core.recover', 'reader.checkpoint', 'host.capacity'}


def invoke(database, method, params):
    objects, series = Entities(database), Series(database)
    params = dict(params)
    if method in {'object.get','object.list','object.history','conditions.due'}:
        binding=params.pop('market_binding',None)
        personal=params.get('scope')=='PERSONAL'
        if method=='object.history':
            owner=objects.get(params['kind'],params['object_id'])
            personal=bool(owner and owner['scope']=='PERSONAL')
        if personal:
            bound=objects.get('DECISION',binding['object_id'],version=binding['version']) if binding else None
            if not bound or bound['scope']!='PUBLIC' or bound['digest']!=binding['digest']:
                raise ValueError('freeze independent market judgement before reading personal objects')
    if method.startswith('acceptance.'):
        from . import acceptance
        return getattr(acceptance, method.split('.')[1])(database, **params)
    if method.startswith('failure.'):
        from . import failures
        return getattr(failures, 'listing' if method == 'failure.list' else method.split('.')[1])(database, **params)
    if method == 'status':
        return {'schema': database.one('SELECT version FROM node_schema WHERE singleton=1')['version'],
                'writer': database.metadata('writer'),
                'pending_operations': database.rows("SELECT operation_id,status,started_ms FROM operations WHERE status<>'COMMITTED'"),
                'objects': database.rows('SELECT kind,scope,COUNT(*) AS count FROM entities GROUP BY kind,scope')}
    if method == 'object.get':
        selected = dict(params)
        scope = selected.pop('scope')
        result = objects.get(**selected)
        if result and result['scope'] != scope:
            raise ValueError('object belongs to a different analysis phase')
        return result
    if method == 'object.list':
        return objects.list(**params)
    if method == 'object.history':
        return objects.history(**params)
    if method == 'object.put':
        if set(params) != {'spec', 'expected_version'}:
            raise ValueError('only spec and expected_version are accepted')
        spec = params['spec']
        if not isinstance(spec.get('payload'), dict):
            raise ValueError('typed business payload must be an object')
        if spec['kind'] == 'DELIVERY':
            raise ValueError('delivery changes use the native delivery protocol and its validated projection')
        if spec['kind'] == 'ALLOCATION':
            market = spec['payload'].get('market_binding', {})
            reference = objects.get('DECISION', market.get('object_id', ''), version=market.get('version'))
            if not reference or reference['scope'] != 'PUBLIC' or reference['digest'] != market.get('digest'):
                raise ValueError('allocation requires the frozen independent market judgement')
        return objects.put(**params)
    if method == 'review.resolve':
        objects.resolve(**params)
        return {'resolved': params['requirement']}
    if method == 'conditions.due':
        return objects.due(**params)
    if method == 'prices.register':
        return series.instrument(**params)
    if method == 'prices.ingest':
        return series.ingest(**params)
    if method == 'prices.query':
        return series.query(**params)
    if method == 'prices.products':
        return series.products(**params)
    if method == 'evidence.put':
        data = base64.b64decode(params['base64'], validate=True)
        if len(data) > 24 * 1024 * 1024:
            raise ValueError('split evidence exceeding 24 MiB')
        return {'digest': database.put_blob(data), 'bytes': len(data)}
    if method == 'market.freeze':
        if set(params) != {'spec', 'expected_version'}:
            raise ValueError('only spec and expected_version are accepted')
        spec = params['spec']
        if spec['kind'] != 'DECISION' or spec['scope'] != 'PUBLIC':
            raise ValueError('only an independent public judgement may be frozen')
        result = objects.put(**params)
        frozen = objects.get('DECISION', spec['object_id'])
        return {**result, 'market_binding': {field: frozen[field] for field in ('object_id', 'version', 'digest')}}
    if method == 'context.public':
        return {'objects': objects.list(scope='PUBLIC', **params),
                'due': objects.due(scope='PUBLIC'), 'personal_inputs': 'WITHHELD'}
    if method == 'context.personal':
        binding = params['market_binding']
        frozen = objects.get('DECISION', binding['object_id'], version=binding['version'])
        if not frozen or frozen['scope'] != 'PUBLIC' or frozen['digest'] != binding['digest']:
            raise ValueError('freeze independent market judgement before requesting personal context')
        return {'market': frozen, 'objects': objects.list(scope='PERSONAL'),
                'due': objects.due(scope='PERSONAL'),
                'fact_authority': 'confirmed local files; recommendations never change holdings'}
    if method == 'facts.project':
        supplied = dict(params)
        binding = supplied.pop('market_binding')
        frozen = objects.get('DECISION', binding['object_id'], version=binding['version'])
        if not frozen or frozen['scope'] != 'PUBLIC' or frozen['digest'] != binding['digest']:
            raise ValueError('freeze independent market judgement before extracting personal facts')
        from .fact_views import project
        return project(database, **supplied)
    if method == 'storage.status':
        from .lifecycle import status
        return status(database)
    raise ValueError('unsupported service method: ' + method)


def dispatch(settings, request):
    method, params = request['method'], request.get('params', {})
    if method == 'core.execute':
        from .execution import execute
        return execute(settings, request)
    if method == 'core.recover':
        from .execution import recover
        return recover(settings, params['operation_id'])
    if method == 'host.capacity':
        db=Database(settings)
        try:
            with db.writer(),db.transaction():
                db.metadata('capacity.host',params,write=True)
                history=db.metadata('capacity.host.daily') or []
                prior=[r for r in history if r['day'] != params['day']]
                if prior:
                    params={**params,'mysql_growth_bytes':params['mysql_allocated_bytes']-prior[-1]['mysql_allocated_bytes']}
                db.metadata('capacity.host.daily',(prior+[params])[-401:],write=True)
                return {'recorded_day':params['day']}
        finally:db.close()
    if method == 'reader.checkpoint':
        from .catalog import Catalog
        db = Database(settings)
        try:
            with db.writer(), db.transaction():
                if (db.metadata('writer') or {}).get('owner') != 'DOCKER':
                    raise Conflict('reader checkpoint requires the active Docker writer')
                result = Catalog(db).sync('reader', settings['reader_data'])
                from .reader_prices import project
                prices = project(db)
                db.metadata('reader.checkpoint', {'at_ms': millis(), 'databases': result['sqlite']}, write=True)
                return {'checkpointed': True, 'databases': result['sqlite'], 'prices': prices}
        finally:
            db.close()
    if method not in READS | WRITES:
        raise ValueError('unsupported service method')
    database = Database(settings)
    try:
        if method in READS:
            with database.transaction():
                return invoke(database, method, params)
        operation_id = request.get('operation_id')
        if not isinstance(operation_id, str) or not 0 < len(operation_id) <= 191:
            raise ValueError('stable operation_id required for retryable writes')
        request_digest = fingerprint(encode({'method': method, 'params': params}))
        with database.writer(), database.transaction():
            if (database.metadata('writer') or {}).get('owner') != 'DOCKER':
                raise Conflict('comparison mode is read only; the Docker writer has not been activated')
            if database.one("SELECT operation_id FROM operations WHERE status<>'COMMITTED' LIMIT 1"):
                raise Conflict('recover the interrupted core operation before another business write')
            prior = database.one('SELECT * FROM operations WHERE operation_id=%s', (operation_id,))
            if prior:
                if prior['request_digest'] != request_digest:
                    raise Conflict('operation_id already used for another request')
                if prior['status'] != 'COMMITTED':
                    raise Conflict('operation needs recovery before retry')
                return database.value(prior['result_digest'])
            database.put_blob(encode({'method': method, 'params': params}))
            result = invoke(database, method, params)
            result_digest = database.put_blob(encode(result))
            database.execute('INSERT INTO operations(operation_id,request_digest,status,started_ms,finished_ms,'
                             'result_digest) VALUES(%s,%s,%s,%s,%s,%s)',
                             (operation_id, request_digest, 'COMMITTED', millis(), millis(), result_digest))
            return result
    finally:
        database.close()
