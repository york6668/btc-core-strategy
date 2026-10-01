"""Lossless, bounded reading views; these files never own strategy or judgments."""
from pathlib import Path
import json

from .common import create, digest, read, require, pointer


PART_CHARACTERS = 6000


def _save(path, value):
    path = Path(path)
    if path.exists():
        actual = path.read_bytes() if isinstance(value, bytes) else read(path)
        require(actual == value, 'reading material changed: ' + path.name)
    else:
        create(path, value)
    return str(path)


def _parts(directory, name, body):
    """Split at line boundaries when possible; concatenation reproduces every byte."""
    pieces = []
    remaining = body
    while remaining:
        end = min(len(remaining), PART_CHARACTERS)
        if end < len(remaining):
            newline = remaining.rfind('\n', end // 2, end)
            if newline >= 0:
                end = newline + 1
        pieces.append(remaining[:end])
        remaining = remaining[end:]
    if not pieces:
        pieces = ['']
    return [_save(directory / (name + '-%03d.txt' % (i + 1)), piece.encode('utf-8'))
            for i, piece in enumerate(pieces)]


def _walk(value, path=''):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, path + '/' + key.replace('~', '~0').replace('/', '~1'))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from _walk(child, path + '/' + str(i))


def make(directory, stage, material, base=None, required=None, on_demand=None):
    """Reference exact unchanged subtrees of this run's initial reading set.

    A new conversation reads the base first. Existing readers can read only the
    current parts, following references when needed. No semantic summarization.
    """
    directory, material = Path(directory), Path(material)
    value = read(material)
    known = {}
    cursor, visited = base, set()
    while cursor:
        require(cursor not in visited, 'cyclic base reading material')
        visited.add(cursor)
        prior = read(cursor)
        prior_value = read(prior['material'])
        require(digest(prior_value) == prior['material_hash'], 'base reading material changed')
        for path, child in _walk(prior_value):
            if len(json.dumps(child, ensure_ascii=False)) >= 800:
                known.setdefault(digest(child), {'read_set': str(cursor), 'material': prior['material'],
                                                'pointer': path})
        cursor = prior.get('base_read_set')
    identity = {'view_revision': 2, 'value': value, 'material': str(material), 'base': base,
                'required': {k: digest(Path(p).read_bytes()) for k, p in (required or {}).items()},
                'on_demand': on_demand or {}}
    target = directory / 'reading' / (stage + '-' + digest(identity)[7:23])
    report_reads = {}
    seen = {}

    def view(child, path='', origin=None):
        origin = origin or str(material)
        key = digest(child)
        if key in known:
            return {'$read_ref': known[key]}
        if isinstance(child, (dict, list)) and len(json.dumps(child, ensure_ascii=False)) >= 800:
            if key in seen:
                return {'$read_ref': seen[key]}
            seen[key] = {'read_set': str(target / 'read-set.json'),
                         'material': origin, 'pointer': path}
        if isinstance(child, str) and (path.endswith('/conclusion/narrative') or path.endswith('/report_narrative')) and child:
            report_dir = directory / 'reading' / 'reports' / key[7:]
            full = _save(report_dir / 'full.txt', child.encode('utf-8'))
            if key not in report_reads:
                report_reads[key] = {'role': 'complete_report', 'content_hash': key,
                                     'parts': _parts(report_dir, 'part', child), 'characters': len(child),
                                     'read_when': 'Not already read in this conversation, or content changed.'}
            return {'$report_ref': full, 'content_hash': key}
        if isinstance(child, dict):
            return {k: view(v, path + '/' + k.replace('~', '~0').replace('/', '~1'), origin) for k, v in child.items()}
        if isinstance(child, list):
            return [view(v, path + '/' + str(i), origin) for i, v in enumerate(child)]
        return child

    body = json.dumps(view(value), ensure_ascii=False, indent=2) + '\n'
    reads = [{'role': 'stage_context', 'parts': _parts(target, 'context', body), 'characters': len(body)}]
    required_reads = []
    for role, path in (required or {}).items():
        content = (json.dumps(view(read(path), origin=str(path)), ensure_ascii=False, indent=2) + '\n'
                   if Path(path).suffix == '.json' else Path(path).read_text(encoding='utf-8'))
        required_reads.append({'role': role, 'parts': _parts(target, role, content), 'characters': len(content)})
    reads.extend(report_reads.values())
    reads.extend(required_reads)
    result = {'schema': 'BTC_STAGE_READ_SET_V1', 'view_revision': 2, 'stage': stage,
              'material': str(material), 'material_hash': digest(value), 'base_read_set': base,
              'instructions': 'Read all required parts in order, batching only within output limits. '
                  'Parts concatenate exactly; a part is not a complete document. '
                  'References reuse exact content, not prior judgments. '
                  'In a new conversation read the base read_set first. Paths do not prove review.',
              'required': reads, 'on_demand': on_demand or {}}
    return _save(target / 'read-set.json', result)


def compose(template, answers):
    """Only merge authored fields; do not inherit any previous run's decisions."""
    require(isinstance(template, dict) and isinstance(answers, dict), 'template and answers must be objects')
    require(not any(k.startswith('_') for k in answers), 'answers cannot replace template bindings')

    def merge(original, authored):
        if isinstance(original, dict) and isinstance(authored, dict):
            out = dict(original)
            for key, value in authored.items():
                out[key] = merge(original.get(key), value)
            return out
        # Arrays are complete authored replacements, never index-wise mergers.
        return authored

    return merge(template, answers)


def resolve_answers(value, roots, package, evidence_dir):
    """Resolve explicit authored references, without generating any judgement.

    A value reference carries its expected hash. Raw source/pointer pairs may
    omit only the mechanical hash; an existing hash is never silently repaired.
    """
    from copy import deepcopy
    from . import evidence
    roots = [Path(root).resolve() for root in roots]
    files = {}
    sources = {source['id']: source for source in package['sources']}

    def resolve(item):
        if isinstance(item, list):
            return [resolve(child) for child in item]
        if not isinstance(item, dict):
            return item
        if set(item) == {'$value_ref'}:
            ref = item['$value_ref']
            require(isinstance(ref, dict) and set(ref) == {'file', 'pointer', 'value_hash'}, 'invalid authored value reference')
            path = Path(ref['file']).resolve()
            require(any(path == root or root in path.parents for root in roots), 'authored value reference escapes allowed material')
            if path not in files:
                files[path] = read(path)
            result = pointer(files[path], ref['pointer'])
            require(digest(result) == ref['value_hash'], 'authored value reference changed')
            return deepcopy(result)
        if set(item) in ({'source_id', 'pointer'}, {'source_id', 'pointer', 'value_hash'}):
            require(item['source_id'] in sources, 'unknown current raw source')
            source = sources[item['source_id']]
            path = Path(evidence_dir) / 'raw' / (source['raw_hash'][7:] + '.json')
            if path not in files:
                files[path] = read(path)
            ref = dict(item)
            ref.setdefault('value_hash', digest(pointer(files[path], item['pointer'])))
            evidence.resolve(package, evidence_dir, ref)
            return ref
        return {key: resolve(child) for key, child in item.items()}

    return resolve(value)
