"""Open model assessments bound to frozen evidence, without factor selection rules."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from . import evidence
from .common import digest, exact, read, require, text


DISCOVERY_REVIEW = (
    '每轮同时核验原深评依据和主动发现原报告遗漏、低估或过时的重要因素。'
    '查验上次有效核验后的公开新增信息及下一复核前的重要事件；首次基线和深评发布前覆盖报告讨论的投资周期。'
    '行情包与原因子名单不是全部研究范围，按问题查阅公开原始公告、日程及资金或融资证据。'
    'factor_checks记录实际查验来源、时间、传导、影响周期、原报告覆盖和可能受影响的结论；提到名称不等于已评估。'
    '实际核验因素如何支持或改变周期形态、当前机会与此前建议的价值和共识；价格未突破不阻止定价复核，短期反弹不能替代周期证据。'
    '关键遗漏是严重分析缺陷：补证后使用research_findings的真实原始引用发起或恢复BTC_CYCLE，不等价格或金额变化。'
    '未查证或关键来源失败不能当作无变化；记录影响、补查期限并独立判断必要反馈。'
    '因子动态选择，不设固定目录、数量、方向、权重或关键词评分。'
    '研究范围来自完整核心报告，逐项验证三类收益、美股定投/减仓/回补、加密本金保护和配置建议；'
    '美股事件与原始证据用本EvidenceBundle的PUBLIC_RAW引用，经factor_checks/research_findings维护，不套BTC行情引用或触价条件。'
    '未在手机摘要展示的重要条件也须核验，不能只沿用上一条短稿。'
)

PUBLICATION_REVIEW = (
    '独立判断当前证据是否覆盖投资周期内可能影响未来价值、下行路径和等待成本的重要因素。'
    '对实际发现的关键问题，完整内部深评正文纳入其传导与条件；不采纳说明理由，未解决说明影响及期限。'
    '在coverage.value_pricing和coverage.verification中实际核对因素覆盖与时效，六项字段齐全不代表分析完整。'
    '完整报告开头必须能读出周期形态、切换确认性及建议价值与共识；因子解释、传导分段或历史价位不能代替结论。'
    '短窗承接或去仓只是竞争解释，不能直接证明中长期价值未受损；保留减仓与继续持有的比较。'
    '三类收益和分资产结论及配置比例必须在同一正文与业务语义中一致；依报告核验本金保护、资金不重复占用及退出/回补安排。'
)


def validate(value, bundle, directory):
    """Validate shape and cited values only; prose remains a model assessment.

    Empty items or references are legitimate. The model explains exclusions,
    evidence gaps, changing mechanisms and relevance in its own words; this
    validator does not infer a direction, score, coverage or trading action.
    """
    exact(value, 'items summary', 'factor assessment')
    text(value['summary'], 'factor assessment summary')
    require(isinstance(value['items'], list), 'factor items must be a list')
    ids = set()
    for item in value['items']:
        exact(item, 'id name assessment change_reason refs counter_refs', 'factor item')
        for field in ('id', 'name', 'assessment', 'change_reason'):
            text(item[field], 'factor ' + field)
        require(item['id'] not in ids, 'duplicate factor id')
        ids.add(item['id'])
        for field in ('refs', 'counter_refs'):
            require(isinstance(item[field], list), 'factor ' + field + ' must be a list')
            for ref in item[field]:
                # A cited lead remains a lead; an interpretation is not promoted
                # into EvidenceBundle.verified_facts by being used by a factor.
                evidence.resolve(bundle, directory, ref)
    return value


def context(root, project, evidence_dir=None):
    """Expose the guide, actual current assessment and compact raw-source index.

    This does not access account fact files or create another current owner.
    A missing guide is visible for historical/isolated projects, not a fake
    factor evaluation. Raw payloads (including release/revision metadata) are
    read only when the model follows a returned source path or raw reference.
    """
    from .storage import current

    root = Path(root)
    from node_config import factor_guide
    guide = factor_guide()
    pinned = current(root)
    core = (pinned or {}).get('assessment') or {}
    factor_assessment = core.get('factor_assessment')
    current_value = {
        'decision_id': (pinned or {}).get('decision_id'),
        'assessment_revision': core.get('revision'),
        'evidence_id': core.get('evidence_id'),
        'status': ('ASSESSED' if factor_assessment is not None else
                   'HISTORY_UNASSESSED' if core else 'NO_CURRENT_ASSESSMENT'),
        'factor_assessment': deepcopy(factor_assessment),
        'section_provenance': deepcopy(core.get('section_provenance', {}).get('factor_assessment')),
        'temporal_scope': deepcopy(core.get('temporal_scope')),
    }
    if evidence_dir is None and core.get('evidence_id'):
        evidence_dir = root / 'evidence' / core['evidence_id'][7:]
    indexed = None
    if evidence_dir is not None:
        directory = Path(evidence_dir).resolve()
        bundle = evidence.validate(read(directory / 'evidence.json'), directory)
        indexed = {
            'evidence_id': bundle['evidence_id'], 'cutoff': bundle['cutoff'],
            'directory': str(directory),
            'sources': [dict(source, raw_path=str(directory / 'raw' / (source['raw_hash'][7:] + '.json')))
                        for source in bundle['sources']],
        }
        if 'report_products' in bundle:
            indexed['report_products'] = deepcopy(bundle['report_products'])
        if 'collector' in bundle:
            from .collector import context as collector_context
            indexed['collector_observations'] = collector_context(bundle, directory)
    return {
        'guide': {'path': str(guide), 'hash': digest(guide.read_bytes()) if guide.is_file() else None,
                  'status': 'AVAILABLE' if guide.is_file() else 'MISSING'},
        'current': current_value,
        'evidence': indexed,
        'discovery_instructions': DISCOVERY_REVIEW,
    }
