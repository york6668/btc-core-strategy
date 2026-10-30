"""Round-trip reading, including long Chinese reports and exact reuse."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from v4 import material_reads
from v4.common import create, read, pointer, Invalid, digest


class ReadingTests(unittest.TestCase):
    def test_value_reference_uses_core_encoding_and_changed_content_still_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / 'context.json'
            value = {'text':'中文', 'fraction':0.015747413950151383}
            create(path, {'baseline': value})
            ref = material_reads.value_reference(path, '/baseline', [root])
            self.assertEqual(ref['$value_ref']['value_hash'], digest(value))
            self.assertEqual(material_reads.resolve_answers(ref, [root], {'sources':[]}, root), value)
            path.write_text('{"baseline":{"changed":true}}')
            with self.assertRaisesRegex(Invalid, 'authored value reference changed'):
                material_reads.resolve_answers(ref, [root], {'sources':[]}, root)
            with self.assertRaisesRegex(Invalid, 'escapes allowed material'):
                material_reads.value_reference(path, '/baseline', [root / 'other'])

    def expand(self, value, material=None):
        if isinstance(value, dict):
            if '$read_ref' in value:
                ref = value['$read_ref']; original = read(ref.get('material', material))
                return pointer(original, ref['pointer']) if ref['pointer'] else original
            if '$report_ref' in value:
                return Path(value['$report_ref']).read_text()
            return {k: self.expand(v, material) for k, v in value.items()}
        if isinstance(value, list):
            return [self.expand(v, material) for v in value]
        return value

    def view(self, index):
        part = next(v for v in index['required'] if v['role'] == 'stage_context')
        return json.loads(''.join(Path(p).read_text() for p in part['parts']))

    def test_lossless_report_and_changed_conditions_reusing_exact_base(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            report = '市场判断与未决限制。\n' * 1900 + '最后一项条件不能省略。'
            first = {'conclusion': {'narrative': report}, 'conditions': ['UNKNOWN'],
                     'sources': [{'published_at': '2026-01-01', 'text': '原始证据' * 900}]}
            create(directory / 'first.json', first)
            base = material_reads.make(directory, 'observe', directory / 'first.json')
            before = read(base)
            self.assertEqual(self.expand(self.view(before)), first)
            reports = [v for v in before['required'] if v['role'] == 'complete_report']
            self.assertEqual(len(reports), 1)
            self.assertEqual(''.join(Path(p).read_text() for p in reports[0]['parts']), report)
            second = dict(first, conditions=['UNKNOWN', 'new risk'])
            create(directory / 'second.json', second)
            result = read(material_reads.make(directory, 'report', directory / 'second.json', base=base))
            self.assertEqual(self.expand(self.view(result)), second)
            self.assertEqual(result['base_read_set'], base)
            self.assertFalse(any(v['role'] == 'complete_report' for v in result['required']))
            self.assertLess(sum(v['characters'] for v in result['required']), len(report) // 3)
            for index in (before, result):
                for entry in index['required']:
                    for path in entry['parts']:
                        self.assertLessEqual(len(Path(path).read_text()), material_reads.PART_CHARACTERS)

    def test_new_report_is_required_and_no_automatic_judgment(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            create(directory / 'a.json', {'conclusion': {'narrative': 'original'}})
            base = material_reads.make(directory, 'observe', directory / 'a.json')
            create(directory / 'b.json', {'conclusion': {'narrative': 'changed'}})
            index = read(material_reads.make(directory, 'report', directory / 'b.json', base=base))
            self.assertTrue(any(v['role'] == 'complete_report' for v in index['required']))
        template = {'action': None, 'conditions': [{'state': None}], '_binding': {'context_hash': 'frozen'}}
        result = material_reads.compose(template, {'conditions': []})
        self.assertIsNone(result['action']); self.assertEqual(result['conditions'], [])
        self.assertEqual(result['_binding'], template['_binding'])
        with self.assertRaises(Invalid):
            material_reads.compose(template, {'_binding': {}})

    def test_required_templates_share_exact_subtrees_with_context(self):
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)
            shared={'refs':['frozen-reference-' + str(i) for i in range(100)],'unknown':'still unresolved'}
            original={'baseline':shared};template={'bindings':shared,'judgement':None}
            create(directory/'context.json',original);create(directory/'template.json',template)
            index=read(material_reads.make(directory,'main',directory/'context.json',required={'template':directory/'template.json'}))
            parts=next(v for v in index['required'] if v['role']=='template')
            view=json.loads(''.join(Path(p).read_text() for p in parts['parts']))
            self.assertIn('$read_ref',view['bindings'])
            self.assertIsNone(view['judgement'])
            self.assertEqual(self.expand(view),template)
            self.assertLess(parts['characters'],len(json.dumps(template)))

    def test_duplicate_subtrees_reference_exact_original_and_keep_changed_limits(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            shared = {'evidence': '原文依据' * 500, 'limits': ['UNKNOWN'],
                      'window': {'start': '2026-09-29', 'end': '2026-09-30'}}
            changed = dict(shared, limits=['UNKNOWN', 'new decisive gap'])
            original = {'first': shared, 'nested': {'repeat': shared, 'changed': changed}}
            create(directory / 'context.json', original)
            index = read(material_reads.make(directory, 'report', directory / 'context.json'))
            view = self.view(index)
            self.assertIn('$read_ref', view['nested']['repeat'])
            self.assertEqual(view['nested']['repeat']['$read_ref']['pointer'], '/first')
            self.assertEqual(view['nested']['changed']['limits'], changed['limits'])
            self.assertEqual(self.expand(view), original)
            self.assertLess(sum(v['characters'] for v in index['required']),
                            len(json.dumps(original, ensure_ascii=False)))

    def test_compact_candidate_keeps_scope_and_changed_conditions_without_repeating_exact_text(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            basis = '同一甲所现货完整窗口，主动成交不是外部净资本。' * 90
            original = {'factor': {'id': 'TEST_A', 'assessment': basis, 'change_reason': '同对象内的简明变化'},
                        'market': {'id': 'TEST_MULTI', 'basis': basis, 'conflicts': '保留重要反证',
                                   'conditions': ['UNKNOWN'], 'window': {'start': '2026-01-01', 'end': '2026-01-02'}},
                        'changed': {'basis': basis, 'conditions': ['UNKNOWN', '新的失效条件']}}
            create(directory / 'candidate.json', original)
            index = read(material_reads.make(directory, 'market-review', directory / 'candidate.json', compact=True))
            view = self.view(index)
            self.assertEqual(index['view_revision'], 4)
            ref = view['market']['basis']['$read_ref']
            self.assertEqual(set(ref), {'pointer'})
            self.assertEqual(pointer(original, ref['pointer']), basis)
            self.assertEqual(self.expand(view, index['material']), original)
            self.assertEqual(view['changed']['conditions'], original['changed']['conditions'])
            self.assertLess(sum(v['characters'] for v in index['required']), len(json.dumps(original, ensure_ascii=False)) // 2)

    def test_compact_reuse_keeps_each_required_document_origin(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            shared = {'evidence': '同一证据，内部空白与限制不能改变。\n  ' * 60,
                      'limits': ['UNKNOWN']}
            template_only = '模板自己的原文\n  仍须精确还原。' * 40
            originals = {
                'context': {'accumulation': shared, 'repeat': shared},
                'template': {'bindings': shared, 'first': template_only,
                             'repeat': template_only, 'judgement': None},
                'other': {'same_template_value': template_only,
                          'limits': ['UNKNOWN', '新的独有条件']},
            }
            paths = {name: directory / (name + '.json') for name in originals}
            for name, value in originals.items():
                create(paths[name], value)
            path = material_reads.make(directory, 'observe', paths['context'],
                required={name: paths[name] for name in ('template', 'other')}, compact=True)
            index = read(path)
            views = {entry['role']: json.loads(''.join(Path(p).read_text() for p in entry['parts']))
                     for entry in index['required']}
            for role, name in (('stage_context', 'context'), ('template', 'template'), ('other', 'other')):
                self.assertEqual(self.expand(views[role], str(paths[name])), originals[name])
            self.assertEqual(set(views['stage_context']['repeat']['$read_ref']), {'pointer'})
            self.assertEqual(views['template']['bindings']['$read_ref']['material'], str(paths['context']))
            self.assertEqual(set(views['template']['repeat']['$read_ref']), {'pointer'})
            self.assertEqual(views['other']['same_template_value']['$read_ref']['material'], str(paths['template']))

            # Existing compact descriptors remain readable without rewriting them.
            legacy = dict(index, view_revision=3)
            legacy_path = Path(path).parent / 'legacy-read-set.json'
            create(legacy_path, legacy)
            before = legacy_path.read_bytes()
            self.assertTrue(material_reads.read_page(legacy_path, 0, 12000)['segments'])
            self.assertEqual(legacy_path.read_bytes(), before)

    def test_reader_returns_all_required_fragments_and_reports_a_real_next_cursor_without_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            original = {'basis': '完整中文材料。' * 1500, 'tail': '末段的重要条件仍须可见。'}
            create(directory / 'candidate.json', original)
            path = material_reads.make(directory, 'market-review', directory / 'candidate.json', compact=True)
            before = {str(p): p.read_bytes() for p in directory.rglob('*') if p.is_file()}
            cursor, chunks = 0, []
            while True:
                page = material_reads.read_page(path, cursor, 333)
                content = ''.join(v['content'] for v in page['segments'])
                self.assertGreater(len(content), 0)
                self.assertLessEqual(len(content), 333)
                self.assertEqual(page['cursor'], cursor)
                self.assertNotIn('approved', page)
                chunks.append(content)
                if page['complete']:
                    self.assertIsNone(page['next_cursor']); break
                self.assertEqual(page['next_cursor'], cursor + len(content))
                cursor = page['next_cursor']
            self.assertEqual(self.expand(json.loads(''.join(chunks)), str(directory / 'candidate.json')), original)
            self.assertIn(original['tail'], ''.join(chunks))
            self.assertEqual(before, {str(p): p.read_bytes() for p in directory.rglob('*') if p.is_file()})
            # A lower budget safely retries the same cursor; it does not skip.
            self.assertEqual(material_reads.read_page(path, 0, 13)['segments'][0]['content'], ''.join(chunks)[:13])

    def test_reader_rejects_changed_material_parts_and_paths_before_returning_content(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            create(directory / 'candidate.json', {'basis': '完整证据及未知限制。' * 400})
            path = material_reads.make(directory, 'market-review', directory / 'candidate.json', compact=True)
            index = read(path); part = Path(index['required'][0]['parts'][0]); original = part.read_bytes()
            part.write_text(part.read_text().replace('证据', '篡改', 1))
            with self.assertRaisesRegex(Invalid, 'reading part content changed'):
                material_reads.read_page(path, 0, 1)
            part.write_bytes(original)
            create(directory / 'other.json', {'changed': True})
            (directory / 'candidate.json').write_bytes((directory / 'other.json').read_bytes())
            with self.assertRaisesRegex(Invalid, 'reading material changed'):
                material_reads.read_page(path)
            with self.assertRaisesRegex(Invalid, 'escapes run reading directory'):
                material_reads.read_page(path, allowed_directory=directory / 'elsewhere')

    def test_reader_retains_legacy_view_and_output_budget_bounds(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            create(directory / 'legacy.json', {'unchanged': '原版完整材料'})
            path = material_reads.make(directory, 'observe', directory / 'legacy.json')
            page = material_reads.read_page(path)
            self.assertTrue(page['complete'])
            self.assertEqual(json.loads(''.join(x['content'] for x in page['segments'])), read(directory / 'legacy.json'))
            for cursor, budget in ((-1, 5), (page['total_characters']+1, 5), (0, 0), (0, 12001)):
                with self.assertRaises(Invalid): material_reads.read_page(path, cursor, budget)
            # Legacy complete reports bind the canonical string hash, unlike
            # a new compact view's raw UTF-8 content hash.
            create(directory / 'report.json', {'report_narrative': '原版完整源报告及全部限制。'})
            report_set = material_reads.make(directory, 'report', directory / 'report.json')
            report_page = material_reads.read_page(report_set)
            self.assertEqual(''.join(x['content'] for x in report_page['segments'] if x['role']=='complete_report'),
                             read(directory / 'report.json')['report_narrative'])


if __name__ == '__main__':
    unittest.main()
