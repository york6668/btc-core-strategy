"""Finished Markdown, real journal recovery and public product identity contracts."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1]/'scripts'), str(Path(__file__).parent)]
import test_market_reporting as fixture
from v4 import authored_report as authored, delivery, reporting, equity_products, evidence
from v4.common import Invalid, create, digest, read


class AuthoredReportTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.MarketReportingTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        original = self.f.context
        def context(root):
            value = original(root)
            value['metrics']['binance_spot_1h'].update(instrument='BTCUSDT', quote_unit='USDT', market='SPOT')
            return value
        self.f.context = context
        mock.patch('v4.reporting.context', side_effect=context).start()

    def spec(self, body=None):
        value = self.f.spec()
        quote = 'BTC 最近完成小时收盘 80,001.25 USDT。'
        body = body or ('市场结论\n\n'+quote+'\n\n| 对象 | 判断 |\n|---|---|\n| 币安 QQQ | 短期承压，中期盈利判断有条件成立。 |\n\n'
                        '- 价格风险：产品报价未核验，不能套用指数点位。\n- 个人应对：保护合并复核，数量未知。')
        value['layout'] = {'format': authored.FORMAT, 'body': body,
            'bindings': [{'text': quote, 'refs':['/metrics/binance_spot_1h/latest/close'], 'scope':'market', 'price':{'subject':'BTC','unit':'USDT'}}],
            'price_status': {'lower_spot':'VALID', 'upper_short':'VALID'},
            'quality_review': {k:'已对照实际正文中的对象、期限、条件、来源和限制逐项核对。' for k in authored.QUALITY}}
        value['information'] = {'market': quote}
        return value

    def source(self, value=None):
        result = reporting.record(self.root, value or self.spec())
        return reporting.source(self.root, result['review_id'])

    def queue(self, value=None):
        source = self.source(value)
        queued = delivery.enqueue(self.root, self.f.f.decision['decision_id'], self.f.f.destination, source)
        return queued['delivery_id'], source

    def begin(self, identity, source, action='BEGIN'):
        self.f.f.body = source['body']
        self.f.f.items = []
        status = self.f.f.begin(identity, action, bind=False)
        if not status['ready_to_send']:
            return status
        return delivery.transition(self.root, identity, 'BIND_REQUEST', {
            'schema':'BTC_SENT_REQUEST_V1', 'attempt_id':status['attempt']['attempt_id'], 'destination':self.f.f.destination,
            'request_ref':'REQUEST_'+str(status['attempts']), 'record_refs':['REQUEST_'+str(status['attempts'])],
            'request_body':status['request_body'], 'anchor_ref':'SYNTHETIC_BEFORE_SEND_CURSOR',
            'request_started_at':'2030-03-01T15:00:01+00:00'})

    def receipt(self, identity, body=None):
        result = self.f.f.receipt(identity, body)
        result['review'].update(bindings=[], checks={})
        return result

    def test_final_markdown_survives_full_chain_and_has_no_generated_advice(self):
        identity, source = self.queue()
        self.assertEqual(source['body'], self.spec()['layout']['body'])
        artifact = read(self.root/'outbox'/identity/'artifact.json')
        self.assertEqual(artifact['presentation_mode'], 'VERBATIM')
        self.assertEqual(self.begin(identity, source)['attempt']['mode'], 'VERBATIM')
        self.assertEqual(delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity))['state'], 'DELIVERED')
        self.assertEqual(reporting.reported(self.root)['display'], source['body'])
        self.assertEqual(reporting.reported(self.root)['consensus_prices'], {})
        self.assertNotIn('64,000', source['body'])

    def test_short_and_long_reports_are_not_reflowed_or_truncated(self):
        for suffix in ('', '\n\n'+('保留关键条件与尚未知的限制。\n'*600)):
            value = self.spec('BTC 最近完成小时收盘 80,001.25 USDT。'+suffix)
            source = self.source(value)
            self.assertEqual(source['body'], value['layout']['body'])
        self.source(self.spec('BTC 最近完成小时收盘 80,001.25 USDT。\n\n1. 观察条件。\n2. 保留未知。'))

    def test_reading_review_belongs_to_new_sources_and_legacy_sources_keep_their_contract(self):
        quote = 'BTC 最近完成小时收盘 80,001.25 USDT。'
        value = self.spec(quote)
        context = self.f.context(self.root)
        context['report_contract_revision'] = 4
        review = value['layout']['quality_review']
        review['market_independence'] = '本稿市场原句只引用独立公开小时收盘，未引入个人资产。'
        decision = self.f.f.decision
        # Correct references alone do not fulfil the new reading review record.
        with self.assertRaisesRegex(Invalid, 'complete authored expression review'):
            authored.render(dict(value, context=context), decision)
        review.update({key: '对照本稿实际收盘原句与公开来源说明主题、决定影响及技术依据边界。'
                       for key in authored.READING_REVIEW})
        self.assertEqual(authored.render(dict(value, context=context), decision)[0], quote)
        review['maintenance_separation'] = ''
        with self.assertRaisesRegex(Invalid, 'maintenance_separation'):
            authored.render(dict(value, context=context), decision)
        for key in authored.READING_REVIEW:
            review.pop(key)
        context['report_contract_revision'] = 3
        self.assertEqual(authored.render(dict(value, context=context), decision)[0], quote)
        # The program validates records and renders verbatim; content quality
        # still needs the actual model/human reading, not a prose score.

    def test_clock_only_change_is_not_a_new_report(self):
        original = self.f.context
        ctx = original(self.root); ctx['evaluated_at'] = '2030-03-01T13:00:00Z'
        self.f.context = lambda root: dict(original(root), evaluated_at=ctx['evaluated_at'])
        with mock.patch('v4.reporting.context', side_effect=self.f.context):
            def timed():
                value = self.spec(); line = authored.evaluation_time(ctx)
                value['layout']['body'] = line+'\n\n'+value['layout']['body']
                value['layout']['bindings'].append({'text':line,'scope':'clock','refs':['/evaluation_time']})
                return value
            identity, source = self.queue(timed()); self.begin(identity, source)
            delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity))
            self.assertEqual(reporting.reported(self.root)['display_evaluation_time'], authored.evaluation_time(ctx))
            ctx['evaluated_at'] = '2030-03-01T13:01:00Z'; self.f.seq += 1
            with self.assertRaisesRegex(Invalid, 'same mobile meaning'):
                self.source(timed())

    def test_prepared_clock_survives_minute_change_without_freezing_validity_clock(self):
        ctx = self.f.context(self.root)
        ctx['evaluated_at'] = '2030-03-01T13:00:00Z'
        value = self.spec(); line = authored.evaluation_time(ctx)
        value['layout']['body'] = line+'\n\n'+value['layout']['body']
        value['layout']['bindings'].append({'text':line, 'scope':'clock', 'refs':['/evaluation_time']})
        live = dict(ctx, evaluated_at='2030-03-01T13:01:00Z')
        with mock.patch('v4.reporting.context', return_value=live):
            result = reporting.record(self.root, value, prepared_context=ctx)
        saved = reporting.review(self.root, result['review_id'])
        self.assertEqual(saved['context']['evaluated_at'], '2030-03-01T13:01:00Z')
        self.assertEqual(saved['context']['authored_evaluated_at'], '2030-03-01T13:00:00Z')
        self.assertEqual(reporting.source(self.root, result['review_id'])['body'], value['layout']['body'])
        decision = deepcopy(self.f.f.decision)
        decision['assessment']['recommendations']['upper_short']['validity']['until'] = '2030-03-01T13:00:30Z'
        with self.assertRaisesRegex(Invalid, 'expired advice'):
            authored.price_status(saved, decision)
        with mock.patch('v4.reporting.context', return_value=live):
            with self.assertRaisesRegex(Invalid, 'future'):
                reporting.record(self.root, value, prepared_context=dict(ctx, evaluated_at='2030-03-01T13:02:00Z'))
            with self.assertRaisesRegex(Invalid, 'stale prepared'):
                reporting.record(self.root, value, prepared_context=dict(ctx, context_id='unrelated'))

    def test_only_actually_displayed_formal_price_enters_consensus_cache(self):
        value = self.spec(); value['layout']['body'] += '\n有效保护参考 82,000 USDT，仍须符合原条件。'
        value['layout']['bindings'].append({'text':'有效保护参考 82,000 USDT，仍须符合原条件。','scope':'personal',
            'refs':['/assessment/recommendations/upper_short/tiers/0/price_low_usd']})
        identity, source = self.queue(value); self.begin(identity, source)
        delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity))
        prices = reporting.reported(self.root)['consensus_prices']
        self.assertEqual(set(prices), {'upper_short'})
        self.assertEqual(prices['upper_short']['tiers'], [{'price_low_usd':'82000','price_high_usd':'82000'}])

    def test_legacy_format_reads_and_recovers_alongside_new_entry(self):
        identity, source = self.f.queue(); self.f.begin(identity, source)
        with mock.patch('v4.reporting.render', side_effect=AssertionError('legacy frozen source must not rerender')):
            self.assertEqual(self.f.confirm(identity)['state'], 'DELIVERED')
            delivery.validate_delivered(self.root, identity)
        self.f.seq += 1
        identity, source = self.queue(); self.begin(identity, source)
        self.assertEqual(delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity))['state'], 'DELIVERED')

    def test_every_display_change_is_detected_even_when_numbers_are_unchanged(self):
        identity, source = self.queue(); self.begin(identity, source)
        artifact = read(self.root/'outbox'/identity/'artifact.json')
        body = source['body']
        variations = [body.replace('中期盈利判断有条件成立', '中期盈利确定增长'),
                      body.replace('数量未知。', ''), body+'\n补充解释。', body.replace('\n\n', '\n'),
                      '\n'.join(reversed(body.split('\n'))), body.replace('80,001.25','80,002.25')]
        for changed in variations:
            with self.subTest(changed=changed[-40:]):
                self.assertIn('VERBATIM_SOURCE_CHANGED', delivery.presentation_errors(artifact, self.receipt(identity, changed)))

    def test_changed_readback_requires_correction_and_recovery_never_rerenders(self):
        identity, source = self.queue(); self.begin(identity, source)
        status = delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity, source['body']+'\n额外说明'))
        self.assertEqual(status['state'], 'CORRECTION_REQUIRED')
        self.assertFalse((self.root/'reported-current.json').exists())
        with mock.patch('v4.reporting.render', side_effect=AssertionError('must recover frozen body')):
            self.begin(identity, source, 'BEGIN_CORRECTION')
            self.assertEqual(delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity))['state'], 'DELIVERED')
            delivery.validate_delivered(self.root, identity)

    def test_unknown_send_is_read_back_without_resending(self):
        identity, source = self.queue(); self.begin(identity, source)
        delivery.transition(self.root, identity, 'UNKNOWN', {})
        before=read(self.root/'outbox'/identity/'status.json')['attempts']
        self.begin(identity, source)
        self.assertEqual(read(self.root/'outbox'/identity/'status.json')['attempts'],before)
        self.assertEqual(delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity))['state'], 'DELIVERED')

    def test_exact_duplicate_and_format_only_change_are_not_new_reports(self):
        identity, source = self.queue(); self.begin(identity, source)
        delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity))
        self.f.seq += 1
        with self.assertRaisesRegex(Invalid, 'same mobile meaning'):
            self.source()
        value = self.spec(); value['layout']['body'] = value['layout']['body'].replace('市场结论','## 市场结论')
        with self.assertRaisesRegex(Invalid, 'same mobile meaning'):
            self.source(value)

    def test_number_and_personal_source_contamination_fail_before_freezing(self):
        value = self.spec(); value['layout']['body'] += '\n未知概率 90%。'
        with self.assertRaisesRegex(Invalid, 'all report numbers'):
            self.source(value)
        value = self.spec(); value['layout']['bindings'][0]['refs'] = ['/assessment/recommendations/upper_short/tiers/0/price_low_usd']
        with self.assertRaisesRegex(Invalid, 'personal portfolio'):
            self.source(value)

    def test_expired_and_withdrawn_advice_cannot_become_valid_or_show_old_prices(self):
        value = self.spec(); ctx = self.f.context(self.root); ctx['evaluated_at'] = '2040-01-01T00:00:00Z'
        with mock.patch('v4.reporting.context', return_value=ctx):
            with self.assertRaisesRegex(Invalid, 'expired advice'):
                self.source(value)
            value['layout']['price_status'] = dict(lower_spot='REVIEW_REQUIRED', upper_short='REVIEW_REQUIRED')
            value['layout']['body'] += '\n旧保护价 82,000 USDT。'
            value['layout']['bindings'].append({'text':'旧保护价 82,000 USDT。', 'scope':'personal',
                'refs':['/assessment/recommendations/upper_short/tiers/0/price_low_usd']})
            with self.assertRaisesRegex(Invalid, 'cannot be restored'):
                self.source(value)
        decision = deepcopy(self.f.f.decision)
        decision['assessment']['recommendations']['upper_short']['status'] = 'NOT_RECOMMENDED'
        value['layout']['price_status']['upper_short'] = 'WITHDRAWN'
        with self.assertRaisesRegex(Invalid, 'cannot be restored'):
            authored.render(dict(value, context=ctx), decision)

    def test_product_price_rejects_index_source_and_wrong_units(self):
        ctx = self.f.context(self.root)
        ctx['report_subjects'] = equity_products.subjects()
        ctx['report_subjects']['QQQ'].update(identity_status='VERIFIED', product_id='QQQ', quote_unit='USD', quote={'price':'700.50'})
        value = self.spec(); value['layout']['body'] += '\nQQQ 报价 700.50 USD。'
        binding = {'text':'QQQ 报价 700.50 USD。', 'scope':'market', 'refs':['/report_subjects/QQQ/quote/price'],
                   'price':{'subject':'QQQ','unit':'USD'}}
        value['layout']['bindings'].append(binding)
        with mock.patch('v4.reporting.context', return_value=ctx):
            self.assertIn('700.50 USD', self.source(value)['body'])
            value['layout']['body'] = value['layout']['body'].replace('700.50 USD', '700.50 USDT')
            binding['text'] = 'QQQ 报价 700.50 USDT。'
            with self.assertRaisesRegex(Invalid, 'unit differs'):
                self.source(value)
            value['layout']['body'] = value['layout']['body'].replace('700.50 USDT', '700.50 USD')
            binding['text'] = 'QQQ 报价 700.50 USD。'
            binding['price']['unit'] = 'USDT'
            with self.assertRaisesRegex(Invalid, 'unit differs'):
                self.source(value)
            binding['price']['unit'] = 'USD'
            binding['refs'] = ['/metrics/binance_spot_1h/latest/close']
            binding['text'] = 'QQQ 报价 80,001.25 USD。'
            value['layout']['body'] = value['layout']['body'].replace('QQQ 报价 700.50 USD。', binding['text'])
            with self.assertRaisesRegex(Invalid, 'cannot become product prices'):
                self.source(value)

    def test_dollar_policy_accepts_usd_and_preserves_frozen_legacy(self):
        ctx = self.f.context(self.root)
        ctx['report_subjects'] = equity_products.subjects()
        ctx['report_subjects']['QQQ'].update(identity_status='VERIFIED',product_id='QQQ',quote_unit='USD',quote={'price':'700.50'})
        value = self.spec(); value['layout']['body'] += '\nQQQ 报价 700.50 USD。'
        binding = {'text':'QQQ 报价 700.50 USD。','scope':'market','refs':['/report_subjects/QQQ/quote/price'],
                   'price':{'subject':'QQQ','unit':'USD'}}
        value['layout']['bindings'].append(binding)
        # Historical contexts retain their original quote contract.
        self.assertIn('700.50 USD', authored.render(dict(value,context=ctx),self.f.f.decision)[0])
        ctx['report_quote_policy'] = deepcopy(equity_products.REPORT_QUOTE_POLICY)
        self.assertIn('700.50 USD', authored.render(dict(value,context=ctx),self.f.f.decision)[0])
        ctx['report_quote_policy'] = {'venue':'BINANCE','quote_unit':'USDT','show_underlying_prices':False}
        with self.assertRaisesRegex(Invalid, 'dollar quote policy'):
            authored.render(dict(value,context=ctx),self.f.f.decision)
        ctx['report_quote_policy'] = deepcopy(equity_products.REPORT_QUOTE_POLICY)
        # Relabelling a USD source is still forbidden after selecting USDT.
        value['layout']['body'] = value['layout']['body'].replace('700.50 USD','700.50 USDT')
        binding['text'] = 'QQQ 报价 700.50 USDT。'; binding['price']['unit'] = 'USDT'
        with self.assertRaisesRegex(Invalid, 'matching unit'):
            authored.render(dict(value,context=ctx),self.f.f.decision)
        ctx['report_subjects']['QQQ']['quote_unit'] = 'USDT'
        self.assertIn('700.50 USDT', authored.render(dict(value,context=ctx),self.f.f.decision)[0])

    def test_unified_report_keeps_index_levels_internal_and_allows_missing_product_prices(self):
        ctx = self.f.context(self.root)
        ctx['report_subjects'] = equity_products.subjects()
        ctx['report_quote_policy'] = deepcopy(equity_products.REPORT_QUOTE_POLICY)
        value = self.spec()
        self.assertEqual(authored.render(dict(value,context=ctx),self.f.f.decision)[0],value['layout']['body'])
        with self.assertRaisesRegex(Invalid, 'underlying index levels'):
            authored.validate_price({'text':'SPX 700 USDT','price':{'subject':'SPX','unit':'USDT'},'refs':[]},ctx)
        self.assertEqual(ctx['report_subjects']['VOO']['report_quote_unit'],'USD')


    def test_review_target_changes_invalidate_copied_explanations(self):
        quote = 'BTC 最近完成小时收盘 80,001.25 USDT。'
        context = self.f.context(self.root)
        context['report_contract_revision'] = 5
        value = dict(self.spec(quote), context=context)
        layout = value['layout']
        layout['quality_review'] = {key: None for key in authored.quality_keys(context)}
        layout['reviewed_candidate_hash'] = None
        checked = authored.preview(value, self.f.f.decision)
        self.assertEqual(checked['semantic_review'], 'PENDING_ACTUAL_FULL_BODY_REVIEW')
        self.assertEqual(checked['scope'], 'structure_and_bindings_only')
        layout['quality_review'] = {key: 'Synthetic review explanation for the actual bound candidate, not semantic evidence.'
                                  for key in authored.quality_keys(context)}
        with self.assertRaisesRegex(Invalid, 'has not been reviewed'):
            authored.render(value, self.f.f.decision)
        layout['reviewed_candidate_hash'] = checked['reviewed_candidate_hash']
        self.assertEqual(authored.render(value, self.f.f.decision)[0], quote)
        changed = deepcopy(value)
        changed['layout']['body'] = quote.replace('最近', '最新')
        changed['layout']['bindings'][0]['text'] = changed['layout']['body']
        with self.assertRaisesRegex(Invalid, 'final report changed'):
            authored.render(changed, self.f.f.decision)
        for mutate in (
                lambda v: v['layout']['price_status'].update(lower_spot='ADJUSTED'),
                lambda v: v['layout']['bindings'][0].update(scope='personal'),
                lambda v: v.update(result_binding={'changed_public_result': 'sha256:'+'a'*64}),
                lambda v: v['context'].update(context_id='sha256:'+'b'*64)):
            changed = deepcopy(value); mutate(changed)
            with self.assertRaisesRegex(Invalid, 'final report changed'):
                authored.render(changed, self.f.f.decision)
        # A live validity clock and the review explanation are not source identity.
        changed = deepcopy(value)
        changed['context']['evaluated_at'] = '2030-03-01T13:01:00Z'
        changed['layout']['quality_review']['reasoning'] += ' Another review note.'
        self.assertEqual(authored.candidate_hash(changed), checked['reviewed_candidate_hash'])
        # No automatic prose classifier: a newly computed hash still needs real reading.

    def test_frozen_revision_four_keeps_its_original_layout(self):
        quote = 'BTC 最近完成小时收盘 80,001.25 USDT。'
        context = self.f.context(self.root); context['report_contract_revision'] = 4
        value = dict(self.spec(quote), context=context)
        value['layout']['quality_review'] = {key: 'Original frozen contract review, retained without rewriting the body.'
                                            for key in authored.quality_keys(context)}
        self.assertNotIn('reviewed_candidate_hash', value['layout'])
        self.assertEqual(authored.render(value, self.f.f.decision)[0], quote)

if __name__ == '__main__':
    unittest.main()


class PublicProductTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = {'symbol':'QQQ','product_id':'QQQ_TEST','product_type':'ETF','quote_unit':'USD',
                    'quote':{'product_id':'QQQ_TEST','quote_unit':'USD','price':'700.50','as_of':'2026-09-29T20:00:00Z'},
                    'index':{'product_id':'NDX','quote_unit':'points','price':'30339.33','as_of':'2026-09-29T20:00:00Z'}}
        raw_path = self.root/'sample.json'; create(raw_path, self.raw)
        self.bundle = {'parent_id':None,'cutoff':'2026-09-30T00:00:00Z','sources':[{
            'id':'public','origin':'BINANCE','url':'https://www.binance.com/public/synthetic-fixture','trust':'PUBLIC_RAW',
            'published_at':'2026-09-29T20:00:00Z','retrieved_at':'2026-09-29T21:00:00Z',
            'start':'2026-09-29T20:00:00Z','end':'2026-09-29T20:00:00Z','raw_hash':digest(raw_path.read_bytes())}],
            'measurements':[],'verified_facts':[],'candidates':[],'gaps':[],
            'handoff':{'previous_hour':None,'successful_deep':None,'changes_since_hour':[],'changes_since_deep':[],
                       'active_hypotheses':[],'open_questions':[]}}
        def ref(path):
            from v4.common import pointer
            return {'source_id':'public','pointer':path,'value_hash':digest(pointer(self.raw,path))}
        self.product = {k:self.raw[k] for k in ('symbol','product_id','product_type','quote_unit')}
        self.product.update(venue='BINANCE', identity_refs={k:ref('/'+k) for k in self.product},
                            quote={'price':'700.50','as_of':self.raw['quote']['as_of'],
                                   'refs':{k:ref('/quote/'+k) for k in self.raw['quote']}})
        self.bundle['report_products'] = [self.product]

    def freeze(self, suffix='valid'):
        return evidence.freeze(self.bundle, {'public':str(self.root/'sample.json')}, self.root/suffix)

    def test_quote_ingestion_preserves_product_and_underlying_price_difference(self):
        result = equity_products.subjects(self.freeze())
        self.assertEqual(result['QQQ']['quote']['price'], '700.50')
        self.assertEqual(result['QQQ']['underlying_market'], 'NDX')
        self.assertEqual(result['QQQ']['quote_unit'], 'USD')
        self.assertIsNone(result['QQQ']['limitation'])
        self.assertIsNone(result['VOO']['quote'])
        self.assertEqual(result['VOO']['identity_status'], 'UNVERIFIED')

    def test_missing_quote_does_not_remove_underlying_analysis_subject(self):
        self.product['quote'] = None
        result = equity_products.subjects(self.freeze())
        self.assertEqual(result['QQQ']['identity_status'], 'VERIFIED')
        self.assertIsNone(result['QQQ']['quote'])
        self.assertEqual(result['VOO']['underlying_market'], 'SPX')

    def test_unknown_quote_time_keeps_retrieval_separate(self):
        self.bundle['sources'][0]['published_at'] = None
        self.product['quote'].update(as_of=None, retrieved_at=self.bundle['sources'][0]['retrieved_at'],
                                     time_basis='RETRIEVED_AT_SOURCE_TIME_UNKNOWN')
        self.product['quote']['refs'].pop('as_of')
        result = equity_products.subjects(self.freeze())
        self.assertIsNone(result['QQQ']['quote']['as_of'])
        self.assertIn('报价源时间未提供', result['QQQ']['limitation'])
        self.assertEqual(result['QQQ']['quote']['price'], '700.50')
        self.assertIsNone(read(self.root/'valid/evidence.json')['sources'][0]['published_at'])
        self.product['quote']['retrieved_at'] = '2026-09-29T20:00:00Z'
        with self.assertRaisesRegex(Invalid, 'retrieval time differs'):
            self.freeze('wrong-time')

    def test_collector_identity_cannot_support_a_verified_product_price(self):
        self.bundle['sources'][0]['trust'] = 'UNTRUSTED_LEAD'
        with self.assertRaisesRegex(Invalid, 'untrusted lead'):
            self.freeze()

    def test_product_records_need_their_reference_closure_after_compaction(self):
        frozen = self.freeze()
        self.assertEqual(equity_products.retained([frozen], {'public'}), [self.product])
        self.assertEqual(equity_products.retained([frozen], set()), [])

    def test_product_zone_cannot_rename_or_replace_index_history(self):
        from test_market_state import zone
        from v4 import market_state
        old = zone('2026-09-29T20:00:00Z'); old.update(market='NDX',quote_unit='index points')
        changed = deepcopy(old); changed.update(market='QQQ',quote_unit='USD')
        with self.assertRaisesRegex(Invalid, 'cannot be renamed'):
            market_state.validate_zones([changed], lambda ref:None, prior=[old])
        retired = deepcopy(old); retired.update(status='RETIRED',retirement_reason='Synthetic attempted product replacement.')
        changed.update(id='qqq-new-zone',supersedes=[old['id']])
        with self.assertRaisesRegex(Invalid, 'cannot replace'):
            market_state.validate_zones([retired,changed], lambda ref:None, prior=[old])

    def test_public_subjects_reach_market_material_without_personal_fields(self):
        from v4 import market_state
        frozen = self.freeze()
        projected = market_state.market_projection({'identity':{}, 'factor_context':{'evidence':frozen}})
        self.assertEqual(projected['report_subjects']['QQQ']['quote']['price'], '700.50')
        self.assertEqual(projected['report_subjects']['VOO']['underlying_market'], 'SPX')

    def test_index_quote_cannot_be_relabelled_as_product(self):
        for key, ref in self.product['quote']['refs'].items():
            ref.update(pointer='/index/'+key, value_hash=digest(self.raw['index'][key]))
        self.product['quote']['price'] = '30339.33'
        with self.assertRaisesRegex(Invalid, 'quote instrument or unit'):
            self.freeze()

    def test_raw_price_and_identity_tampering_are_rejected(self):
        self.product['quote']['price'] = '701.50'
        with self.assertRaisesRegex(Invalid, 'public raw quote'):
            self.freeze('bad-price')
        self.product['quote']['price'] = '700.50'
        self.product['product_id'] = 'QQQon'
        with self.assertRaisesRegex(Invalid, 'identity differs'):
            self.freeze('bad-id')
