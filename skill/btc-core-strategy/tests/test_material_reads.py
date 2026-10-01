"""Round-trip reading, including long Chinese reports and exact reuse."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from v4 import material_reads
from v4.common import create, read, pointer, Invalid


class ReadingTests(unittest.TestCase):
    def expand(self, value):
        if isinstance(value, dict):
            if '$read_ref' in value:
                ref = value['$read_ref']; original = read(ref['material'])
                return pointer(original, ref['pointer']) if ref['pointer'] else original
            if '$report_ref' in value:
                return Path(value['$report_ref']).read_text()
            return {k: self.expand(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.expand(v) for v in value]
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


if __name__ == '__main__':
    unittest.main()
