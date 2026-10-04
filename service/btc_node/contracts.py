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
