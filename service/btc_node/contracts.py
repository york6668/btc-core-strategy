"""Discoverable input contracts for business tools; payloads remain domain-owned."""


def obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


TEXT = {'type': 'string', 'minLength': 1}
TIME = {'type': 'string', 'format': 'date-time', 'description': 'Explicit ISO 8601 timestamp with timezone.'}
SCOPE = {'type': 'string', 'enum': ['PUBLIC', 'PERSONAL']}
VERSION = {'type': 'integer', 'minimum': 0}
MARKET_BINDING = obj({'object_id':TEXT,'version':VERSION,'digest':TEXT}, ['object_id','version','digest'])
SPEC = obj({'kind': TEXT, 'object_id': TEXT, 'scope': SCOPE, 'product': {'type': ['string','null']},
    'observed_at': TIME, 'known_at': TIME, 'identified_at': TIME,
    'change_reason': TEXT, 'source_ref': TEXT, 'method_version': TEXT,
    'payload': {'type': 'object', 'description': 'Original typed business fields: region/history, hypothesis/forward window, plan/conditions, research/progress, or review/subject. No program inference of confirmation or fills.'},
    'active': {'type': 'boolean'}, 'review_at': {'type': ['string','null']},
    'valid_until': {'type': ['string','null']}, 'correction_of': VERSION, 'outcome_review': TEXT,
    'dependencies': {'type': 'array', 'items': {'type': 'object', 'description':
        'ENTITY: type,kind,object_id,relation; BLOB: type,digest,relation; OBSERVATION: type,product,series,item_key,relation.'}}},
    ['kind','object_id','scope','observed_at','known_at','change_reason','source_ref','method_version','payload','dependencies'])

PARAMETERS = {
    'model.deep.status': obj({}),
    'model.deep.claim': obj({'claim_id':TEXT,'issue_id':TEXT,'scope':TEXT,'phase':TEXT,'input_digest':TEXT,'model':TEXT},
                             ['claim_id','issue_id','scope','phase','input_digest','model']),
    'model.deep.finish': obj({'claim_id':TEXT,'outcome':{'type':'string','enum':['COMPLETE','FAILED','UNKNOWN','CANCELED']},
        'result_ref':TEXT,'failure_kind':TEXT,'failure_ref':TEXT},['claim_id','outcome']),
    'model.deep.reconcile': obj({'claim_id':TEXT,'outcome':{'type':'string','enum':['COMPLETE','FAILED','CANCELED']},
        'evidence_ref':TEXT,'result_ref':TEXT},['claim_id','outcome','evidence_ref']),
    'model.defect.open': obj({'scope':TEXT,'kind':TEXT,'signature':TEXT,'fault_digest':TEXT,'evidence_ref':TEXT},
                              ['scope','kind','signature','fault_digest','evidence_ref']),
    'model.defect.resolve': obj({'fault_id':TEXT,'repair_digest':TEXT,'verification_ref':TEXT,'verification_status':TEXT},
                                 ['fault_id','repair_digest','verification_ref','verification_status']),
    'failure.get': obj({'document_key':TEXT}, ['document_key']),
    'failure.list': obj({'after':TEXT,'limit':{'type':'integer','minimum':1,'maximum':500}}),
    'failure.resolve': obj({'document_key':TEXT,'expected_digest':TEXT,'expected_version':VERSION,
        'resolution_operation_id':TEXT,'reason':TEXT},
        ['document_key','expected_digest','expected_version','resolution_operation_id','reason']),
    'acceptance.start': obj({'release':TEXT}, ['release']),
    'acceptance.status': obj({}),
    'acceptance.window': obj({'day':TEXT}, ['day']),
    'acceptance.record': obj({'day':TEXT,'checks':{'type':'object','description':'All nine criteria: status PASS/FAIL/REPLAY_ONLY, actual_input, state_change, consumer_result, stored evidence refs and any natural_gap.'}}, ['day','checks']),
    'acceptance.defect': obj({'reason':TEXT,'affected_days':{'type':'array','items':TEXT},'criteria':{'type':'array','items':TEXT}}, ['reason','affected_days','criteria']),
    'acceptance.repair': obj({'defect_index':VERSION,'release':TEXT,'reason':TEXT,'evidence_refs':{'type':'array','minItems':1,'items':{'type':'object'}}, 'preserve_window':{'type':'boolean','description':'Close a scoped correction only after its actual natural acceptance; retain the shared window and other criteria.'}}, ['defect_index','release','reason','evidence_refs']),
    'status': obj({}), 'storage.status': obj({}),
    'object.get': obj({'kind': TEXT, 'object_id': TEXT, 'scope': SCOPE, 'version': VERSION, 'as_of': TIME,
                       'market_binding':MARKET_BINDING}, ['kind','object_id','scope']),
    'object.put': obj({'spec': SPEC, 'expected_version': VERSION}, ['spec','expected_version']),
    'market.freeze': obj({'spec': SPEC, 'expected_version': VERSION}, ['spec','expected_version']),
    'object.list': obj({'scope': SCOPE, 'kind': TEXT, 'product': TEXT, 'active': {'type': ['boolean','null']},
                        'market_binding':MARKET_BINDING,'limit': {'type':'integer','minimum':1,'maximum':500}, 'after': TEXT}, ['scope']),
    'object.history': obj({'kind': TEXT,'object_id': TEXT,'after': VERSION,'market_binding':MARKET_BINDING,
                           'limit': {'type':'integer','minimum':1,'maximum':500}}, ['kind','object_id']),
    'conditions.due': obj({'scope': SCOPE,'at': TIME,'after': TEXT,'market_binding':MARKET_BINDING,
                           'limit': {'type':'integer','minimum':1,'maximum':500}}, ['scope']),
    'review.resolve': obj({'requirement': TEXT, 'resolution_ref': TEXT}, ['requirement','resolution_ref']),
    'prices.register': obj({'product': TEXT, 'identity_spec': obj({k:TEXT for k in ('market','symbol','product_type','quote_unit','source_identity')},
                             ['market','symbol','product_type','quote_unit','source_identity'])}, ['product','identity_spec']),
    'prices.ingest': obj({'product': TEXT, 'series': TEXT, 'interval_ms': VERSION, 'provenance': {'type':'object'},
        'rows': {'type':'array','minItems':1,'maxItems':10000,'items':obj({'item_key':TEXT,'observed_at':TIME,'known_at':TIME,'payload':{'type':'object'}},
                                                           ['item_key','observed_at','known_at','payload'])}},
        ['product','series','interval_ms','provenance','rows']),
    'prices.query': obj({'product':TEXT,'series':TEXT,'start':TIME,'end':TIME,'as_of':TIME,
                          'after':obj({'observed_ms':{'type':'integer'},'item_key':TEXT}, ['observed_ms','item_key']),
                          'limit':{'type':'integer','minimum':1,'maximum':10000}}, ['product','series','start','end','as_of']),
    'prices.products': obj({'after':TEXT,'limit':{'type':'integer','minimum':1,'maximum':500}}),
    'prices.repair_seller_references': obj({'after':TEXT,'limit':{'type':'integer','minimum':1,'maximum':500}}),
    'prices.repair_seller_knowledge': obj({'source_digest':TEXT}, ['source_digest']),
    'context.public': obj({'kind':TEXT,'product':TEXT,'limit':{'type':'integer','minimum':1,'maximum':500},'after':TEXT}),
    'context.personal': obj({'market_binding':obj({'object_id':TEXT,'version':VERSION,'digest':TEXT}, ['object_id','version','digest'])}, ['market_binding']),
    'evidence.put': obj({'base64':TEXT}, ['base64']),
    'facts.project': obj({'market_binding':obj({'object_id':TEXT,'version':VERSION,'digest':TEXT}, ['object_id','version','digest']),
        'fact_version':{'type':'object','description':'Exact sha256 mapping for all three confirmed local files.'},
        'items':{'type':'array','minItems':1,'maxItems':500,'items':obj({'object_id':TEXT,
            'category':{'type':'string','enum':['POSITIONS','OPERATIONS','TARGETS','PREFERENCES','CONSTRAINTS']},
            'fields':{'type':'object','description':'Confirmed values only. Unknown stays null or UNKNOWN; suggestions and derived risks belong to ALLOCATION.'},
            'source_refs':{'type':'array','minItems':1,'items':obj({'file':TEXT,'line_start':{'type':'integer','minimum':1},
                'line_end':{'type':'integer','minimum':1},'quote':TEXT}, ['file','line_start','line_end','quote'])}},
            ['object_id','category','fields','source_refs'])}}, ['market_binding','fact_version','items']),
}


def validate_object_read(method, params):
    """Enforce the advertised read shapes before phase checks or Python calls."""
    if method not in {'object.get', 'object.list', 'object.history', 'conditions.due', 'context.personal'}:
        return

    def check(value, schema, path):
        expected = schema['type']
        types = expected if isinstance(expected, list) else [expected]
        matches = {'object': isinstance(value, dict), 'string': isinstance(value, str),
                   'integer': type(value) is int, 'boolean': type(value) is bool,
                   'null': value is None}
        if not any(matches[t] for t in types):
            raise ValueError(path + ': expected ' + ' or '.join(types))
        if 'enum' in schema and value not in schema['enum']:
            raise ValueError(path + ': expected ' + ' or '.join(schema['enum']))
        if isinstance(value, str) and len(value) < schema.get('minLength', 0):
            raise ValueError(path + ': expected non-empty string')
        if type(value) is int:
            if ('minimum' in schema and value < schema['minimum']) or ('maximum' in schema and value > schema['maximum']):
                raise ValueError(path + ': integer outside allowed bounds')
        if isinstance(value, dict):
            properties = schema['properties']
            for name in schema['required']:
                if name not in value:
                    raise ValueError(path + '.' + name + ': required ' + properties[name]['type'])
            for name, field in value.items():
                if name not in properties:
                    raise ValueError(path + '.' + name + ': unsupported field; allowed: ' + ', '.join(properties))
                check(field, properties[name], path + '.' + name)

    check(params, PARAMETERS[method], method + '.params')
