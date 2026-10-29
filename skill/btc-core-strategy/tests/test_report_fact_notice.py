"""Synthetic display acknowledgements; no private facts or model-quality claims."""
from copy import deepcopy
import unittest
from unittest import mock

import test_authored_report as fixture
from v4 import delivery, reporting
from v4.common import Invalid, digest, read, replace


class FactNoticeTests(unittest.TestCase):
    def setUp(self):
        # Only this historical-format contract fixture uses the old write floor;
        # this suite makes no current lifecycle or node-admission claim.
        floor = mock.patch('v4.storage.NEW_WRITE_REVISION', 1)
        floor.start(); self.addCleanup(floor.stop)
        self.a = fixture.AuthoredReportTests(); self.a.setUp()
        self.addCleanup(self.a.doCleanups)
        self.root = self.a.root
        self.version = deepcopy(self.a.f.f.decision['assessment']['fact_version'])
        self.key = next(iter(self.version))
        self.version[self.key] = digest(b'synthetic changed fact')
        original = self.a.f.context

        def context(root):
            value = original(root)
            value.update(market_revision=5, report_contract_revision=3)
            value['identity']['fact_version'] = deepcopy(self.version)
            value['context_id'] = digest(value['identity'])
            return value

        self.a.f.context = context
        mock.patch('v4.reporting.context', side_effect=context).start()
        mock.patch('v4.reporting.facts', side_effect=lambda _: deepcopy(self.version)).start()

    def spec(self, suffix='本次公开观察保留早期迹象与趋势确认的区别。', notice=False):
        quote = 'BTC 最近完成小时收盘 80,001.25 USDT。'
        value = self.a.spec(quote + '\n\n' + suffix)
        value['layout']['bindings'].append({'text': suffix, 'scope': 'market',
                                           'refs': ['/assessment/outlook/narrative']})
        value['layout']['quality_review']['market_independence'] = '正文只用合成公开观察，未从个人事实推导市场方向。'
        value['layout']['price_status'] = {'lower_spot': 'REVIEW_REQUIRED', 'upper_short': 'REVIEW_REQUIRED'}
        if notice:
            line = '个人事实已改变，旧个人建议尚未适配，不作为当前可执行建议。'
            value['layout']['body'] += '\n\n' + line
            value['layout']['bindings'].append({'text': line, 'scope': 'personal',
                                               'refs': ['/assessment/fact_version/' + self.key]})
            value['information']['fact_limitations'] = line
        return value

    def confirm(self, value):
        identity, source = self.a.queue(value)
        self.a.begin(identity, source)
        self.assertEqual(delivery.transition(self.root, identity, 'CONFIRM', self.a.receipt(identity))['state'], 'DELIVERED')
        self.a.f.seq += 1
        return read(self.root / 'reported-current.json')

    def test_first_or_only_queued_notice_does_not_authorize_omission(self):
        with self.assertRaisesRegex(Invalid, 'personal response must explain'):
            self.a.source(self.spec())
        self.a.queue(self.spec(notice=True))
        with self.assertRaisesRegex(Invalid, 'personal response must explain'):
            self.a.source(self.spec('另一项公开观察仍不能把未送达纠错当作已告知。'))

    def test_actual_notice_allows_successive_market_only_reports(self):
        self.confirm(self.spec(notice=True))
        self.assertEqual(reporting.reported(self.root)['disclosed_fact_version'], self.version)
        for line in ('后续公开观察说明反证尚未解除。', '再一次公开观察说明早期迹象仍未确认。'):
            self.confirm(self.spec(line))
            self.assertEqual(reporting.reported(self.root)['disclosed_fact_version'], self.version)

    def test_new_fact_generation_requires_a_new_notice(self):
        self.confirm(self.spec(notice=True))
        self.version[self.key] = digest(b'another synthetic changed fact')
        with self.assertRaisesRegex(Invalid, 'personal response must explain'):
            self.a.source(self.spec())

    def test_delivered_notice_preserves_the_single_terminal_lf_contract(self):
        value = self.spec(notice=True)
        value['layout']['body'] += '\n'
        value['information']['fact_limitations'] += '\n'
        identity, source = self.a.queue(value)
        self.a.begin(identity, source)
        receipt = self.a.receipt(identity, source['body'][:-1])
        result = delivery.transition(self.root, identity, 'CONFIRM', receipt)
        self.assertEqual(result['state'], 'DELIVERED')
        presentation = read(self.root / 'outbox' / identity / result['presentation'])
        self.assertEqual(presentation['text_match'], 'TERMINAL_LF_ONLY')
        self.assertEqual(reporting.reported(self.root)['disclosed_fact_version'], self.version)
        self.a.f.seq += 1
        self.a.source(self.spec('后续市场观察不重复已经送达的个人限制。'))

    def test_personal_report_and_old_advice_keep_their_guards(self):
        self.confirm(self.spec(notice=True))
        value = self.spec('公开观察与个人适用性分别审阅。')
        value['layout']['bindings'][-1]['scope'] = 'personal'
        value['layout']['bindings'][-1]['refs'] = ['/assessment/fact_version/' + self.key]
        with self.assertRaisesRegex(Invalid, 'personal response must explain'):
            self.a.source(value)
        value = self.spec('新的公开观察不恢复旧价位。')
        value['layout']['price_status']['lower_spot'] = 'VALID'
        with self.assertRaisesRegex(Invalid, 'unreviewed or expired advice'):
            self.a.source(value)

    def test_acknowledgement_follows_actual_display_not_coverage_baseline(self):
        baseline = self.confirm(self.spec(notice=True))
        self.version[self.key] = digest(b'fact changed again before actual display')
        self.confirm(self.spec('本次公开观察不同于原覆盖基线。', notice=True))
        replace(self.root / 'reported-current.json', baseline)
        prior = reporting.reported(self.root)
        self.assertFalse(prior['display_matches_baseline'])
        self.assertEqual(prior['disclosed_fact_version'], self.version)
        self.a.source(self.spec('继续公开市场观察，不重复已实际显示的限制。'))


if __name__ == '__main__':
    unittest.main()
