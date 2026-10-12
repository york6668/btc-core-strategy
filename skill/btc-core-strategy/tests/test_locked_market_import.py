"""Clearly synthetic response reconstruction, preserving collection provenance."""
import base64,json,unittest,sys,hashlib
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlencode
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import market_observation as m

class LockedImportTests(unittest.TestCase):
    def setUp(self):
        self.cutoff=1788436800
        self.ds=m.spec('binance_spot_1h','binance_spot')
        self.params={'symbol':'BTCUSDT','interval':'1h','limit':2,'endTime':self.cutoff*1000-1}
        rows=[[t*1000,'100','103','99','102','10',(t+3600)*1000-1,'1000',30,'4','400','0'] for t in (self.cutoff-7200,self.cutoff-3600)]
        body=json.dumps(rows).encode()
        self.inputs={'binance_spot_1h':{'source_id':'SYNTHETIC_TEST_RESPONSE','url':'https://data-api.binance.vision/api/v3/klines?'+urlencode(self.params),
           'retrieved_at':m.iso(self.cutoff+60),'raw_hash':'sha256:'+hashlib.sha256(body).hexdigest(),'bytes_base64':base64.b64encode(body).decode()}}

    def test_real_request_and_clock_are_preserved_and_missing_dimensions_are_not_retrieved(self):
        out=m.normalize_locked(m.iso(self.cutoff),self.inputs);m.validate_evidence(out)
        self.assertEqual(out['retrievals']['binance_spot_1h']['parameters'],{k:str(v) for k,v in self.params.items()})
        self.assertEqual(out['retrievals']['binance_spot_1h']['retrieved_at'],m.iso(self.cutoff+60))
        self.assertEqual(out['retrievals']['binance_index_1h']['status'],'NOT_IN_LOCK')
        self.assertFalse(out['datasets']['binance_spot_1h']['coverage']['complete'])
        self.assertEqual(len(out['datasets']['binance_spot_1h']['rows']),2)

    def test_changed_normalized_value_and_original_byte_hash_are_rejected(self):
        out=m.normalize_locked(m.iso(self.cutoff),self.inputs)
        out['datasets']['binance_spot_1h']['rows'][-1]['close']=101
        with self.assertRaises(m.ObservationError):m.validate_evidence(out)
        other=deepcopy(self.inputs);other['binance_spot_1h']['raw_hash']='sha256:'+'0'*64
        with self.assertRaises(m.ObservationError):m.normalize_locked(m.iso(self.cutoff),other)

    def test_wrong_provider_product_interval_or_future_retrieval_is_rejected(self):
        for key,old,new in (('url','BTCUSDT','ETHUSDT'),('url','interval=1h','interval=1d'),('url','data-api.binance.vision','example.com'),('retrieved_at',m.iso(self.cutoff+60),'2099-01-01T00:00:00Z')):
            inp=deepcopy(self.inputs);inp['binance_spot_1h'][key]=inp['binance_spot_1h'][key].replace(old,new)
            with self.subTest(key=key,new=new),self.assertRaises(m.ObservationError):m.normalize_locked(m.iso(self.cutoff),inp)

if __name__=='__main__':unittest.main()
