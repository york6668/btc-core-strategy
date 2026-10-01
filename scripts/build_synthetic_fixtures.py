#!/usr/bin/env python3
"""Construct synthetic legacy documents; never read a real report or account."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skill/btc-core-strategy'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', '--output', dest='output', type=Path, required=True)
    args = parser.parse_args()
    fixtures = args.output.resolve()
    os.environ['BTC_TEST_FIXTURE_DIR'] = str(fixtures)
    fixtures.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        node = Path(directory)
        for name in ('资产配置.md', '操作记录.md', '纠正记录.md'):
            (node / name).write_text('SYNTHETIC TEST FACTS ONLY\n')
        config = node / 'node.json'
        config.write_text(json.dumps({'schema': 'BTC_NODE_CONFIG_V1', 'project_dir': str(node),
                                     'legacy_revision2_dir': str(fixtures)}))
        os.environ['BTC_NODE_CONFIG'] = str(config)
        sys.path[:0] = [str(SKILL / 'scripts'), str(SKILL / 'tests')]
        import test_strategy_contract as fixture
        import test_revision2_report_bridge as bridge_fixture
        import strategy_contract as contract
        import decision_report
        compact = fixture.make_bundle(False)['report_semantics']
        expressive = decision_report.semantic_projection(decision_report.build_report_projection(
            bridge_fixture.same_meaning_bundle(), bridge_fixture.paragraphs(), visible_level_ids={'V0'}))
        state_fields = 'strategy_id strategy_revision state_id derived_at state_recorded_at trigger_type trigger_fingerprint pricing_as_of account_fact_source account_fact_as_of derivation_status driver_regime path_decision evidence_status monitor_state attention_level projection_mode user_input_requirement project_patch_required monitor_state_as_of projection_plan_id projection_fingerprint'.split()
        plan_fields = 'plan_id source_strategy_id source_strategy_revision source_state_id state_as_of projection_fingerprint monitoring_revision final_expiry_event expires_at scheduled_task_ref'.split()
        state = {key: 'SYNTHETIC' for key in state_fields}
        plan = {key: 'SYNTHETIC' for key in plan_fields}
        state['strategy_revision'] = plan['source_strategy_revision'] = '2'
        state_sections = ['本轮边界与数据验证', '当前派生状态', 'anchor_map 与 value_zone_lifecycle', '本地账户事实与限制', '证据状态与下一边界']
        plan_sections = ['计划元数据与角色边界', '数据验证、归因与策略结论', '未来价值路径与切换条件', '溢价空单评估阶梯（仅供用户评估）', '折价现货评估阶梯（仅供用户评估）', '持仓复核与账户影响', '失效证据与事件链', 'scheduled 与云端展示收据']
        def document(fields, sections):
            return '# SYNTHETIC REVISION 2 — NOT A REAL REPORT\n\n' + ''.join(
                '- **%s**：%s\n' % item for item in fields.items()) + ''.join(
                '\n## ' + heading + '\n' + ('合成测试材料；所有价格和行为仅用于覆盖校验与迁移，不表示任何账户事实。\n' * 14)
                for heading in sections)
        report = '# SYNTHETIC REPORT — NOT INVESTMENT ADVICE\n\n'
        witnesses = {}
        audit = {}
        for field in decision_report.FIELDS:
            quote = 'Synthetic report field: ' + field + ' = ' + json.dumps(compact[field], ensure_ascii=False, sort_keys=True)
            line = report.count('\n') + 1
            report += quote + '\n\n'
            witnesses[field] = {'visible_excerpt': quote, 'semantic_fingerprint': contract._fingerprint({field: compact[field]})}
            audit[field] = {'sources': [{'artifact': 'revision2_report_1346.md', 'line_start': line,
                'line_end': line, 'quote': quote}], 'inference_kind': 'HISTORICAL_SEMANTIC_CODEC',
                'interpretation': '合成历史语义测试；绝非账户Delta或现金。'}
        audit['portfolio_action_by_module']['inference_kind'] = 'DIRECT_PROPOSED_INCREMENT_NOT_ACCOUNT_FACT'
        audit['decision_relevant_levels_and_sizes']['interpretation'] = '合成旧报告正文确实包含V1及候选阶梯；比较集合仅含V0。'
        v1_line = report.count('\n') + 1
        v1_quote = 'Synthetic V1 and candidate ladder: unconfirmed, no executed position.'
        report += v1_quote + '\n\n'
        audit['decision_relevant_levels_and_sizes']['sources'].append({'artifact': 'revision2_report_1346.md', 'line_start': v1_line, 'line_end': v1_line, 'quote': v1_quote})
        disclaimer = 'Synthetic conditions only; no transaction or execution authorization.'
        report += disclaimer + '\n'
        projection = {'schema': contract.REVISION2_REPORT_BASELINE_SCHEMA, 'report_semantics': compact,
                      'rendered_evidence': witnesses, 'authorization_disclaimer_excerpt': disclaimer}
        values = {'state': ('revision2_state_1346.md', document(state, state_sections)),
                  'plan': ('revision2_plan_1346.md', document(plan, plan_sections)),
                  'report': ('revision2_report_1346.md', report),
                  'report_projection': ('revision2_report_1346_semantics.json', json.dumps(projection, ensure_ascii=False, indent=2) + '\n')}
        artifacts = {}
        for key, (filename, text) in values.items():
            (fixtures / filename).write_text(text, encoding='utf-8')
            artifacts[key] = [filename, 'sha256:' + hashlib.sha256(text.encode()).hexdigest()]
        import revision2_report_bridge as bridge
        profile = {'schema': 'BTC_LEGACY_PROFILE_V1', 'codec': bridge.CODEC, 'artifacts': artifacts,
                   'bridge': {'codec': bridge.CODEC, 'semantic_projection': expressive, 'audit_witness': audit,
                              'artifact_fingerprints': {k: v[1] for k, v in artifacts.items()},
                              'explicit_unknowns': list(bridge.HISTORICAL_UNKNOWN_PATHS)}}
        (fixtures / 'profile.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n')
        samples = []
        for label, price, previous, expected in [('below', 76900, 'INSIDE_ZONE', 'BELOW_ZONE'),
            ('inside', 77100, 'BELOW_ZONE', 'INSIDE_ZONE'), ('13:46', 77600, 'INSIDE_ZONE', 'ABOVE_ZONE'),
            ('inside-again', 77300, 'ABOVE_ZONE', 'INSIDE_ZONE'), ('above-again', 77900, 'INSIDE_ZONE', 'ABOVE_ZONE')]:
            samples.append({'label': label, 'prices': {'binance': str(price), 'coinbase': str(price + 10), 'kraken': str(price + 5)},
                            'prior_relation': previous, 'expected_relation': expected})
        (fixtures / 'history_replays.json').write_text(json.dumps({'value_zone': {'lower': 77000, 'upper': 77500}, 'samples': samples}, indent=2) + '\n')
        cutoff = 1788324299999
        candles = [{'open_time': cutoff - (3 - n) * 3600000 + 1,
                    'close_time': cutoff - (2 - n) * 3600000,
                    'open': '77600', 'high': '77800', 'low': '77400' if n == 0 else '77500', 'close': '77700'} for n in range(3)]
        candles[-1]['close_time'] = cutoff + 1000
        (fixtures / 'candles_1346.json').write_text(json.dumps(candles, indent=2) + '\n')


if __name__ == '__main__':
    main()
