"""MCP stdio adapter; domain calls use the very same dispatcher as CLI."""
from __future__ import annotations

import json
import sys

from .api import READS, WRITES
from .codec import encode
from .contracts import PARAMETERS


DESCRIPTIONS = {
    'context.public': 'Prepare public objects and outstanding conditions before market judgement. Personal data is withheld.',
    'market.freeze': 'Freeze the independent market judgement with version and evidence binding before personal adaptation.',
    'context.personal': 'Get confirmed fact projections, portfolio and outstanding reviews using an already frozen market binding.',
    'prices.query': 'Query one exact product and unit in an event window using only information known at as_of.',
    'object.get': 'Continue a region, retest, hypothesis, plan, research issue or delivery by stable identity and optional historical version.',
    'object.put': 'Commit an evidence-bound business judgement with expected_version. Does not infer fills, confirmations or market meaning.',
    'conditions.due': 'List conditions due by time or changed dependencies, including objects absent from reports.',
    'review.resolve': 'Resolve an affected condition using a stored, current, explicitly bound REVIEW.',
    'storage.status': 'Read logical content size, unique content bytes and retention/maintenance status.',
    'failure.get': 'Read a fault receipt and its explicit versioned resolution. Age and later runs do not imply closure.',
    'failure.list': 'List operational fault identities and explicit closure state, including retained closure records after process-file expiry.',
    'failure.resolve': 'Bind an actual successful recovery operation to the exact fault receipt version; retain its process file for 14 days after confirmation, with evidence/dependency protection.',
}


def run(settings):
    from .__main__ import answer, call
    for line in sys.stdin.buffer:
        request = {}
        try:
            request = json.loads(line)
            method = request['method']
            if 'id' not in request:
                continue
            if method == 'initialize':
                result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'btc-node', 'version': '1.0.0'}}
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': [{'name': name.replace('.', '_'),
                         'description': DESCRIPTIONS.get(name, name + ' through the BTC data service'),
                         'inputSchema': {'type': 'object', 'properties': {'params': PARAMETERS[name],
                                          'operation_id': {'type': 'string'}}, 'required': ['params'] +
                                         (['operation_id'] if name in WRITES else []), 'additionalProperties': False}}
                         for name in sorted(READS | WRITES)]}
            elif method == 'tools/call':
                given = request['params']
                names = {name.replace('.', '_'): name for name in READS | WRITES}
                rpc = {'method': names[given['name']], **given.get('arguments', {})}
                response = answer(settings, rpc) if settings.get('direct') else call(settings['socket'], rpc)
                result = {'content': [{'type': 'text', 'text': json.dumps(response, ensure_ascii=False)}],
                          'isError': not response['ok']}
            else:
                raise ValueError('unsupported MCP method')
            outgoing = {'jsonrpc': '2.0', 'id': request['id'], 'result': result}
        except (ValueError, KeyError, TypeError):
            outgoing = {'jsonrpc': '2.0', 'id': request.get('id'),
                        'error': {'code': -32602, 'message': 'invalid method or arguments'}}
        sys.stdout.buffer.write(encode(outgoing))
        sys.stdout.buffer.flush()
