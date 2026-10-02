"""Bound extractions of confirmed local facts; never a holdings write channel."""
from pathlib import Path

from .codec import Conflict, fingerprint, stamp
from .entities import Entities


NAMES = ('资产配置.md', '操作记录.md', '纠正记录.md')
CATEGORIES = {'POSITIONS', 'OPERATIONS', 'TARGETS', 'PREFERENCES', 'CONSTRAINTS'}


def project(db, *, fact_version, items):
    root = Path(db.settings['project'])
    content = {name: (root / name).read_bytes() for name in NAMES}
    actual = {name: 'sha256:' + fingerprint(raw) for name, raw in content.items()}
    if fact_version != actual:
        raise Conflict('confirmed local files changed; re-read their current versions')
    if not isinstance(items, list) or not 0 < len(items) <= 500:
        raise ValueError('bounded fact extractions required')
    results, objects = [], Entities(db)
    for item in items:
        if set(item) != {'object_id', 'category', 'fields', 'source_refs'} or item['category'] not in CATEGORIES:
            raise ValueError('fact extraction needs its identity, category, fields and literal source references')
        if not isinstance(item['fields'], dict) or not item['source_refs']:
            raise ValueError('explicit fields and original local references required; unknown values stay null or UNKNOWN')
        dependencies = []
        for reference in item['source_refs']:
            if set(reference) != {'file', 'line_start', 'line_end', 'quote'} or reference['file'] not in NAMES:
                raise ValueError('reference must name an authoritative local file')
            lines = content[reference['file']].decode('utf-8').splitlines()
            start, end = reference['line_start'], reference['line_end']
            if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
                raise ValueError('invalid source line range')
            if not reference['quote'].strip() or reference['quote'] not in '\n'.join(lines[start-1:end]):
                raise Conflict('extraction does not match the confirmed source lines')
            dependencies.append({'type': 'BLOB', 'digest': db.put_blob(content[reference['file']]), 'relation': 'CONFIRMED_FILE'})
            parent = objects.get('FACT_PROJECTION', reference['file'])
            if parent:
                dependencies.append({'type': 'ENTITY', 'kind': 'FACT_PROJECTION', 'object_id': reference['file'],
                                     'version': parent['version'], 'relation': 'FACT_VERSION'})
        kind = 'PROFILE' if item['category'] in {'TARGETS', 'PREFERENCES', 'CONSTRAINTS'} else 'FACT_PROJECTION'
        object_id = 'confirmed:' + item['category'] + ':' + item['object_id']
        previous = objects.get(kind, object_id)
        when = stamp()
        spec = {'kind': kind, 'object_id': object_id, 'scope': 'PERSONAL', 'observed_at': when, 'known_at': when,
                'method_version': 'CONFIRMED_FILE_EXTRACTION_V1', 'source_ref': 'LOCAL_CONFIRMED_FILES',
                'change_reason': 'structured view of the exact confirmed file version',
                'payload': {**item, 'fact_version': actual, 'status': 'EXTRACTED_VIEW',
                            'authority': 'CONFIRMED_LOCAL_FILES', 'derived_values': False},
                'dependencies': dependencies}
        results.append(objects.put(spec, expected_version=previous['version'] if previous else 0, trusted_projection=True))
    if any((root / name).read_bytes() != raw for name, raw in content.items()):
        raise Conflict('local fact generation changed during extraction')
    return {'objects': results, 'fact_version': actual,
            'meaning': 'A source-bound extraction is a view; it never modifies confirmed files or records a fill.'}
