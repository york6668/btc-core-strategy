#!/usr/bin/env python3
"""BTC v4 local workflow; no private accounts, network sends or trading operations."""
import argparse
import json
from pathlib import Path
import sys

if __name__ == '__main__':
    from node_bridge import forward_if_enabled
    forward_if_enabled(sys.argv[1:])

from v4 import assessment, delivery, domain, draft, evidence, hourly, migration, storage, sensing, main_flow, research, reporting
from v4.common import Invalid, create, digest, facts, read, locked


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--root', required=True, help='isolated store during testing; production .strategy/v4')
    parser.add_argument('--full', action='store_true', help='include full immutable payload instead of compact paths')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('run-prepare')
    p.add_argument('--run-ref', required=True)
    p.add_argument('--agent', required=True)
    p.add_argument('--directory', required=True)
    p.add_argument('--market', help='frozen raw package for offline replay or same-study refresh')
    p.add_argument('--additional', help='additional model-selected frozen public evidence')
    p.add_argument('--collector-observations', help='frozen local multi-asset export; otherwise read the project reader without syncing')
    p.add_argument('--collector-snapshot', help='validated original collector.json, distinct from a reader export array')
    p.add_argument('--resume-preparation', action='store_true', help='reuse a partial preparation and its frozen inputs without recollection')
    p.add_argument('--retained-source-bindings', help='explicit original evidence_id/source_hash choices for legacy retrieval collisions')
    p.add_argument('--deep', action='store_true', help='force refresh of daily/weekly raw evidence; does not run Astra')
    p = sub.add_parser('run-material')
    p.add_argument('--run', required=True)
    p.add_argument('--stage', choices=('report','report-body','main'), default='report')
    p = sub.add_parser('run-value-ref')
    p.add_argument('--run', required=True)
    p.add_argument('--file', required=True)
    p.add_argument('--pointer', required=True)
    p = sub.add_parser('run-advance')
    p.add_argument('--run', required=True)
    p.add_argument('--stage', choices=('market','observe','main','report'), required=True)
    choice = p.add_mutually_exclusive_group(required=True)
    choice.add_argument('--input', help='complete authored input; legacy interface')
    choice.add_argument('--answers', help='authored judgment JSON, merged with --template')
    p.add_argument('--template', help='unchanged stage template from this run, required with --answers')
    p.add_argument('--check-only', action='store_true', help='main stage: validate without writing any artifact or strategy state')
    p = sub.add_parser('migrate')
    p.add_argument('--state', required=True)
    p.add_argument('--plan', required=True)
    p = sub.add_parser('import-delivery')
    p.add_argument('--report', required=True)
    p.add_argument('--projection', required=True)
    p.add_argument('--provider-readback', required=True)
    for name in ('pin', 'recover', 'facts'):
        sub.add_parser(name)
    p = sub.add_parser('context')
    p.add_argument('--limit', type=int, default=240)
    p.add_argument('--market', help='current public market package; required by recommendation sensing')
    p.add_argument('--evidence', help='frozen public EvidenceBundle directory; enables revision 4 dynamic factors')
    p = sub.add_parser('observe')
    p.add_argument('--market', required=True)
    p.add_argument('--review', required=True)
    p.add_argument('--evidence', help='same frozen evidence directory used by revision 4 context')
    p = sub.add_parser('sensing-gate')
    p.add_argument('--observation', required=True)
    p = sub.add_parser('gate')
    p.add_argument('--binding', required=True, help='actual persisted hourly binding JSON')
    p = sub.add_parser('prepare-domain')
    p.add_argument('--at', required=True)
    p.add_argument('--trigger', required=True)
    p.add_argument('--replacements', required=True)
    p.add_argument('--output', required=True)
    p = sub.add_parser('archive-evidence')
    p.add_argument('--directory', required=True)
    p = sub.add_parser('evidence-root')
    for flag in ('base', 'market', 'references', 'directory', 'cutoff'):
        p.add_argument('--' + flag, required=True)
    p.add_argument('--additional', help='additional frozen public evidence directory to merge into the independent root')
    p = sub.add_parser('freeze-evidence')
    p.add_argument('--spec', required=True)
    p.add_argument('--raw-files', required=True, help='JSON mapping source id to frozen raw file')
    p.add_argument('--directory', required=True)
    p.add_argument('--parent')
    for name in ('validate', 'commit'):
        p = sub.add_parser(name)
        p.add_argument('--draft', required=True)
        p.add_argument('--evidence', required=True)
        p.add_argument('--review', required=True)
        if name == 'commit':
            p.add_argument('--expected', required=True)
    p = sub.add_parser('assemble-main')
    for flag in ('components', 'evidence', 'market', 'identity', 'checks', 'output', 'review-output'):
        p.add_argument('--' + flag, required=True)
    sub.add_parser('report-context')
    p=sub.add_parser('report-care')
    p.add_argument('--run-ref')
    p = sub.add_parser('report-review')
    p.add_argument('--spec', required=True)
    p = sub.add_parser('market-report-source')
    p.add_argument('--review', required=True)
    p.add_argument('--output', required=True)
    p = sub.add_parser('report-source')
    for flag in ('decision', 'layout', 'constraints', 'author-review', 'output'):
        p.add_argument('--' + flag, required=True)
    for name in ('research-request', 'research-result'):
        p = sub.add_parser(name)
        p.add_argument('--spec', required=True)
    p = sub.add_parser('research-orchestrated-independent')
    for flag in ('request','rollout','dispatch','returned','input-review'):
        p.add_argument('--'+flag,required=True)
    p = sub.add_parser('research-orchestrated-result')
    p.add_argument('--spec',required=True)
    p = sub.add_parser('research-market-amendment')
    for flag in ('independent','rollout'):
        p.add_argument('--'+flag, required=True)
    for flag in ('dispatch','returned','turn'):
        p.add_argument('--'+flag)
    p.add_argument('--additional')
    p = sub.add_parser('research-read')
    p.add_argument('--request', required=True)
    p.add_argument('--phase', choices=('independent','portfolio','candidate'), required=True)
    p.add_argument('--candidate-id')
    p.add_argument('--independent-id')
    p = sub.add_parser('research-independent')
    for flag in ('request', 'agent', 'rollout', 'turn'):
        p.add_argument('--' + flag, required=True)
    p = sub.add_parser('research-reveal')
    for flag in ('request', 'independent', 'candidate'):
        p.add_argument('--' + flag, required=True)
    p.add_argument('--additional', help='archived final EvidenceBundle directory after supplementary research')
    p.add_argument('--check-only', action='store_true', help='validate the mapping and raw bindings without disclosing a candidate')
    p = sub.add_parser('enqueue')
    p.add_argument('--decision', required=True)
    p.add_argument('--source', help='new locally frozen source JSON for main revisions 3/4')
    p.add_argument('--destination', required=True, help='JSON with actual project_id and thread_id')
    p = sub.add_parser('delivery')
    p.add_argument('--identity', required=True)
    p.add_argument('--action', choices=('BEGIN', 'UNKNOWN', 'CONFIRM', 'NOT_SENT', 'BIND_REQUEST', 'ACKNOWLEDGE', 'BEGIN_CORRECTION', 'RECONCILE_REQUEST', 'RETRY_UNKNOWN', 'SUPERSEDE_PENDING'), required=True)
    p.add_argument('--receipt')
    p = sub.add_parser('install-views')
    p.add_argument('--expected-hashes', required=True)
    p = sub.add_parser('repair-completion')
    repair = p.add_mutually_exclusive_group(required=True)
    repair.add_argument('--completion')
    repair.add_argument('--delivery-id')
    repair.add_argument('--main-draft', help='original submitted draft for an already published normalized main')
    args = parser.parse_args(argv)
    try:
        if args.command == 'run-prepare':
            from v4 import run_flow
            from v4 import market_state
            result = run_flow.prepare(args.root,args.project,args.run_ref,args.directory,args.market,args.additional,args.deep,args.agent,args.collector_observations,
                                      workflow_revision=7 if market_state.enabled(args.root) else None,
                                      resume_preparation=args.resume_preparation, collector_snapshot=args.collector_snapshot,
                                      retained_source_bindings=args.retained_source_bindings)
        elif args.command == 'run-value-ref':
            from v4 import run_flow, material_reads
            run = run_flow.read_run(args.run, args.root, args.project)
            result = material_reads.value_reference(args.file, args.pointer,
                [run['directory'], Path(run['root']) / 'research', Path(run['root']) / 'versions'])
        elif args.command == 'run-material':
            from v4 import run_flow
            run = run_flow.read_run(args.run, args.root, args.project)
            result = run_flow.phase_material(run, 'refresh', args.stage)
        elif args.command == 'run-advance':
            from v4 import run_flow
            result = run_flow.advance(args.run,args.stage,args.input,args.root,args.project,args.template,args.answers,check_only=args.check_only)
        elif args.command == 'migrate':
            result = migration.migrate(args.root, args.project, args.state, args.plan)
        elif args.command == 'import-delivery':
            result = migration.import_delivery(args.root, args.report, args.projection, args.provider_readback)
        elif args.command == 'pin':
            result = storage.current(args.root)
        elif args.command == 'recover':
            result = storage.recover(args.root)
        elif args.command == 'facts':
            result = facts(args.project)
        elif args.command == 'context':
            if args.market:
                result = sensing.context(args.root, Path(args.project) / '.strategy/market_observation.sqlite3', args.project, read(args.market), args.limit, compact=not args.full, evidence_dir=args.evidence)
            else:
                result = hourly.context(args.root, Path(args.project) / '.strategy/market_observation.sqlite3', args.limit)
        elif args.command == 'observe':
            result = sensing.record(args.root, Path(args.project) / '.strategy/market_observation.sqlite3', args.project, read(args.market), read(args.review), evidence_dir=args.evidence)
        elif args.command == 'sensing-gate':
            result = sensing.gate(args.root, args.observation)
        elif args.command == 'gate':
            result = hourly.gate(args.root, Path(args.project) / '.strategy/market_observation.sqlite3', read(args.binding))
        elif args.command == 'prepare-domain':
            value = draft.prepare_domain(storage.current(args.root), args.at, args.trigger, read(args.replacements))
            create(args.output, value)
            result = {'output': args.output}
        elif args.command == 'archive-evidence':
            result=storage.archive_evidence(args.root,args.directory)
        elif args.command == 'evidence-root':
            result=evidence.compact_root(args.base,args.market,read(args.references),args.directory,args.cutoff,additional=args.additional)
        elif args.command == 'freeze-evidence':
            result = evidence.freeze(read(args.spec), read(args.raw_files), args.directory, args.parent)
        elif args.command == 'validate':
            from v4 import report, semantics
            prior = storage.current(args.root)
            value, review_value = read(args.draft), read(args.review)
            if value.get('revision', 1) >= 3:
                result = storage.validate_main(args.root, args.project, value, args.evidence, review_value, prior)
            else:
                storage.enforce_write_policy(args.root, value, prior)
                result = assessment.validate(value, args.evidence, storage.domain_baseline(args.root, prior), args.project, review_value)
                comparison = semantics.compare(result, prior.get('assessment'), prior['canonical_domain'], review_value['material_new_outlook'])
                report.validate_changes(result, comparison)
            if 2 <= result.get('revision', 1) < 3:
                sensing.assert_current(args.root, args.project, read(args.draft), args.evidence)
            result = {'valid': True, 'draft_id': result['draft_id'], 'scope': 'structure_and_bindings_only'}
        elif args.command == 'commit':
            result = storage.commit(args.root, args.project, read(args.draft), args.evidence, read(args.review), args.expected)
        elif args.command == 'assemble-main':
            value, reviewed = main_flow.assemble(args.root,args.project,read(args.components),args.evidence,read(args.market),read(args.identity),read(args.checks))
            create(args.output,value)
            create(args.review_output,reviewed)
            result={'draft_id':value['draft_id'],'draft':args.output,'review':args.review_output}
        elif args.command == 'report-care':
            from v4 import report_care
            result=report_care.status(args.root,run_ref=args.run_ref)
        elif args.command == 'report-context':
            result=reporting.context(args.root)
        elif args.command == 'report-review':
            result=reporting.record(args.root,read(args.spec))
        elif args.command == 'market-report-source':
            value=reporting.source(args.root,args.review)
            create(args.output,value)
            result={'source':args.output,'body':value['body'],'body_hash':value['body_hash']}
        elif args.command == 'report-source':
            value=main_flow.report_source(args.root,args.decision,read(args.layout),read(args.constraints),read(args.author_review))
            create(args.output,value)
            result={'source':args.output,'body':value['body'],'body_hash':value['body_hash']}
        elif args.command == 'research-request':
            from v4 import market_state
            result=research.request(args.root,args.project,read(args.spec), workflow_revision=7 if market_state.enabled(args.root) else None)
        elif args.command == 'research-result':
            result=research.result(args.root,read(args.spec))
        elif args.command == 'research-orchestrated-independent':
            result=research.orchestrated_independent(args.root,args.request,args.rollout,args.dispatch,args.returned,read(args.input_review))
        elif args.command == 'research-orchestrated-result':
            result=research.orchestrated_result(args.root,read(args.spec))
        elif args.command == 'research-market-amendment':
            result=research.amend_market(args.root,args.independent,args.rollout,args.dispatch,args.returned,args.additional,args.turn)
        elif args.command == 'research-read':
            result=research.read_input(args.root,args.request,args.phase,args.candidate_id,args.independent_id)
        elif args.command == 'research-independent':
            result=research.independent(args.root,args.request,args.agent,args.rollout,args.turn)
        elif args.command == 'research-reveal':
            result=research.reveal(args.root,args.request,args.independent,read(args.candidate),additional=args.additional,check_only=args.check_only)
        elif args.command == 'enqueue':
            result = delivery.enqueue(args.root, args.decision, read(args.destination), read(args.source) if args.source else None)
        elif args.command == 'delivery':
            result = delivery.transition(args.root, args.identity, args.action, read(args.receipt) if args.receipt else None)
        elif args.command == 'repair-completion':
            db = Path(args.project) / '.strategy/market_observation.sqlite3'
            with locked(args.root):
                if args.main_draft:
                    result = storage.repair_main_completion(args.root, args.project, read(args.main_draft))
                elif args.delivery_id:
                    result = hourly.complete_report(args.root, args.delivery_id, db)
                else:
                    result = hourly.complete_deep(args.root, args.completion, db)
        else:
            storage.install_views(args.root, args.project, read(args.expected_hashes))
            result = {'installed': True}
        if not args.full and isinstance(result,dict):
            if args.command in ('pin','commit') and result.get('decision_id'):
                result={k:result[k] for k in ('status','decision_id','report_required','comparison','created_at') if k in result}
                result['decision_path']=str(Path(args.root)/'versions'/result['decision_id']/'decision.json')
                pinned = read(result['decision_path'])
                owned = pinned.get('assessment') or {}
                policy_path = Path(args.root)/'write-policy.json'
                policy = read(policy_path) if policy_path.exists() else {}
                result['assessment_revision'] = owned.get('revision',1)
                result['minimum_write_revision'] = max(result['assessment_revision'],policy.get('minimum_assessment_revision',1))
                result['domain_strategy_revision'] = pinned['canonical_domain'].get('strategy_revision')
                result['has_current_conclusion'] = bool(owned.get('conclusion'))
                result['assessment_owner'] = {k:v for k,v in owned.get('provenance',{}).items() if k in ('role','model','effort')}
            elif args.command in ('freeze-evidence','evidence-root'):
                result={'evidence_id':result['evidence_id'],'directory':args.directory,'source_count':len(result['sources'])}
            elif args.command.startswith('research-'):
                result={k:v for k,v in result.items() if k not in ('analysis','summary','candidate')}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except (Invalid, OSError, ValueError, KeyError, TypeError) as exc:
        message = str(exc)
        next_action = 'REPAIR_INPUT_AND_RESUME_THIS_STAGE'
        if 'context changed' in message or 'changed_bindings' in message:
            next_action = 'REFRESH_STAGE_MATERIAL_REVIEW_CHANGED_BINDINGS'
        elif 'reference' in message or 'raw value' in message:
            next_action = 'REPAIR_REFERENCE_AGAINST_FROZEN_EVIDENCE'
        elif args.command == 'delivery':
            next_action = 'READ_BACK_EXISTING_ATTEMPT' if 'processing' not in message else 'WAIT_FOR_PROCESSING_THEN_READ_BACK'
        print(json.dumps({'error': type(exc).__name__, 'message': message,
                          'failed_stage': getattr(args, 'stage', args.command), 'next_action': next_action,
                          'reuse_completed_work': True,
                          'retry': 'Repair the reported cause or review changed evidence before retrying; do not restart completed research.'}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
