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


def make(directory, stage, material, base=None, required=None, on_demand=None, *, compact=False):
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
    revision = 4 if compact else 2
    identity = {'view_revision': revision, 'value': value, 'material': str(material), 'base': base,
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
        reusable = isinstance(child, (dict, list, str)) if compact else isinstance(child, (dict, list))
        if reusable and len(json.dumps(child, ensure_ascii=False)) >= (120 if compact else 800):
            if key in seen:
                first = seen[key]
                reference = {'$read_ref': ({'pointer': first['pointer']}
                    if compact and first['material'] == origin else first)}
                # Only exact repeated bytes are reused. Short local pointers
                # retain every parent object and all differing conditions.
                if not compact or len(json.dumps(child, ensure_ascii=False)) > len(json.dumps(reference, ensure_ascii=False)) + 32:
                    return reference
            seen.setdefault(key, {'read_set': str(target / 'read-set.json'),
                                  'material': origin, 'pointer': path})
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

    formatting = {'separators': (',', ':')} if compact else {'indent': 2}
    body = json.dumps(view(value), ensure_ascii=False, **formatting) + '\n'
    reads = [{'role': 'stage_context', 'parts': _parts(target, 'context', body), 'characters': len(body)}]
    if compact:
        reads[0]['content_hash'] = digest(body.encode('utf-8'))
    required_reads = []
    for role, path in (required or {}).items():
        content = (json.dumps(view(read(path), origin=str(path)), ensure_ascii=False, **formatting) + '\n'
                   if Path(path).suffix == '.json' else Path(path).read_text(encoding='utf-8'))
        required_reads.append({'role': role, 'parts': _parts(target, role, content), 'characters': len(content)})
        if compact:
            required_reads[-1]['content_hash'] = digest(content.encode('utf-8'))
    reads.extend(report_reads.values())
    reads.extend(required_reads)
    result = {'schema': 'BTC_STAGE_READ_SET_V1', 'view_revision': revision, 'stage': stage,
              'material': str(material), 'material_hash': digest(value), 'base_read_set': base,
              'instructions': 'Read all required parts in order, batching only within output limits. '
                  'Parts concatenate exactly; a part is not a complete document. '
                  'References reuse exact content, not prior judgments. '
                  'In a new conversation read the base read_set first. Paths do not prove review.' +
                  (' A pointer-only $read_ref reuses the exact earlier value in this material; '
                   'retain its owning object when interpreting or extracting it. Use run-read and '
                   'follow next_cursor until complete; complete means all content emitted, not semantic approval.' if compact else ''),
              'required': reads, 'on_demand': on_demand or {}}
    return _save(target / 'read-set.json', result)


def read_page(read_set, cursor=0, max_characters=12000, *, allowed_directory=None):
    """Return bounded actual content, not selected judgments or a read receipt.

    Cursor covers all required documents and parts, including final fragments.
    No persistent state is changed; output completion cannot approve semantics.
    """
    read_set = Path(read_set).resolve()
    allowed = Path(allowed_directory).resolve() if allowed_directory else read_set.parent.parent
    require(allowed in read_set.parents, 'read set escapes run reading directory')
    index = read(read_set)
    require(index.get('schema') == 'BTC_STAGE_READ_SET_V1', 'invalid read set')
    material = Path(index['material']).resolve()
    require(allowed.parent in material.parents, 'reading material escapes run directory')
    require(digest(read(material)) == index['material_hash'], 'reading material changed')
    require(isinstance(cursor, int) and cursor >= 0, 'invalid reading cursor')
    require(isinstance(max_characters, int) and 0 < max_characters <= 12000, 'reading output budget must be 1..12000 characters')
    entries, total = [], 0
    for document in index['required']:
        texts = []
        for name in document['parts']:
            path = Path(name).resolve()
            require(allowed in path.parents, 'reading part escapes run reading directory')
            content = path.read_text(encoding='utf-8')
            texts.append(content)
            entries.append((document['role'], str(path), total, content))
            total += len(content)
        joined = ''.join(texts)
        require(len(joined) == document['characters'], 'reading part length changed')
        if document.get('content_hash'):
            actual_hash = digest(joined) if document['role'] == 'complete_report' else digest(joined.encode('utf-8'))
            require(actual_hash == document['content_hash'], 'reading part content changed')
    require(cursor <= total, 'reading cursor exceeds required material')
    end = min(total, cursor + max_characters)
    segments = []
    for role, path, start, content in entries:
        left, right = max(cursor, start), min(end, start + len(content))
        if left < right:
            segments.append({'role': role, 'part': path, 'start': left-start, 'end': right-start,
                             'content': content[left-start:right-start]})
    return {'read_set': str(read_set), 'material': str(material), 'material_hash': index['material_hash'],
            'base_read_set': index.get('base_read_set'), 'cursor': cursor,
            'next_cursor': end if end < total else None, 'complete': end == total,
            'total_characters': total, 'segments': segments,
            'recommended_max_output_tokens': 16000,
            'next': 'Read the returned content. If tool output was truncated, repeat this cursor with a smaller budget. '
                    'Follow next_cursor until complete, then perform the actual candidate review; output completion is not approval.'}


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


def value_reference(path, location, roots):
    """Compute the existing canonical hash; return no material or judgement."""
    path = Path(path).resolve()
    require(any(path == Path(root).resolve() or Path(root).resolve() in path.parents for root in roots),
            'authored value reference escapes allowed material')
    value = pointer(read(path), location)
    return {'$value_ref': {'file': str(path), 'pointer': location, 'value_hash': digest(value)}}


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
