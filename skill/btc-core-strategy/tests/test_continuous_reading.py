import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from v4 import continuous_reading as views, material_reads


class PublicContinuityReadingTests(unittest.TestCase):
    def test_every_row_cause_version_clock_and_unique_condition_round_trips(self):
        rows = [dict(requirement_key='r' + str(i), entity_key='subject', cause_key='cause',
                     cause_version=i % 2 + 1, reason='same actual dependency condition',
                     opened_ms=100 + i, resolved_ms=None, resolution_ref=None,
                     unique_condition={'limit': i}) for i in range(4)]
        rows[3]['resolved_ms'] = 250
        original = {'scope': 'PUBLIC', 'as_of_ms': 300, 'objects': [], 'requirements': rows}
        value, identifiers = views.requirements_view(original, {})
        self.assertEqual(views.restore_requirements(value, identifiers), original)
        self.assertEqual(len(value['causes']), 2)
        self.assertEqual(value['opened_ms_values'], [100, 101, 102, 103])
        self.assertEqual(value['groups'][0]['row_fields']['3']['unique_condition'], {'limit': 3})
        self.assertEqual(value['groups'][0]['row_fields']['3']['resolved_ms'], 250)
        self.assertEqual(set(identifiers), {'schema', 'requirement_keys'})
        value['groups'][0]['entries'].pop()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            views.restore_requirements(value, identifiers)

    def make_inputs(self, p):
        def obj(name, active=True):
            return dict(kind='REGION', object_id=name, scope='PUBLIC', product='BTC',
                        version=2, digest='digest-' + name, known_ms=90, observed_ms=80,
                        active=active, first_identified_ms=10, review_at=None, valid_until='old deadline',
                        source_ref='original public source', method_version='fixed', entity_key=name,
                        payload={'condition': 'Actual original condition ' * 60}, dependencies=[])
        active, retired = obj('active'), obj('retired', False)
        pin = {k: active[k] for k in ('kind', 'object_id', 'scope', 'product', 'version', 'digest', 'known_ms', 'observed_ms')}
        snapshot = {'scope': 'PUBLIC', 'as_of_ms': 100, 'objects': [pin], 'requirements': [
            dict(requirement_key='original-obligation', entity_key='retired', cause_key='original-cause',
                 cause_version=12, reason='Original retired subject still requires review', opened_ms=95,
                 resolved_ms=None, resolution_ref=None)]}
        native = [{'requested': pin, 'response': {'ok': True, 'result': active}}]
        supplemental = {'request': {'method': 'object.get', 'params': {'scope': 'PUBLIC', 'kind': 'REGION',
                        'object_id': 'retired', 'as_of': '1970-01-01T00:00:00.100Z'}},
                        'response': {'ok': True, 'result': retired},
                        'routing_identity_only_current': {'active': True, 'version': 999}}
        def save(name, value):
            f = p / name
            f.write_text(json.dumps(value))
            return f
        snap, nat, sup = save('snapshot.json', snapshot), save('native.json', native), save('supplement.json', supplemental)
        base_material = save('base.json', {'prior_public_value': active['payload']})
        base = material_reads.make(p, 'observe', base_material)
        return snap, nat, sup, base

    def test_retired_subject_is_original_as_of_not_current_routing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            snap, nat, sup, base = self.make_inputs(p)
            original = snap.read_bytes(), nat.read_bytes()
            manifest = views.build(p, snap, nat, base, material_reads, supplemental_paths=[sup])
            result = json.loads(Path(manifest).read_text())
            self.assertEqual(result['subject_gaps'], [])
            projected = json.loads((p / 'public-author/controlled-reading/requirements-material.json').read_text())
            self.assertFalse(projected['subjects']['retired']['active'])
            self.assertEqual(projected['subjects']['retired']['version'], 2)
            self.assertEqual(projected['subjects']['retired']['valid_until'], 'old deadline')
            identities = json.loads(Path(result['identity_directory']).read_text())
            self.assertEqual(views.restore_requirements(projected, identities), json.loads(snap.read_text()))
            for key in ['native_read_set', 'requirements_read_set']:
                cursor, contents = 0, ''
                while True:
                    page = material_reads.read_page(result[key], cursor, 113, allowed_directory=p / 'reading')
                    contents += ''.join(segment['content'] for segment in page['segments'])
                    if page['complete']:
                        break
                    cursor = page['next_cursor']
                self.assertTrue(contents)
            self.assertEqual((snap.read_bytes(), nat.read_bytes()), original)

    def test_missing_retired_subject_remains_explicit_gap_with_all_obligations(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            snap, nat, _, base = self.make_inputs(p)
            m = json.loads(Path(views.build(p, snap, nat, base, material_reads)).read_text())
            self.assertEqual(m['subject_gaps'], ['retired'])
            self.assertEqual(m['original_requirements'], 1)

    def test_wrong_as_of_and_private_supplement_cannot_enter_public_view(self):
        for field, value in [('as_of', '1970-01-01T00:00:01Z'), ('scope', 'PERSONAL')]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp)
                snap, nat, sup, base = self.make_inputs(p)
                data = json.loads(sup.read_text())
                data['request']['params'][field] = value
                sup.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, 'snapshot as_of'):
                    views.build(p, snap, nat, base, material_reads, supplemental_paths=[sup])


if __name__ == '__main__':
    unittest.main()
