"""ISOLATED SYNTHETIC feedback contracts; no market or performance evidence."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from v4 import evidence, forward_lifecycle as life, public_observations as observations
from v4.common import Invalid, create, digest

T0 = '2024-01-01T00:00:00Z'
T1 = '2024-01-02T00:00:00Z'
T2 = '2024-01-03T00:00:00Z'


class SourceFactsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-system-facts-')
        self.addCleanup(temporary.cleanup); self.root = Path(temporary.name)
        self.bundle = {'sources': []}; self.directory = self.root / 'evidence'

    def source(self, identity, url, raw, start=T0, end=T2, known=T2):
        temporary = self.root / (identity + '.json'); create(temporary, raw)
        raw_hash = digest(temporary.read_bytes())
        create(self.directory / 'raw' / (raw_hash[7:] + '.json'), temporary.read_bytes())
        source = {'id': identity, 'origin': evidence.canonical_origin(url), 'url': url,
                  'trust': 'PUBLIC_RAW', 'start': start, 'end': end,
                  'retrieved_at': known, 'published_at': None, 'raw_hash': raw_hash}
        self.bundle['sources'].append(source)
        return source

    def refs(self, identity):
        return evidence.observation_refs(self.bundle, self.directory, identity)

    def resolved(self, ref):
        return evidence.resolve(self.bundle, self.directory, ref, verified=True)

    def stable(self, identity='later', value='110', stamp=1704153600, known=T2):
        return self.source(identity, 'https://stablecoins.llama.fi/stablecoincharts/all',
            [{'date': str(stamp), 'totalCirculatingUSD': {'peggedUSD': value}}], known=known)

    def fixture(self, value='110', broad=False, stamp=1704153600):
        self.stable('basis', '100', 1704067200, T0)
        basis = self.refs('basis')[0]
        anchor = dict(basis, pointer=basis['pointer'] + '/value', value_hash=digest('100'))
        self.stable(value=value, stamp=stamp)
        later = self.refs('later')[0]
        scope = deepcopy(self.resolved(later)['scope'])
        if broad: scope['product'] = ['peggedUSD', 'peggedEUR']
        old = {'id': 'synthetic-old', 'formed_at': T0, 'data_cutoff': T0, 'scope': scope,
               'basis_kind': 'FACTS', 'basis_refs': [anchor],
               'scale': {'process': 'Observe the actual later stablecoin capacity; not asset deployment.', 'end': None, 'end_ref': None},
               'expectations': [{'id': 'capacity', 'mechanism': 'Only scoped stablecoin capacity.', 'scope': scope,
                   'support': {'left': {'sample': 'later', 'path': '/value'}, 'op': 'GT', 'right': {'anchor': anchor}},
                   'falsification': {'left': {'sample': 'later', 'path': '/value'}, 'op': 'LT', 'right': {'anchor': anchor}}}]}
        owner = self.root / 'original-freeze.json'
        create(owner, {'created_at': T0, 'market_assessment': {'markets': [{'horizons': [{'forward': {'lifecycle': {'claim': old}}}]}]}})
        with mock.patch('v4.forward_lifecycle.now', return_value=T0):
            life.register(self.root, {'markets': [{'horizons': [{'forward': {'lifecycle': {'claim': old}}}]}]}, owner, '/created_at')
        next_claim = deepcopy(old); next_claim.update(id='synthetic-next', formed_at=T2, data_cutoff=T2)
        review = {'prior_claim_hash': digest(old), 'kind': 'FORWARD_OBSERVATION', 'evaluated_at': T2,
                  'samples': [{'id': 'later', 'ref': later, 'time_pointer': '/observed_at',
                               'scope': deepcopy(self.resolved(later)['scope'])}],
                  'outcomes': [{'expectation_id': 'capacity', 'verdict': 'SUPPORTED', 'supported_scope': deepcopy(scope),
                                'explanation': 'Synthetic fixture checks the true source value, not a market claim.'}]}
        return old, next_claim, review

    def verify(self, old, current, review):
        prior = {'forward': {'lifecycle': {'claim': old}}}
        value = {'claim': current, 'reviews': [review], 'dependencies': []}
        with mock.patch('v4.forward_lifecycle.now', return_value=T2):
            resolver = evidence.forward_resolver(self.bundle, self.directory)
            return life.validate(value, T2, resolver, prior, fact_view=resolver.fact, root=self.root)

    def test_capacity_value_is_recomputed_and_does_not_mean_btc_deployment(self):
        old, current, review = self.fixture()
        result = self.verify(old, current, review)
        self.assertEqual(result['verification'][0]['verdict'], 'SUPPORTED')
        fact = evidence.forward_resolver(self.bundle, self.directory).fact(review['samples'][0]['ref'], None)
        self.assertEqual(fact['scope']['measure'], 'CIRCULATING_CAPITALIZATION')
        self.assertEqual(fact['scope']['subject'], 'STABLECOINS')
        self.assertEqual(fact['known_at'], T2)
        self.assertEqual(fact['observed_at'], '2024-01-02T00:00:00+00:00')

    def test_partial_support_retains_actual_product_and_period(self):
        old, current, review = self.fixture(broad=True)
        review['outcomes'][0].update(verdict='PARTIAL', supported_scope=self.resolved(review['samples'][0]['ref'])['scope'])
        result = self.verify(old, current, review)
        self.assertEqual(result['verification'][0]['verdict'], 'PARTIAL')

    def test_process_expectation_needs_no_preselected_next_observation_date(self):
        old, current, review = self.fixture()
        old = deepcopy(old); old['id'] = 'synthetic-open-process'
        old['scope'].pop('period'); old['expectations'][0]['scope'] = deepcopy(old['scope'])
        market = {'markets': [{'horizons': [{'forward': {'lifecycle': {'claim': old}}}]}]}
        owner = self.root / 'open-process-freeze.json'; create(owner, {'created_at': T0, 'market_assessment': market})
        with mock.patch('v4.forward_lifecycle.now', return_value=T0): life.register(self.root, market, owner, '/created_at')
        current = deepcopy(old); current.update(id='process-next', formed_at=T2, data_cutoff=T2)
        review['prior_claim_hash'] = digest(old); review['outcomes'][0]['supported_scope'] = deepcopy(old['scope'])
        result = self.verify(old, current, review)
        self.assertEqual(result['verification'][0]['verdict'], 'SUPPORTED')
        self.assertIsNone(old['scale']['end'])
        self.assertNotIn('period', old['scope'])
        self.assertEqual(self.resolved(review['samples'][0]['ref'])['scope']['period'], '2024-01-02')

    def test_explicit_wrong_period_neither_supports_nor_falsifies(self):
        old, current, review = self.fixture(value='90')
        old = deepcopy(old); old['id'] = 'synthetic-explicit-other-date'
        old['scope']['period'] = '2024-01-03'; old['expectations'][0]['scope'] = deepcopy(old['scope'])
        market = {'markets': [{'horizons': [{'forward': {'lifecycle': {'claim': old}}}]}]}
        owner = self.root / 'wrong-date-freeze.json'; create(owner, {'created_at': T0, 'market_assessment': market})
        with mock.patch('v4.forward_lifecycle.now', return_value=T0): life.register(self.root, market, owner, '/created_at')
        current = deepcopy(old); current.update(id='other-date-next', formed_at=T2, data_cutoff=T2)
        review['prior_claim_hash'] = digest(old); review['outcomes'][0].update(verdict='FALSIFIED', supported_scope={})
        with self.assertRaisesRegex(Invalid, 'unverified response'): self.verify(old, current, review)
        review['outcomes'][0].update(verdict='SUPPORTED', supported_scope=deepcopy(old['scope']))
        with self.assertRaisesRegex(Invalid, 'scope'): self.verify(old, current, review)
        review['outcomes'][0].update(verdict='UNKNOWN', supported_scope={})
        self.assertEqual(self.verify(old, current, review)['verification'][0]['verdict'], 'UNKNOWN')

    def test_actual_falsifier_requires_falsified_not_supported(self):
        old, current, review = self.fixture(value='90')
        with self.assertRaisesRegex(Invalid, 'falsified'): self.verify(old, current, review)
        review['outcomes'][0].update(verdict='FALSIFIED', supported_scope={})
        self.assertEqual(self.verify(old, current, review)['verification'][0]['verdict'], 'FALSIFIED')

    def test_unknown_source_value_stays_unknown(self):
        old, current, review = self.fixture(value='N/A')
        review['outcomes'][0].update(verdict='UNKNOWN', supported_scope={})
        self.assertEqual(self.verify(old, current, review)['verification'][0]['verdict'], 'UNKNOWN')
        review['outcomes'][0].update(verdict='SUPPORTED', supported_scope=deepcopy(old['scope']))
        with self.assertRaisesRegex(Invalid, 'unavailable'): self.verify(old, current, review)

    def test_wrong_measure_cannot_falsify_other_mechanism(self):
        old, current, review = self.fixture(value='90')
        # Re-register an initial claim about actual deployment rather than
        # capacity, without rewriting an already registered predecessor.
        old = deepcopy(old); old['id'] = 'synthetic-deployment'; old['scope']['measure'] = 'NET_FUND_FLOW'
        old['expectations'][0]['scope'] = deepcopy(old['scope'])
        owner = self.root / 'deployment-freeze.json'
        market = {'markets': [{'horizons': [{'forward': {'lifecycle': {'claim': old}}}]}]}
        create(owner, {'created_at': T0, 'market_assessment': market})
        with mock.patch('v4.forward_lifecycle.now', return_value=T0): life.register(self.root, market, owner, '/created_at')
        current = deepcopy(old); current.update(id='deployment-next', formed_at=T2, data_cutoff=T2)
        review['prior_claim_hash'] = digest(old)
        review['outcomes'][0].update(verdict='FALSIFIED', supported_scope={})
        with self.assertRaisesRegex(Invalid, 'unverified response'): self.verify(old, current, review)
        review['outcomes'][0].update(verdict='UNKNOWN', supported_scope={})
        self.assertEqual(self.verify(old, current, review)['verification'][0]['verdict'], 'UNKNOWN')

    def test_scope_limited_value_cannot_support_broader_claim(self):
        old, current, review = self.fixture(broad=True)
        with self.assertRaisesRegex(Invalid, 'scope'): self.verify(old, current, review)

    def test_late_read_old_capacity_is_not_later_fact(self):
        old, current, review = self.fixture(stamp=1704067200)
        review['outcomes'][0].update(verdict='UNKNOWN', supported_scope={})
        self.assertEqual(self.verify(old, current, review)['verification'][0]['verdict'], 'UNKNOWN')

    def test_model_fact_kind_does_not_classify_arbitrary_json(self):
        row = {'kind': 'EXTERNAL_OBSERVATION', 'value': '110', 'date': '2024-01-02'}
        self.source('fake', 'https://www.gold.org/goldhub/research/gold-etfs-holdings-and-flows/2024/01',
            {'row': row})
        self.assertEqual(self.refs('fake'), [])
        ref = {'source_id': 'fake', 'pointer': '/row', 'value_hash': digest(row)}
        self.assertEqual(evidence.forward_resolver(self.bundle, self.directory).fact(ref, '/date')['kind'], 'MATERIAL')

    def test_changed_raw_and_changed_derived_hash_are_rejected(self):
        self.stable(); ref = self.refs('later')[0]
        bad = dict(ref, value_hash=digest('invented'))
        with self.assertRaisesRegex(Invalid, 'reference mismatch'): self.resolved(bad)
        source = self.bundle['sources'][0]; path = self.directory / 'raw' / (source['raw_hash'][7:] + '.json')
        path.write_text('[]')
        with self.assertRaisesRegex(Invalid, 'changed'): self.resolved(ref)

    def test_original_transport_hash_and_literal_cannot_be_forged(self):
        raw = observations.document_transport(b'<html>original</html>')
        raw['literal_text'] = '<html>invented</html>'
        self.source('bad', 'https://www.gold.org/goldhub/research/gold-etfs-holdings-and-flows/2024/01', raw)
        with self.assertRaisesRegex(Invalid, 'literal text'): self.refs('bad')

    def test_numeric_projection_preserves_original_decimal_token(self):
        payload = b'[{"date":"1704153600","totalCirculatingUSD":{"peggedUSD":100.00000000000000001}}]'
        source = self.source('exact', 'https://stablecoins.llama.fi/stablecoincharts/all', [])
        source['raw_hash'] = digest(payload)
        create(self.directory / 'raw' / (source['raw_hash'][7:] + '.json'), payload)
        ref = self.refs('exact')[0]; observation = self.resolved(ref)
        self.assertEqual(observation['value'], '100.00000000000000001')
        anchor = dict(ref, pointer=ref['pointer'] + '/value', value_hash=digest(observation['value']))
        self.assertEqual(evidence.resolve_number(self.bundle, self.directory, anchor, verified=True), '100.00000000000000001')

    def test_treasury_real_and_nominal_values_keep_maturity_unit_and_event_clock(self):
        xml = b'<feed xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"><properties><d:NEW_DATE>2024-01-02T00:00:00</d:NEW_DATE><d:TC_10YEAR>2.91</d:TC_10YEAR><d:TC_5YEAR>2.00</d:TC_5YEAR></properties></feed>'
        self.source('real', 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_real_yield_curve', observations.document_transport(xml))
        values = [self.resolved(r) for r in self.refs('real')]
        self.assertEqual(len(values), 2)
        self.assertEqual(values[0]['value'], '2.91')
        self.assertEqual(values[0]['scope']['measure'], 'REAL_YIELD')
        self.assertEqual(values[0]['scope']['maturity'], '10YEAR')
        self.assertEqual(values[0]['scope']['unit'], 'ANNUAL_PERCENT')

    @staticmethod
    def farside_html(values=None, columns=None):
        # Generated synthetic rows; these are not captured market evidence.
        from html import escape
        funds = 'IBIT FBTC BITB ARKB BTCO EZBC BRRR HODL BTCW MSBT GBTC BTC'.split()
        cells = dict.fromkeys(funds, '0.0')
        cells.update(Date='02 Jan 2024', IBIT='120.0', FBTC='10.0', Total='130.0')
        cells.update(values or {})
        columns = columns if columns is not None else ['Date', *funds, 'Total']
        return ('<title>Bitcoin ETF Flow (US$m)</title><table><tr>' +
                ''.join('<th>' + escape(name) + '</th>' for name in columns) + '</tr><tr>' +
                ''.join('<td>' + escape(cells[name]) + '</td>' for name in columns) + '</tr></table>').encode()

    def flow_fixture(self, values=None, columns=None):
        old, current, review = self.fixture()
        self.source('flow', 'https://farside.co.uk/btc/',
                    observations.document_transport(self.farside_html(values, columns)))
        later = self.refs('flow')[0]; record = self.resolved(later)
        scoped = deepcopy(record['scope'])
        old = deepcopy(old)
        old.update(id='synthetic-farside-total', scope=deepcopy(scoped), observation_relations=[])
        old['expectations'][0]['scope'] = deepcopy(scoped)
        market = {'markets': [{'horizons': [{'forward': {'lifecycle': {'claim': old}}}]}]}
        owner = self.root / 'farside-freeze.json'; create(owner, {'created_at': T0, 'market_assessment': market})
        with mock.patch('v4.forward_lifecycle.now', return_value=T0): life.register(self.root, market, owner, '/created_at')
        current = deepcopy(old); current.update(id='farside-next', formed_at=T2, data_cutoff=T2)
        review['prior_claim_hash'] = digest(old)
        review['samples'][0].update(ref=later, scope=deepcopy(scoped))
        review['outcomes'][0].update(verdict='SUPPORTED', supported_scope=deepcopy(scoped))
        return old, current, review, record

    def test_farside_missing_ibit_cannot_grant_complete_prior_support(self):
        old, current, review, value = self.flow_fixture({'IBIT': '-'})
        self.assertIsNone(value['value'])
        self.assertEqual(value['coverage']['status'], 'INCOMPLETE')
        self.assertEqual(value['coverage']['reported_total'], '130.0')
        self.assertEqual(value['coverage']['missing_values'], ['IBIT'])
        self.assertIsNone(value['coverage']['constituents']['IBIT'])
        with self.assertRaisesRegex(Invalid, 'unavailable'): self.verify(old, current, review)
        review['outcomes'][0].update(verdict='UNKNOWN', supported_scope={})
        result = self.verify(old, current, review)
        self.assertFalse(result['complete_prior_support'])
        self.assertEqual(result['component_support'], [])
        self.assertEqual(result['verification'][0]['verdict'], 'UNKNOWN')

    def test_farside_explicit_zero_is_known_and_can_support_full_scope(self):
        old, current, review, value = self.flow_fixture({'IBIT': '0.0', 'FBTC': '130.0'})
        self.assertEqual(value['value'], '130.0')
        self.assertEqual(value['coverage']['status'], 'COMPLETE')
        self.assertEqual(value['coverage']['constituents']['IBIT'], '0.0')
        self.assertTrue(self.verify(old, current, review)['complete_prior_support'])

    def test_farside_all_zero_is_numeric_not_missing(self):
        raw = observations.document_transport(self.farside_html({'IBIT': '0.0', 'FBTC': '0.0', 'Total': '0.0'}))
        self.source('flow', 'https://farside.co.uk/btc/', raw)
        ref = self.refs('flow')[0]; value = self.resolved(ref)
        self.assertEqual(value['value'], '0.0')
        self.assertEqual(value['coverage']['status'], 'COMPLETE')
        self.assertNotIn('/value', evidence.forward_resolver(self.bundle, self.directory).fact(ref, None)['field_errors'])

    def test_farside_missing_or_nonfinite_cells_are_not_zero(self):
        for index, token in enumerate(('', '-', '—', 'N/A', 'NaN', 'Infinity')):
            with self.subTest(token=token):
                identity = 'flow-' + str(index)
                self.source(identity, 'https://farside.co.uk/btc/',
                    observations.document_transport(self.farside_html({'IBIT': token})))
                ref = self.refs(identity)[0]; value = self.resolved(ref)
                self.assertIsNone(value['value'])
                self.assertIsNone(value['coverage']['constituents']['IBIT'])
                self.assertIn('/value', evidence.forward_resolver(self.bundle, self.directory).fact(ref, None)['field_errors'])

    def test_farside_missing_named_columns_cannot_create_a_complete_total(self):
        funds = 'IBIT FBTC BITB ARKB BTCO EZBC BRRR HODL BTCW MSBT GBTC BTC'.split()
        for missing in funds:
            with self.subTest(column=missing):
                columns = ['Date', *(fund for fund in funds if fund != missing), 'Total']
                self.source(missing, 'https://farside.co.uk/btc/',
                    observations.document_transport(self.farside_html(columns=columns)))
                value = self.resolved(self.refs(missing)[0])
                self.assertIsNone(value['value'])
                self.assertIn(missing, value['coverage']['missing_columns'])

    def test_farside_reordered_columns_keep_msbt_and_btcw_identity(self):
        columns = 'Total BTC MSBT Date FBTC GBTC BTCW HODL BRRR EZBC BTCO ARKB BITB IBIT'.split()
        self.source('flow', 'https://farside.co.uk/btc/', observations.document_transport(
            self.farside_html({'IBIT': '0.0', 'FBTC': '123.3', 'MSBT': '6.7'}, columns)))
        value = self.resolved(self.refs('flow')[0])
        self.assertEqual(value['value'], '130.0')
        self.assertEqual(value['coverage']['constituents']['BTCW'], '0.0')
        self.assertEqual(value['coverage']['constituents']['MSBT'], '6.7')

    def test_farside_new_named_fund_participates_in_completeness(self):
        columns = 'Date ADDED IBIT FBTC BITB ARKB BTCO EZBC BRRR HODL BTCW MSBT GBTC BTC Total'.split()
        for identity, added, expected in [('known', '7.0', '137.0'), ('missing', '-', None)]:
            self.source(identity, 'https://farside.co.uk/btc/', observations.document_transport(
                self.farside_html({'ADDED': added, 'Total': '137.0'}, columns)))
            value = self.resolved(self.refs(identity)[0])
            self.assertEqual(value['value'], expected)
            self.assertIn('ADDED', value['coverage']['constituents'])
            self.assertEqual(value['coverage']['missing_values'], [] if expected else ['ADDED'])

    def test_farside_missing_or_inconsistent_total_is_never_reconstructed(self):
        cases = [('missing', {'Total': '-'}, None), ('inconsistent', {'Total': '129.0'}, None),
                 ('column', {}, 'Date IBIT FBTC BITB ARKB BTCO EZBC BRRR HODL BTCW MSBT GBTC BTC'.split())]
        for identity, values, columns in cases:
            self.source(identity, 'https://farside.co.uk/btc/',
                observations.document_transport(self.farside_html(values, columns)))
            value = self.resolved(self.refs(identity)[0])
            self.assertIsNone(value['value'])
            self.assertEqual(value['coverage']['status'], 'INCOMPLETE')

    def test_farside_ambiguous_or_absent_headers_and_ragged_rows_grant_no_fact(self):
        good = self.farside_html()
        cases = [good.replace(b'<th>IBIT</th>', b'<th>FBTC</th>'),
                 good.replace(b'<th>Date</th>', b'<th>Unidentified</th>'),
                 good.replace(b'<td>120.0</td>', b''),
                 good.replace(b'<td>120.0</td>', b'<td colspan="2">120.0</td>'),
                 good.replace(b'<th>IBIT</th>', b'<th rowspan="2">IBIT</th>'),
                 b'<title>Bitcoin ETF Flow (US$m)</title><table><tr><td>02 Jan 2024</td><td>130.0</td></tr></table>']
        for index, payload in enumerate(cases):
            identity = 'bad-' + str(index)
            self.source(identity, 'https://farside.co.uk/btc/', observations.document_transport(payload))
            self.assertEqual(self.refs(identity), [])

    def test_farside_blank_date_header_retains_named_fund_mapping(self):
        payload = self.farside_html().replace(b'<th>Date</th>', b'<th></th>')
        self.source('flow', 'https://farside.co.uk/btc/', observations.document_transport(payload))
        value = self.resolved(self.refs('flow')[0])
        self.assertEqual(value['value'], '130.0')
        self.assertEqual(value['coverage']['constituents']['IBIT'], '120.0')

    def test_farside_reported_subtotal_metadata_cannot_bypass_unknown_value(self):
        old, current, review, value = self.flow_fixture({'IBIT': '-'})
        old = deepcopy(old); old['id'] = 'synthetic-metadata-is-not-full-flow'
        for condition in ('support', 'falsification'):
            old['expectations'][0][condition]['left']['path'] = '/coverage/reported_total'
        market = {'markets': [{'horizons': [{'forward': {'lifecycle': {'claim': old}}}]}]}
        owner = self.root / 'metadata-freeze.json'; create(owner, {'created_at': T0, 'market_assessment': market})
        with mock.patch('v4.forward_lifecycle.now', return_value=T0): life.register(self.root, market, owner, '/created_at')
        current = deepcopy(old); current.update(id='metadata-next', formed_at=T2, data_cutoff=T2)
        review['prior_claim_hash'] = digest(old)
        with self.assertRaisesRegex(Invalid, 'unclassified'): self.verify(old, current, review)
        review['outcomes'][0].update(verdict='UNKNOWN', supported_scope={})
        self.assertFalse(self.verify(old, current, review)['complete_prior_support'])

    def test_farside_does_not_borrow_headers_from_another_table(self):
        good = self.farside_html()
        marker = good.index(b'</tr>') + len(b'</tr>')
        payload = good[:marker] + b'</table><table>' + good[marker:]
        self.source('flow', 'https://farside.co.uk/btc/', observations.document_transport(payload))
        self.assertEqual(self.refs('flow'), [])

    def test_farside_actual_total_keeps_daily_window_and_million_usd(self):
        html = self.farside_html({'IBIT': '(30.5)', 'Total': '(20.5)'})
        self.source('flow', 'https://farside.co.uk/btc/', observations.document_transport(html))
        value = self.resolved(self.refs('flow')[0])
        self.assertEqual(value['value'], '-20.5')
        self.assertEqual(value['scope']['unit'], 'USD_MILLION')
        self.assertEqual(value['economic_period']['end'], '2024-01-02T23:59:59+00:00')

    def test_wgc_deployment_and_holdings_are_distinct_from_price_and_aum(self):
        html = b'<h1>Gold ETF Flows: December 2023</h1><time datetime="2024-01-02T12:00:00Z">Published</time><p>Global physically-backed gold ETFs attracted US$10bn in December. Despite a lower gold price, global holdings rose 67t to a record 4,256t, while total AUM declined 7%.</p>'
        self.source('gold', 'https://www.gold.org/goldhub/research/gold-etfs-holdings-and-flows/2024/01', observations.document_transport(html), start='2023-12-01T00:00:00Z')
        values = [self.resolved(r) for r in self.refs('gold')]
        self.assertEqual({v['scope']['measure'] for v in values}, {'NET_FUND_FLOW', 'HOLDINGS_CHANGE', 'HOLDINGS'})
        self.assertEqual(values[0]['value'], '10')
        self.assertEqual(values[0]['known_at'], T2)
        self.assertEqual(values[0]['available_at'], '2024-01-02T12:00:00+00:00')
        self.assertEqual(values[0]['observed_at'], '2023-12-31T23:59:59+00:00')

    def test_wgc_literal_outflow_and_holdings_decline_keep_negative_values(self):
        html = b'<h1>Gold ETF Flows: December 2023</h1><time datetime="2024-01-02T12:00:00Z">Published</time><p>Global physically-backed gold ETFs saw outflows of US$5bn in December. Global holdings fell 70t to 4,000t.</p>'
        self.source('gold', 'https://www.gold.org/goldhub/research/gold-etfs-holdings-and-flows/2024/01', observations.document_transport(html), start='2023-12-01T00:00:00Z')
        values = {self.resolved(r)['scope']['measure']: self.resolved(r)['value'] for r in self.refs('gold')}
        self.assertEqual(values, {'NET_FUND_FLOW': '-5', 'HOLDINGS_CHANGE': '-70', 'HOLDINGS': '4000'})

    def test_unverified_legacy_text_and_model_extraction_do_not_produce_observations(self):
        self.source('legacy', 'https://www.nasdaq.com/economic-institute/the-print/test',
            {'content': 'In 2023, More than four in five reported positive earnings growth',
             'source_html_sha256': '0' * 64, 'extraction': 'author text'})
        self.assertEqual(self.refs('legacy'), [])

    def test_nonfinite_numeric_fields_do_not_gain_support(self):
        old, current, review = self.fixture(value='NaN')
        review['outcomes'][0].update(verdict='UNKNOWN', supported_scope={})
        self.assertEqual(self.verify(old, current, review)['verification'][0]['verdict'], 'UNKNOWN')

    def test_midperiod_formation_cannot_claim_full_daily_flow_as_later_response(self):
        old, current, review = self.fixture()
        html = self.farside_html({'IBIT': '100.0', 'Total': '110.0'})
        self.source('flow', 'https://farside.co.uk/btc/', observations.document_transport(html))
        later = self.refs('flow')[0]; scoped = self.resolved(later)['scope']
        old = deepcopy(old); old.update(id='synthetic-full-day-flow', formed_at='2024-01-02T12:00:00Z', scope=deepcopy(scoped))
        old['expectations'][0]['scope'] = deepcopy(scoped)
        owner = self.root / 'flow-freeze.json'; market = {'markets': [{'horizons': [{'forward': {'lifecycle': {'claim': old}}}]}]}
        create(owner, {'created_at': old['formed_at'], 'market_assessment': market})
        with mock.patch('v4.forward_lifecycle.now', return_value=old['formed_at']): life.register(self.root, market, owner, '/created_at')
        current = deepcopy(old); current.update(id='flow-next', formed_at=T2, data_cutoff=T2)
        review.update(prior_claim_hash=digest(old))
        review['samples'][0].update(ref=later, scope=scoped)
        review['outcomes'][0].update(verdict='SUPPORTED', supported_scope=scoped)
        with self.assertRaisesRegex(Invalid, 'pre-formation'): self.verify(old, current, review)
        review['outcomes'][0].update(verdict='UNKNOWN', supported_scope={})
        self.assertEqual(self.verify(old, current, review)['verification'][0]['verdict'], 'UNKNOWN')

    @staticmethod
    def pdf(text):
        # Small source document assembled in the temporary test, not a stored
        # fixture or encoded copy of a user's real research data.
        contents = ('BT /F1 12 Tf 10 700 Td (' + text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') + ') Tj ET').encode('ascii')
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Count 1 /Kids [3 0 R] >>',
                   b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
                   b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
                   b'<< /Length ' + str(len(contents)).encode() + b' >>\nstream\n' + contents + b'\nendstream']
        result = b'%PDF-1.4\n'; offsets = [0]
        for index, item in enumerate(objects, 1):
            offsets.append(len(result)); result += str(index).encode() + b' 0 obj\n' + item + b'\nendobj\n'
        start = len(result); result += b'xref\n0 6\n0000000000 65535 f \n'
        result += b''.join(('%010d 00000 n \n' % offset).encode() for offset in offsets[1:])
        return result + b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n' + str(start).encode() + b'\n%%EOF\n'

    def test_factset_original_pdf_separates_actual_reporters_from_estimated_growth(self):
        try: import pypdf
        except ImportError: self.skipTest('optional PDF parser unavailable; production returns no classified PDF facts')
        text = ('January 2, 2024 For Q3 2023 (with 19 S&P 500 companies reporting actual results), '
                '16 S&P 500 companies have reported a positive EPS surprise and 14 S&P 500 companies has reported a positive revenue surprise. '
                'For Q4 2023, the estimated (year-over-year) earnings growth rate for the S&P 500 is 29.6%.')
        raw = observations.document_transport(self.pdf(text)); raw['derived_literal_text'] = '999 companies; invented author interpretation'
        self.source('factset', 'https://advantage.factset.com/hubfs/Research/EarningsInsight_010224.pdf', raw)
        refs = self.refs('factset'); values = [self.resolved(r) for r in refs]
        actual = [v for v in values if v['kind'] == 'EXTERNAL_OBSERVATION']
        estimate = [v for v in values if v['kind'] == 'FORECAST']
        self.assertEqual([v['value'] for v in actual], ['19', '16', '14'])
        self.assertEqual(estimate[0]['value'], '29.6')
        self.assertEqual(estimate[0]['scope']['measure'], 'ESTIMATED_EARNINGS_GROWTH')
        forecast_ref = next(r for r in refs if self.resolved(r)['kind'] == 'FORECAST')
        self.assertEqual(evidence.forward_resolver(self.bundle, self.directory).fact(forecast_ref, None)['kind'], 'FORECAST')

    def test_pdf_without_parser_does_not_trust_attached_text(self):
        raw = observations.document_transport(self.pdf('January 2, 2024'))
        raw['derived_literal_text'] = 'For Q3 2023 (with 999 S&P 500 companies reporting actual results)'
        self.source('factset', 'https://advantage.factset.com/hubfs/Research/EarningsInsight_010224.pdf', raw)
        with mock.patch.dict('sys.modules', {'pypdf': None}): self.assertEqual(self.refs('factset'), [])

    def test_later_report_of_other_quarter_cannot_falsify_explicit_quarter(self):
        try: import pypdf
        except ImportError: self.skipTest('optional PDF parser unavailable')
        old, current, review = self.fixture()
        text = ('January 2, 2024 For Q4 2023 (with 19 S&P 500 companies reporting actual results), '
                '16 S&P 500 companies have reported a positive EPS surprise and 14 S&P 500 companies has reported a positive revenue surprise.')
        self.source('factset', 'https://advantage.factset.com/hubfs/Research/EarningsInsight_010224.pdf', observations.document_transport(self.pdf(text)))
        later = self.refs('factset')[0]; scoped = self.resolved(later)['scope']
        old = deepcopy(old); old.update(id='synthetic-exact-quarter', scope=deepcopy(scoped))
        old['scope']['period'] = '2023-Q3'; old['expectations'][0]['scope'] = deepcopy(old['scope'])
        owner = self.root / 'quarter-freeze.json'; market = {'markets': [{'horizons': [{'forward': {'lifecycle': {'claim': old}}}]}]}
        create(owner, {'created_at': T0, 'market_assessment': market})
        with mock.patch('v4.forward_lifecycle.now', return_value=T0): life.register(self.root, market, owner, '/created_at')
        current = deepcopy(old); current.update(id='quarter-next', formed_at=T2, data_cutoff=T2)
        review['prior_claim_hash'] = digest(old); review['samples'][0].update(ref=later, scope=scoped)
        review['outcomes'][0].update(verdict='FALSIFIED', supported_scope={})
        with self.assertRaisesRegex(Invalid, 'unverified response'): self.verify(old, current, review)
        review['outcomes'][0].update(verdict='UNKNOWN', supported_scope={})
        self.assertEqual(self.verify(old, current, review)['verification'][0]['verdict'], 'UNKNOWN')

    @staticmethod
    def yahoo(symbol='QQQ', value='110'):
        return {'chart': {'error': None, 'result': [{'meta': {'currency': 'USD', 'symbol': symbol,
                'instrumentType': 'ETF' if symbol in ('QQQ', 'VOO', 'GLD') else 'INDEX',
                'exchangeName': 'NGM' if symbol == 'QQQ' else 'PCX' if symbol in ('VOO', 'GLD') else 'SNP' if symbol == '^GSPC' else 'NIM',
                'dataGranularity': '1d', 'currentTradingPeriod': {'regular': {'start': 1704153600, 'end': 1704211200}}},
                'timestamp': [1704153600], 'indicators': {'quote': [{'close': [value]}]}}]}}

    def test_yahoo_actual_daily_close_retains_symbol_product_and_source_session_clock(self):
        for symbol, subject, unit in [('^GSPC', 'SPX', 'INDEX_POINTS'), ('^NDX', 'NDX', 'INDEX_POINTS'),
                ('QQQ', 'QQQ', 'USD_PER_SHARE'), ('VOO', 'VOO', 'USD_PER_SHARE'), ('GLD', 'GLD', 'USD_PER_SHARE')]:
            self.source(subject, 'https://query1.finance.yahoo.com/v8/finance/chart/' + symbol, self.yahoo(symbol))
            ref = self.refs(subject)[0]; fact = evidence.forward_resolver(self.bundle, self.directory).fact(ref, None)
            self.assertEqual(fact['scope']['subject'], subject)
            self.assertEqual(fact['scope']['quote_unit'], 'USD')
            self.assertEqual(fact['scope']['unit'], unit)
            self.assertEqual(fact['observed_at'], '2024-01-02T16:00:00+00:00')
            self.assertEqual(fact['known_at'], T2)

    def test_yahoo_unknown_symbol_currency_session_or_forecast_does_not_classify(self):
        cases = []
        for field, value in [('symbol', 'VOO'), ('exchangeName', 'UNKNOWN'), ('currency', 'EUR'), ('instrumentType', 'FUTURE'), ('dataGranularity', '1h')]:
            raw = self.yahoo(); raw['chart']['result'][0]['meta'][field] = value; cases.append(raw)
        raw = self.yahoo(); raw['kind'] = 'FORECAST'; cases.append(raw)
        raw = self.yahoo(); raw['chart']['result'][0]['meta'].pop('symbol'); cases.append(raw)
        raw = self.yahoo(); raw['chart']['result'][0]['meta'].pop('currentTradingPeriod'); cases.append(raw)
        for index, raw in enumerate(cases):
            sid = 'unclassified-' + str(index)
            self.source(sid, 'https://query1.finance.yahoo.com/v8/finance/chart/QQQ', raw)
            self.assertEqual(self.refs(sid), [])

    def test_yahoo_null_close_is_unavailable_and_untrusted_container_cannot_grant(self):
        source = self.source('null', 'https://query1.finance.yahoo.com/v8/finance/chart/QQQ', self.yahoo(value=None))
        ref = self.refs('null')[0]
        self.assertIn('/value', evidence.forward_resolver(self.bundle, self.directory).fact(ref, None)['field_errors'])
        source['trust'] = 'UNTRUSTED_LEAD'
        with self.assertRaisesRegex(Invalid, 'public source'): self.refs('null')
        with self.assertRaisesRegex(Invalid, 'untrusted lead'): evidence.forward_resolver(self.bundle, self.directory).fact(ref, None)

    def test_yahoo_closed_price_after_intraday_formation_is_a_point_not_preformation_flow(self):
        old, current, review = self.fixture()
        self.source('yahoo', 'https://query1.finance.yahoo.com/v8/finance/chart/QQQ', self.yahoo())
        ref = self.refs('yahoo')[0]; scoped = self.resolved(ref)['scope']
        scoped.pop('period')  # next actual session date is deliberately not fixed
        old = deepcopy(old); old.update(id='synthetic-yahoo-price', formed_at='2024-01-02T12:00:00Z', scope=deepcopy(scoped))
        old['expectations'][0]['scope'] = deepcopy(scoped)
        owner = self.root / 'yahoo-freeze.json'; market = {'markets': [{'horizons': [{'forward': {'lifecycle': {'claim': old}}}]}]}
        create(owner, {'created_at': old['formed_at'], 'market_assessment': market})
        with mock.patch('v4.forward_lifecycle.now', return_value=old['formed_at']): life.register(self.root, market, owner, '/created_at')
        current = deepcopy(old); current.update(id='yahoo-next', formed_at=T2, data_cutoff=T2)
        review['prior_claim_hash'] = digest(old); review['samples'][0].update(ref=ref, scope=deepcopy(scoped))
        review['outcomes'][0].update(verdict='SUPPORTED', supported_scope=deepcopy(scoped))
        self.assertEqual(self.verify(old, current, review)['verification'][0]['verdict'], 'SUPPORTED')
        # It cannot be a response to a judgement first formed after the close.
        old = deepcopy(old); old.update(id='synthetic-late-yahoo-price', formed_at='2024-01-02T18:00:00Z')
        owner = self.root / 'late-yahoo-freeze.json'; market = {'markets': [{'horizons': [{'forward': {'lifecycle': {'claim': old}}}]}]}
        create(owner, {'created_at': old['formed_at'], 'market_assessment': market})
        with mock.patch('v4.forward_lifecycle.now', return_value=old['formed_at']): life.register(self.root, market, owner, '/created_at')
        current = deepcopy(old); current.update(id='late-yahoo-next', formed_at=T2, data_cutoff=T2)
        review['prior_claim_hash'] = digest(old); review['outcomes'][0].update(verdict='UNKNOWN', supported_scope={})
        self.assertEqual(self.verify(old, current, review)['verification'][0]['verdict'], 'UNKNOWN')


if __name__ == '__main__': unittest.main()
