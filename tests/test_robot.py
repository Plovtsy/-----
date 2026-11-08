from datetime import datetime, timedelta, timezone
from pathlib import Path
from decimal import Decimal
import tempfile
import uuid
import json

import pytest
from sqlalchemy.orm import Session

from kontur.infrastructure.database import Account, Checkpoint, Command, Database, Fill, Order
from kontur.domain.engine import InputError
from kontur.robot import Robot, preflight
from kontur.infrastructure.broker import SandboxBroker, public


def amount(n): return {'units':n,'nano':0,'currency':'rub'}

def fixture_spec():
    return {'future':{'instrument':{'uid':'future-uid','currency':'rub','lot':1,'asset_type':'index','api_trade_available_flag':True,
        'expiration_date':(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()}},
        'margin':{'initial_margin_on_sell':amount(1000),'min_price_increment':amount(1),'min_price_increment_amount':amount(10)},
        'price':{'last_prices':[{'price':amount(100),'time':datetime.now(timezone.utc).isoformat()}]},
        'status':{'api_trade_available_flag':True,'market_order_available_flag':True,'trading_status':'TRADING_STATUS_NORMAL_TRADING'}}


def payload(direction='SELL',quantity=1): return {'account_id':'test','instrument_id':'future-uid','quantity':quantity,'direction':direction}


def test_sdk_optional_fields_are_json_safe():
    from t_tech.invest.schemas import Account as BrokerAccount
    assert json.loads(json.dumps(public(BrokerAccount(id='test',name='Demo'))))['id']=='test'


def test_broker_target_is_always_sandbox(monkeypatch):
    monkeypatch.setenv('TINVEST_SANDBOX_TOKEN','dummy')
    assert SandboxBroker().client()._target == 'sandbox-invest-public-api.tbank.ru'


def test_preflight_guards():
    account=Account(key='sandbox:test',mode='sandbox',revision=0,contracts=0,halted=False,reconciled=True)
    positions={'futures':[],'money':[amount(100000)]}
    portfolio={'total_amount_portfolio':amount(100000)}
    assert preflight(fixture_spec(),positions,portfolio,account,payload()) == 0
    spec=fixture_spec();spec['price']['last_prices'][0]['time']=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
    with pytest.raises(InputError,match='старше'):preflight(spec,positions,portfolio,account,payload())
    spec=fixture_spec();spec['future']['instrument']['expiration_date']=(datetime.now(timezone.utc)+timedelta(days=3)).isoformat()
    with pytest.raises(InputError,match='Экспирация'):preflight(spec,positions,portfolio,account,payload())
    with pytest.raises(InputError,match='закрыть'):preflight(fixture_spec(),positions,portfolio,account,payload('BUY'))
    with pytest.raises(InputError,match='обеспечения'):preflight(fixture_spec(),{'futures':[],'money':[amount(0)]},portfolio,account,payload())


class FakeBroker:
    def positions(self, account_id):return {'futures':[],'money':[amount(100000)]}
    def orders(self, account_id):return {'orders':[]}
    def inspect_future(self, instrument_id):return fixture_spec()
    def portfolio(self, account_id):return {'total_amount_portfolio':amount(100000)}
    def submit_market(self,*args):raise TimeoutError('unknown broker outcome')


def test_timeout_is_uncertain_and_never_retried(monkeypatch):
    monkeypatch.setenv('TINVEST_SANDBOX_ACCOUNT_ID','test')
    with tempfile.TemporaryDirectory() as folder:
        storage=Database(f'sqlite:///{Path(folder)/"robot.sqlite3"}')
        command_id=str(uuid.uuid4());data=payload()|{'command_id':command_id}
        assert storage.enqueue('sandbox:test','place_order',data,command_id)=={'id':command_id,'status':'pending'}
        assert storage.enqueue('sandbox:test','place_order',data,command_id)=={'id':command_id,'status':'pending'}
        robot=object.__new__(Robot);robot.storage=storage;robot.broker=FakeBroker()
        with Session(storage.engine) as db:
            command=db.get(Command,command_id);account=db.get(Account,'sandbox:test')
            robot.process(db,command,account)
            assert command.status=='uncertain'
            assert not account.reconciled
            order=db.query(Order).one()
            assert order.status=='uncertain'
        with Session(storage.engine) as db:
            account=db.get(Account,'sandbox:test');robot.reconcile(db,account)
            assert not account.reconciled
            assert db.get(Checkpoint,'sandbox:test').state['reconciled'] is False


def test_reconciliation_tracks_partial_and_final_fill(monkeypatch):
    monkeypatch.setenv('TINVEST_SANDBOX_ACCOUNT_ID','test')
    with tempfile.TemporaryDirectory() as folder:
        storage=Database(f'sqlite:///{Path(folder)/"partial.sqlite3"}')
        command_id=str(uuid.uuid4());order_id=str(uuid.uuid4())
        storage.enqueue('sandbox:test','place_order',payload()|{'command_id':command_id},command_id)
        with Session(storage.engine) as db:
            account=db.get(Account,'sandbox:test')
            db.add(Order(id=order_id,command_id=command_id,account_key=account.key,
                         instrument_id='future-uid',direction='SELL',quantity=2,filled=0,
                         status='submitted',broker_order_id='broker-id',created=Decimal('1')))
            db.commit()

        class FillingBroker:
            lots=1
            def positions(self,_):return {'futures':[{'instrument_uid':'future-uid','balance':-self.lots}]}
            def orders(self,_):return {'orders':[{'order_request_id':order_id,'lots_executed':self.lots,
                'execution_report_status':'EXECUTION_REPORT_STATUS_PARTIALLYFILL' if self.lots==1 else 'EXECUTION_REPORT_STATUS_FILL'}]}
        broker=FillingBroker();robot=object.__new__(Robot);robot.storage=storage;robot.broker=broker
        for lots,expected in [(1,'partial'),(2,'filled')]:
            broker.lots=lots
            with Session(storage.engine) as db:
                account=db.get(Account,'sandbox:test')
                robot.reconcile(db,account)
                db.commit()
            with Session(storage.engine) as db:
                assert db.get(Command,command_id).status==expected
                assert db.get(Order,order_id).filled==lots
                assert db.get(Account,'sandbox:test').contracts==lots
                assert db.get(Checkpoint,'sandbox:test').state['reconciled'] is (lots == 2)
                assert db.query(Fill).count()==lots
