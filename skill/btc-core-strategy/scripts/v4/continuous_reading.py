"""Local, lossless model views of a pinned PUBLIC continuity snapshot.

Storage remains authoritative. Opaque identities and original row order have
an exact directory; every subject, condition, distinct cause/version and opening
time remains in required reading. No node query or resolution is performed.
"""
from pathlib import Path
import hashlib
import json
from datetime import datetime


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load(path):
    return json.loads(Path(path).read_bytes())


def _save(path, value):
    data = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != data:
        raise ValueError('preserve existing reading artifact: ' + str(path))
    if not path.exists():
        path.write_bytes(data)
    return str(path)


def _millis(value):
    return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)


def requirements_view(snapshot, subjects):
    rows = snapshot['requirements']
    causes, times, groups, identifiers = [], [], {}, []
    cause_index, time_index = {}, {}
    cause_keys, cause_key_index = [], {}
    common = {}
    structural = {'requirement_key', 'entity_key', 'cause_key', 'cause_version', 'reason', 'opened_ms'}
    if rows:
        common = {k: v for k, v in rows[0].items() if k not in structural
                  and all(k in row and row[k] == v for row in rows)}
    for ordinal, row in enumerate(rows):
        if not structural <= row.keys():
            raise ValueError('requirement lacks original identity/condition/time fields')
        cause = (row['cause_key'], row['cause_version'])
        if cause not in cause_index:
            if cause[0] not in cause_key_index:
                cause_key_index[cause[0]] = len(cause_keys)
                cause_keys.append(cause[0])
            cause_index[cause] = len(causes)
            causes.append({'cause_key_ref': cause_key_index[cause[0]], 'cause_version': cause[1]})
        if row['opened_ms'] not in time_index:
            time_index[row['opened_ms']] = len(times)
            times.append(row['opened_ms'])
        key = (row['entity_key'], row['reason'])
        group = groups.setdefault(key, {'entity_key': key[0], 'reason': key[1], 'events': {}})
        event = group['events'].setdefault(time_index[row['opened_ms']], [])
        other = {k: v for k, v in row.items() if k not in structural and k not in common}
        event.append((ordinal, cause_index[cause], other))
        identifiers.append(row['requirement_key'])
    ordered, ordinals = [], []
    for group in groups.values():
        events, row_fields = [], {}
        for time_ref, entries in group['events'].items():
            event_index = len(events)
            events.append([time_ref, [entry[1] for entry in entries]])
            row_fields.update({str(event_index) + '/' + str(i): entry[2]
                               for i, entry in enumerate(entries) if entry[2]})
            ordinals.extend(entry[0] for entry in entries)
        ordered.append({**group, 'events': events, 'row_fields': row_fields})
    view = {'schema': 'BTC_PUBLIC_REQUIREMENTS_READING_V2',
            'snapshot_header': {k: v for k, v in snapshot.items() if k != 'requirements'},
            'meaning': 'All original requirements remain unresolved as recorded. Grouping is not review or resolution. '
                       'Indices below are zero-based exact bindings, not severity or approval labels. '
                       'Review every distinct cause/version and opening-time difference against its affected subject; '
                       'the model must explain impact or a specific unresolved limitation, not infer it from grouping.',
            'event_binding': 'Each cause_indices position is one original requirement. '
                             'Its exact original ordinal is in identity_directory.entry_ordinals, '
                             'in group/event/position traversal order. cause_key_ref preserves '
                             'distinct cause identity; the directory maps it to the original opaque hash.',
            'event_columns': ['opened_ms_index', 'cause_indices'],
            'row_field_keys': 'event_index/cause_position within the owning group',
            'row_defaults': common, 'causes': causes, 'opened_ms_values': times,
            'subjects': subjects, 'groups': ordered, 'requirement_count': len(rows),
            'identity_directory_role': 'Opaque requirement/cause keys and original row ordinals only; no conditions, cause versions or clocks omitted.'}
    return view, {'schema': 'BTC_REQUIREMENT_IDENTITIES_V2', 'requirement_keys': identifiers,
                  'cause_keys': cause_keys, 'entry_ordinals': ordinals}


def restore_requirements(view, identities):
    if view['schema'] not in {'BTC_PUBLIC_REQUIREMENTS_READING_V1', 'BTC_PUBLIC_REQUIREMENTS_READING_V2'}:
        raise ValueError('unsupported requirements reading view')
    restored = {}
    offset = 0
    for group in view['groups']:
        if view['schema'] == 'BTC_PUBLIC_REQUIREMENTS_READING_V1':
            entries = [(ordinal, cause_ref, time_ref, group['row_fields'].get(str(ordinal), {}))
                       for ordinal, cause_ref, time_ref in group['entries']]
        else:
            entries = []
            for event_index, (time_ref, cause_indices) in enumerate(group['events']):
                for position, cause_ref in enumerate(cause_indices):
                    ordinal = identities['entry_ordinals'][offset]
                    offset += 1
                    entries.append((ordinal, cause_ref, time_ref,
                                    group['row_fields'].get(str(event_index) + '/' + str(position), {})))
        for ordinal, cause_ref, time_ref, fields in entries:
            if ordinal in restored:
                raise ValueError('duplicate requirement ordinal')
            cause = view['causes'][cause_ref]
            cause_key = (identities['cause_keys'][cause['cause_key_ref']]
                         if 'cause_key_ref' in cause else cause['cause_key'])
            row = dict(view['row_defaults'])
            row.update(requirement_key=identities['requirement_keys'][ordinal],
                       entity_key=group['entity_key'], reason=group['reason'],
                       cause_key=cause_key, cause_version=cause['cause_version'],
                       opened_ms=view['opened_ms_values'][time_ref])
            row.update(fields)
            restored[ordinal] = row
    if set(restored) != set(range(view['requirement_count'])) or len(identities['requirement_keys']) != len(restored):
        raise ValueError('incomplete requirement coverage')
    if view['schema'] == 'BTC_PUBLIC_REQUIREMENTS_READING_V2' and offset != len(identities['entry_ordinals']):
        raise ValueError('incomplete original ordinal coverage')
    return {**view['snapshot_header'], 'requirements': [restored[i] for i in range(len(restored))]}


def build(directory, snapshot_path, native_path, base_read_set, reader, *, supplemental_paths=(), code_files=()):
    directory = Path(directory).resolve()
    snapshot_path, native_path = Path(snapshot_path).resolve(), Path(native_path).resolve()
    snapshot, native = _load(snapshot_path), _load(native_path)
    if snapshot['scope'] != 'PUBLIC':
        raise ValueError('PUBLIC snapshot required')
    indexed = {(o['kind'], o['object_id']): o for o in snapshot['objects']}
    subjects, objects = {}, []
    for item in native:
        obj, requested = item['response']['result'], item['requested']
        identity = (obj['kind'], obj['object_id'])
        pin = indexed.get(identity)
        if (obj['scope'] != 'PUBLIC' or requested['scope'] != 'PUBLIC' or not pin
                or any(obj[k] != pin[k] for k in ('version', 'digest', 'known_ms', 'observed_ms'))
                or any(requested[k] != obj[k] for k in ('kind', 'object_id', 'version', 'digest'))
                or obj['known_ms'] > snapshot['as_of_ms']):
            raise ValueError('native object differs from pinned PUBLIC index')
        subjects[obj['entity_key']] = obj
        objects.append(item)
    if len(objects) != len(indexed) or len(subjects) != len(indexed):
        raise ValueError('not all pinned PUBLIC objects supplied exactly once')
    for path in supplemental_paths:
        item = _load(path)
        obj = item['response']['result']
        request = item['request']['params']
        if (not obj or obj['scope'] != 'PUBLIC' or request['scope'] != 'PUBLIC'
                or any(request[k] != obj[k] for k in ('kind', 'object_id'))
                or _millis(request['as_of']) != snapshot['as_of_ms']
                or obj['known_ms'] > snapshot['as_of_ms']):
            raise ValueError('supplemental subject is not bound to snapshot as_of')
        subjects[obj['entity_key']] = {**obj,
                                       'role': 'SUPPLEMENTAL_AS_OF_SUBJECT_NOT_ADDED_TO_ORIGINAL_ACTIVE_INDEX'}
        objects.append({'request': item['request'], 'response': item['response']})
    missing = sorted({r['entity_key'] for r in snapshot['requirements']} - subjects.keys())
    view, ids = requirements_view(snapshot, subjects)
    view['subject_gaps'] = missing
    if restore_requirements(view, ids) != snapshot:
        raise ValueError('requirements view is not exactly reversible')
    binding = {'snapshot': _hash(snapshot_path), 'native': _hash(native_path),
               'base': _hash(base_read_set),
               'supplemental': [_hash(p) for p in supplemental_paths],
               'code': [_hash(p) for p in code_files], 'requirements_view': 2}
    view_id = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()[:16]
    output = directory / 'public-author' / ('controlled-reading-' + view_id)
    identity_path = _save(output / 'requirement-identities.json', ids)
    view['identity_directory'] = {'path': identity_path, 'sha256': _hash(identity_path)}
    view_path = _save(output / 'requirements-material.json', view)
    native_material = _save(output / 'native-material.json', objects)
    native_reads = reader.make(directory, 'continuous-public-native-view', native_material,
                               base=str(base_read_set), compact=True)
    requirement_reads = reader.make(directory, 'continuous-public-requirements-view', view_path,
                                    base=native_reads, compact=True)
    bindings = {'snapshot': {'path': str(snapshot_path), 'sha256': _hash(snapshot_path)},
                'native': {'path': str(native_path), 'sha256': _hash(native_path)},
                'base_read_set': {'path': str(base_read_set), 'sha256': _hash(base_read_set)},
                'supplemental': [{'path': str(p), 'sha256': _hash(p)} for p in supplemental_paths],
                'code': [{'path': str(p), 'sha256': _hash(p)} for p in code_files]}
    result = {'schema': 'BTC_PUBLIC_CONTINUITY_READING_V1', 'input_bindings': bindings,
              'native_read_set': native_reads, 'requirements_read_set': requirement_reads,
              'identity_directory': identity_path, 'requirements_reconstruction_exact': True,
              'original_requirements': len(snapshot['requirements']), 'subjects': len(subjects),
              'subject_gaps': missing, 'meaning': 'Reading repair only. No market judgment, approval, resolution, expiry extension or state mutation. '
              'Actual complete reading/semantic review remains required. Original run engine and inputs retain their own version.'}
    return _save(output / 'reading-manifest.json', result)
