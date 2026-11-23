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
    'schedule.status': obj({}),
    'schedule.window': obj({}),
    'schedule.tick': obj({}),
    'schedule.claim': obj({'attempt_id':TEXT}, ['attempt_id']),
    'task.status': obj({'task_id':TEXT}),
    'task.submit': obj({'task_id':TEXT,'issue_id':TEXT,'kind':TEXT,'phase':TEXT,
        'input_digest':TEXT,'release_id':TEXT,'evidence_ref':TEXT,'reason':TEXT,
        'reason_kind':TEXT,'scope':TEXT,'due_ms':VERSION,'namespace':TEXT},
        ['task_id','issue_id','kind','phase','input_digest','release_id','evidence_ref',
         'reason','reason_kind','scope','due_ms']),
    'task.assess': obj({'task_id':TEXT,'dependency_ready':{'type':'boolean'},
        'dependency_ref':TEXT}, ['task_id','dependency_ready','dependency_ref']),
    'task.start': obj({'task_id':TEXT,'attempt_id':TEXT,'release_id':TEXT,
        'claim_id':TEXT,'model':TEXT}, ['task_id','attempt_id','release_id']),
    'task.finish': obj({'task_id':TEXT,'attempt_id':TEXT,
        'outcome':{'type':'string','enum':['COMPLETE','FAILED','UNKNOWN']},
        'completion_ref':TEXT,'failure_kind':TEXT,'failure_ref':TEXT},
        ['task_id','attempt_id','outcome','completion_ref']),
    'task.reconcile': obj({'task_id':TEXT,'attempt_id':TEXT,
        'outcome':{'type':'string','enum':['COMPLETE','NOT_EXECUTED','UNKNOWN']},
        'evidence_ref':TEXT,'completion_ref':TEXT},
        ['task_id','attempt_id','outcome','evidence_ref']),
    'task.repair': obj({'task_id':TEXT,'input_digest':TEXT,'release_id':TEXT,
        'evidence_ref':TEXT,'verification_ref':TEXT,'reason':TEXT},
        ['task_id','input_digest','release_id','evidence_ref','verification_ref','reason']),
    'model.deep.status': obj({}),
    'model.defect.check': obj({'scope':TEXT}, ['scope']),
    'model.deep.claim': obj({'claim_id':TEXT,'issue_id':TEXT,'scope':TEXT,'phase':TEXT,'input_digest':TEXT,'model':TEXT},
                             ['claim_id','issue_id','scope','phase','input_digest']),
    'model.deep.finish': obj({'claim_id':TEXT,'outcome':{'type':'string','enum':['COMPLETE','FAILED','UNKNOWN','CANCELED']},
        'result_ref':TEXT,'failure_kind':TEXT,'failure_ref':TEXT},['claim_id','outcome']),
    'model.deep.reconcile': obj({'claim_id':TEXT,'outcome':{'type':'string','enum':['COMPLETE','FAILED','CANCELED']},
        'evidence_ref':TEXT,'result_ref':TEXT},['claim_id','outcome','evidence_ref']),
    'model.defect.open': obj({'scope':TEXT,'kind':TEXT,'signature':TEXT,'fault_digest':TEXT,'evidence_ref':TEXT,
                              'impact_ref':TEXT,'cause_ref':TEXT},
                              ['scope','kind','signature','fault_digest','evidence_ref']),
    'model.defect.resolve': obj({'fault_id':TEXT,'repair_digest':TEXT,'verification_ref':TEXT,'verification_status':TEXT,
                                 'correction_ref':TEXT},
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
    'acceptance.repair': obj({'defect_index':VERSION,'release':TEXT,'reason':TEXT,
        'evidence_refs':{'type':'array','minItems':1,'items':{'type':'object'}},
        'preserve_window':{'type':'boolean','description':'Retain the original observation window. Required when recording verified and remaining scope under one defect.'},
        'verified_scope':{'type':'array','minItems':1,'items':TEXT,'description':'Named portions verified by the referenced actual evidence; a locked real-input content review is allowed when applicable.'},
        'remaining_scope':{'type':'array','items':TEXT,'description':'Named portions of the original defect not yet verified. They remain open under the same defect index.'}},
        ['defect_index','release','reason','evidence_refs']),
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

OBJECT = {'type': 'object'}
OBJECTS = {'type': 'array', 'items': OBJECT}
STRINGS = {'type': 'array', 'items': TEXT}
CONDITION_LOOKUP = obj({'target': OBJECT, 'condition_id': TEXT}, ['target', 'condition_id'])
DECISION_RESULT = {
    'type': 'object',
    'description': 'Decision with conditions [{id,criterion,review_at}]. For a bounded forecast supply '
                   'observation_end. A changed condition may cite predecessor {target,condition_id,reason}; '
                   'retired_conditions uses the same references for deliberately ended questions.',
}
MARKET_RESULT = {
    **DECISION_RESULT,
    'description': DECISION_RESULT['description'] + ' Mark named current-price assessments in valuations. '
                   'Each entry may specify a value_zone and condition_ids. Spot decisions cite the applicable '
                   'valuation/<name> or overall value.',
}
PERSONAL_RESULT = {
    **DECISION_RESULT,
    'description': DECISION_RESULT['description'] + ' For an existing-position short that may still be open, '
                   'give the economic basis for REDUCE, KEEP and INCREASE in direction_comparison.',
}
PERSONAL_DECISION_FIELDS = (
    {'subject': TEXT, 'expected_version': VERSION, 'policy_basis': TEXT,
     'facts': OBJECT, 'fact_refs': OBJECTS, 'result': PERSONAL_RESULT, 'evidence': OBJECTS,
     'market_refs': STRINGS, 'entry_reference': TEXT,
     'notification': {'type': 'object', 'description': 'Use scope=DIRECTION only for an ordinary update of the '
                      'original short direction. A due time, independent material risk or correction has its own meaning.'}},
    ['subject', 'expected_version', 'policy_basis', 'facts', 'fact_refs', 'market_refs', 'result', 'evidence'],
)
TRADING_FIELDS = {
    'open': ({'market': OBJECT}, ['market']),
    'public': ({'condition': CONDITION_LOOKUP}, []),
    'personal_ready': ({}, []),
    'personal': ({'condition': CONDITION_LOOKUP}, []),
    'seed_policy': ({}, []),
    'policy': ({'subject':TEXT, 'kind':TEXT, 'fields':OBJECT, 'refs':OBJECTS, 'expected_version':VERSION,
                'active':{'type':'boolean'}}, ['subject','kind','fields','refs','expected_version']),
    'feedback': ({'decision_id': TEXT, 'before_report_id': TEXT, 'writing_inputs': {'type': 'boolean'},
                  'risk_seed_inputs': {'type': 'boolean'}}, []),
    'inherit_feedback': ({'retain': OBJECTS}, []),
    'assess_feedback': ({'outcome': TEXT, 'reason': TEXT, 'evidence': OBJECTS}, ['outcome','reason','evidence']),
    'source': ({'digest': TEXT}, ['digest']),
    'research_archive': ({'scope': SCOPE, 'request_id': TEXT, 'phase': TEXT, 'evidence_id':TEXT},
                         ['scope','request_id','phase']),
    'research_result': ({'object_id': TEXT, 'version': VERSION, 'digest': TEXT}, ['object_id','version','digest']),
    'evidence': ({'source': TEXT, 'url': TEXT, 'observed_at': TIME, 'retrieved_at': TIME, 'content': TEXT},
                 ['source', 'url', 'observed_at', 'retrieved_at', 'content']),
    'market': ({'evidence': OBJECTS, 'result': MARKET_RESULT, 'expected_version': VERSION,
                'market': OBJECT, 'reason': TEXT, 'notification': OBJECT}, ['evidence']),
    'personal_decision': PERSONAL_DECISION_FIELDS,
    'portfolio_decision': (
        {'expected_version': VERSION, 'market': OBJECT, 'primary': TEXT, 'alternatives': STRINGS,
         'interactions': {'type': 'array', 'items': obj(
             {'subject': TEXT, 'depends_on': TEXT, 'reason': TEXT}, ['subject', 'depends_on', 'reason'])},
         'member_reviews': {'type': 'object', 'additionalProperties': obj({
             'relation': {'type': 'string', 'enum': ['CONTINUE', 'REPRICE', 'REVISE', 'NEW', 'UNRESOLVED']},
             'reason': TEXT}, ['relation', 'reason'])},
         'decisions': {'type': 'array', 'items': obj(*PERSONAL_DECISION_FIELDS)}, 'reuse': OBJECT},
        ['expected_version', 'market', 'primary', 'alternatives', 'interactions', 'decisions', 'reuse'],
    ),
    'review': ({'target': OBJECT, 'condition_id': TEXT, 'outcome': TEXT, 'explanation': TEXT, 'evidence': OBJECTS,
                'impact': TEXT, 'next_review_at': TIME}, ['target', 'condition_id', 'outcome', 'explanation', 'evidence']),
    'research': ({'scope': SCOPE, 'subject': TEXT, 'question': TEXT, 'evidence': OBJECTS,
                  'expected_version': VERSION, 'outcome': OBJECT},
                 ['scope', 'subject', 'question', 'evidence', 'expected_version']),
    'report': ({'purpose': TEXT, 'event_ref': TEXT,
                'base_report_id': {'type': ['string', 'null'], 'description': 'The structured baseline from trading.feedback, or null on first use.'},
                'assessed_at': TIME,
                'changes': {'type': 'object', 'description': 'Changed entries by module and subject. Seed all six '
                            'modules on the first regular report. The original short direction is filled by the service.'},
                'pending_review': STRINGS,
                'reviewed_unchanged': STRINGS,
                'market_sources': OBJECT,
                'public_seed': obj({'market': OBJECT, 'sections': OBJECT}, ['market', 'sections']),
                'complete': {'type': 'boolean'},
                'valid_until': TIME, 'decisions': OBJECTS, 'reason': TEXT, 'obligation_ids': STRINGS,
                'supersedes': OBJECT, 'legacy_coverage': OBJECT, 'decision_coverage': OBJECT},
               ['purpose', 'base_report_id', 'assessed_at', 'changes', 'decisions', 'reason']),
    'begin_delivery': ({'report_id': TEXT, 'anchor_ref': TEXT, 'expected_destination': OBJECT},
                       ['report_id', 'anchor_ref', 'expected_destination']),
    'record_delivery': ({'report_id': TEXT, 'action': TEXT, 'receipt': OBJECT}, ['report_id', 'action', 'receipt']),
}
TRADING_FIELDS['preview_report'] = TRADING_FIELDS['report']
PARAMETERS.update({'trading.' + action: obj({'run_ref': TEXT, **fields}, ['run_ref', *required])
                   for action, (fields, required) in TRADING_FIELDS.items()})
PARAMETERS['trading.set_entry_reference'] = obj(
    {'price': TEXT, 'confirmed_at': TIME, 'confirmation_ref': TEXT},
    ['price', 'confirmed_at', 'confirmation_ref'],
)


def validate_object_read(method, params):
    """Enforce the advertised read shapes before phase checks or Python calls."""
    if (not method.startswith('trading.')
            and method not in {'object.get', 'object.list', 'object.history', 'conditions.due', 'context.personal'}):
        return

    def check(value, schema, path):
        expected = schema['type']
        types = expected if isinstance(expected, list) else [expected]
        matches = {'object': isinstance(value, dict), 'string': isinstance(value, str),
                   'integer': type(value) is int, 'boolean': type(value) is bool,
                   'null': value is None, 'array': isinstance(value, list)}
        if not any(matches[t] for t in types):
            raise ValueError(path + ': expected ' + ' or '.join(types))
        if 'enum' in schema and value not in schema['enum']:
            raise ValueError(path + ': expected ' + ' or '.join(schema['enum']))
        if isinstance(value, str) and len(value) < schema.get('minLength', 0):
            raise ValueError(path + ': expected non-empty string')
        if type(value) is int:
            if ('minimum' in schema and value < schema['minimum']) or ('maximum' in schema and value > schema['maximum']):
                raise ValueError(path + ': integer outside allowed bounds')
        if isinstance(value, list) and 'items' in schema:
            for index, item in enumerate(value):
                check(item, schema['items'], path + '[' + str(index) + ']')
        if isinstance(value, dict) and 'properties' in schema:
            properties = schema['properties']
            for name in schema['required']:
                if name not in value:
                    raise ValueError(path + '.' + name + ': required ' + properties[name]['type'])
            for name, field in value.items():
                if name not in properties:
                    raise ValueError(path + '.' + name + ': unsupported field; allowed: ' + ', '.join(properties))
                check(field, properties[name], path + '.' + name)

    check(params, PARAMETERS[method], method + '.params')
