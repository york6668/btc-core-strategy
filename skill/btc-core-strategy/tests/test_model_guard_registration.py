"""Research registration reads the node-owned daily slot; no model is launched."""
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

import model_daily
from v4 import research
from v4.common import Invalid, digest


class ModelGuardRegistrationTest(unittest.TestCase):
    def test_missing_or_wrong_completed_daily_claim_cannot_register_research(self):
        with tempfile.TemporaryDirectory() as directory:
            proof = Path(directory) / 'model.jsonl'
            proof.write_text('actual synthetic model proof')
            self._check_registration(proof)

    def _check_registration(self, proof):
        req = {'issue_id': digest({'issue_key':'BTC_CYCLE'})}
        record = {'claim_id':'btc-deep-original','issue_id':req['issue_id'],
                  'scope':'btc/research','phase':'PUBLIC','model':'gpt-6-astra',
                  'state':'COMPLETE','result_ref':model_daily.sha256(proof)}
        state = {'days':{'2026-10-12':record},'faults':{}}
        with patch.object(model_daily, 'status', return_value=state):
            with self.assertRaisesRegex(Invalid, 'completed original'):
                research._guard_completed(None,req,'PUBLIC',None,proof)
            research._guard_completed(None,req,'PUBLIC','btc-deep-original',proof)
            with self.assertRaisesRegex(Invalid, 'completed original'):
                research._guard_completed(None,req,'APPROVAL','btc-deep-original',proof)
            state['faults']['f']={'state':'OPEN','scope':'btc/research'}
            with self.assertRaisesRegex(Invalid, 'open defect'):
                research._guard_completed(None,req,'PUBLIC','btc-deep-original',proof)


if __name__ == '__main__':
    unittest.main()
