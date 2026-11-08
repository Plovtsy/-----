import copy
import math
from decimal import Decimal
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from kontur.domain.engine import InputError, Regime, analyze, backtest, demo, features, plan, validate
from kontur.infrastructure.database import Database
from tests.postgres import temporary_database


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.data = validate(demo())

    def test_known_beta(self):
        # A portfolio worth exactly twice an index has identical percentage returns.
        for row in self.data['history']:
            row['SBER'] = str(Decimal(row['index']) * 2)
        self.data['positions'] = [{'ticker':'SBER','quantity':1}]
        m = features(self.data,199)
        self.assertAlmostEqual(m['beta'],1)
        self.assertAlmostEqual(m['fit'],1)

    def test_scenarios(self):
        self.assertEqual(analyze(self.data)['regime'],'storm')
        self.assertEqual(analyze(validate(demo('calm')))['regime'],'normal')
        self.assertEqual(analyze(validate(demo('recovery')))['regime'],'normal')

    def test_risk_and_collateral_caps(self):
        result=analyze(self.data)
        self.assertGreater(result['plan']['target'],0)
        for cash in (0,10,12000,40000,180000,1e7):
            for cap in (0,.1,.4,.8):
                d=copy.deepcopy(self.data);d['free_cash']=cash;d['settings']['max_hedge']=cap
                a=analyze(d);p=a['plan']
                self.assertLessEqual(p['coverage'],cap+1e-12)
                self.assertLessEqual(p['reserved']+p['cost'],cash+1e-9)
                self.assertIsInstance(p['target'],int)

    def test_contract_count_cap(self):
        self.data['settings']['max_contracts']=2
        self.assertLessEqual(analyze(self.data)['plan']['target'],2)

    def test_rebalance_pause_only_blocks_new_openings(self):
        last=self.data['history'][-1]['date']
        blocked=analyze(self.data, last_rebalance_date=last)['plan']
        self.assertEqual(blocked['target'],0)
        self.assertIn('Пауза', ' '.join(blocked['warnings']))
        self.assertGreater(analyze(self.data)['plan']['target'],0)
        self.data['settings']['max_hedge']=0
        closing=analyze(self.data, current=5, last_rebalance_date=last)
        self.assertEqual(closing['plan']['target'],0)

    def test_money_inputs_keep_decimal_digits(self):
        raw=demo()
        raw['free_cash']='123456.012345'
        raw['contract']['point_value']='0.123456789'
        data=validate(raw)
        self.assertEqual(data['free_cash'],'123456.012345')
        self.assertEqual(data['contract']['point_value'],'0.123456789')

    def test_weak_correlation_and_negative_beta_block(self):
        m=features(self.data,199)
        for overrides in ({'fit':.1},{'beta':-1}):
            p=plan(self.data,m|overrides,'storm',1e8)
            self.assertEqual(p['target'],0)
            self.assertTrue(p['warnings'])

    def test_expiry_requires_exit(self):
        self.data['contract']['expiry']=self.data['history'][-1]['date']
        p=analyze(self.data,5)['plan']
        self.assertEqual(p['target'],0)
        self.assertEqual(p['delta'],-5)

    def test_hysteresis(self):
        machine=Regime()
        storm={'volatility':.4,'drawdown':0,'momentum':0,'correlation':0,'event':0}
        calm=storm|{'volatility':.1}
        self.assertEqual(machine.update(storm),'storm')
        self.assertEqual(machine.update(calm),'storm')
        self.assertEqual(machine.update(calm),'storm')
        self.assertEqual(machine.update(calm),'normal')

    def test_future_data_cannot_change_previous_decisions(self):
        original=backtest(self.data)
        changed=copy.deepcopy(self.data)
        for row in changed['history'][160:]:
            for k in ['index','future','SBER','GAZP','LKOH','YDEX']:
                row[k]=str(Decimal(row[k])*Decimal('.95'))
        after=backtest(changed)
        self.assertEqual(original['curve'][:120],after['curve'][:120])

    def test_one_day_future_pnl_uses_previous_position(self):
        bt=backtest(self.data)
        cash=float(self.data['free_cash']);previous=0
        fees={t['date']:t['cost'] for t in bt['trades']}
        for end,point in enumerate(bt['curve'],40):
            if end>40:
                cash-=previous*(float(self.data['history'][end]['future'])-float(self.data['history'][end-1]['future']))*10
            cash-=fees.get(point['date'],0)
            self.assertAlmostEqual(cash,point['cash'])
            previous=point['contracts']
        self.assertEqual(previous,0)

    def test_zero_hedge_has_identical_equity(self):
        self.data['settings']['max_hedge']=0
        bt=backtest(self.data)
        self.assertFalse(bt['trades'])
        for row in bt['curve']:
            self.assertEqual(row['hedged'],row['unhedged'])

    def test_validation_rejects_bad_financial_inputs(self):
        for value in [float('nan'),float('inf'),-1,True,'oops']:
            raw=demo();raw['free_cash']=value
            with self.assertRaises(InputError):validate(raw)
        raw=demo();raw['history'][1]['date']=raw['history'][0]['date']
        with self.assertRaises(InputError):validate(raw)
        raw=demo();del raw['history'][10]['SBER']
        with self.assertRaises(InputError):validate(raw)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.database_context=temporary_database()
        self.database=self.database_context.__enter__()
        self.data=validate(demo())

    def tearDown(self):self.database_context.__exit__(None,None,None)

    def test_atomic_duplicate_execution(self):
        p=self.database.propose(self.data)
        with ThreadPoolExecutor(max_workers=4) as executor:
            results=list(executor.map(lambda _:self.database.execute(p['proposal_id']),range(4)))
        self.assertTrue(all(r==results[0] for r in results))
        self.assertEqual(self.database.state()['revision'],1)
        self.assertEqual(len(self.database.state()['journal']),1)

    def test_stale_revision_rejected(self):
        first=self.database.propose(self.data);second=self.database.propose(self.data)
        self.database.execute(first['proposal_id'])
        with self.assertRaises(InputError):self.database.execute(second['proposal_id'])

    def test_expired_proposal_rejected(self):
        with patch('kontur.infrastructure.database.time.time',return_value=1000):p=self.database.propose(self.data)
        with patch('kontur.infrastructure.database.time.time',return_value=1400):
            with self.assertRaises(InputError):self.database.execute(p['proposal_id'])

    def test_new_snapshot_needs_reset(self):
        self.database.execute(self.database.propose(self.data)['proposal_id'])
        other=validate(demo('calm'));proposal=self.database.propose(other)
        self.assertFalse(proposal['paper_compatible'])
        with self.assertRaises(InputError):self.database.execute(proposal['proposal_id'])
        self.database.reset()
        self.database.execute(self.database.propose(other)['proposal_id'])
        self.assertEqual(self.database.state()['contracts'],0)

    def test_commission_is_deducted_and_state_survives_restart(self):
        proposal=self.database.propose(self.data);self.database.execute(proposal['proposal_id'])
        again=Database(self.database.url)
        self.assertAlmostEqual(again.state()['cash'],float(self.data['free_cash'])-proposal['plan']['cost'])

if __name__=='__main__':unittest.main()
