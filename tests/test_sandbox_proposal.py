"""Sandbox proposal checks use a fake broker and never contact T-Invest."""
import copy
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from kontur import api as server
from kontur.infrastructure.database import Account, Database, Proposal
from kontur.domain.engine import demo


def amount(value, currency="rub"):
    value = Decimal(str(value))
    units = int(value)
    return {"units": units, "nano": int((value - units) * 1_000_000_000), "currency": currency}


class Broker:
    def __init__(self, data):
        self.data = data

    def positions(self, _account_id):
        return {"futures": [], "money": [amount(180000)]}

    def portfolio(self, _account_id):
        last = self.data["history"][-1]
        return {"positions": [{"ticker": p["ticker"], "instrument_type": "share",
                               "quantity": amount(p["quantity"]),
                               "current_price": amount(last[p["ticker"]])} for p in self.data["positions"]]}

    def inspect_future(self, instrument_id):
        return {"future": {"instrument": {"uid": instrument_id, "currency": "rub", "lot": 1, "asset_type": "index",
                "expiration_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()}},
                "price": {"last_prices": [{"price": amount(self.data["history"][-1]["future"]),
                                            "time": datetime.now(timezone.utc).isoformat()}]},
                "margin": {"initial_margin_on_sell": amount(12000), "min_price_increment": amount(1),
                           "min_price_increment_amount": amount(10)}}


def test_proposal_is_bound_to_broker_account_and_single_command(monkeypatch):
    data = demo()
    first = date.today() - timedelta(days=len(data["history"]) - 1)
    for i, row in enumerate(data["history"]):
        row["date"] = (first + timedelta(days=i)).isoformat()
    data["source"] = "import"
    monkeypatch.setenv("TINVEST_SANDBOX_TOKEN", "test-only")
    monkeypatch.setenv("TINVEST_SANDBOX_ACCOUNT_ID", "dedicated")
    monkeypatch.setattr(server, "SandboxBroker", lambda: Broker(data))
    with tempfile.TemporaryDirectory() as folder:
        server._db = Database(f"sqlite:///{Path(folder) / 'sandbox.sqlite3'}")
        try:
            with TestClient(server.app) as client:
                request = {"account_id": "dedicated", "instrument_id": "future-uid", "data": data}
                result = client.post("/api/sandbox/preview", json=request)
                assert result.status_code == 200, result.text
                proposal = result.json()
                assert proposal["plan"]["delta"] > 0
                with Session(server._db.engine) as db:
                    account = db.get(Account, "sandbox:dedicated")
                    account.reconciled = True
                    db.commit()
                command_id = str(uuid.uuid4())
                apply = {"account_id": "dedicated", "proposal_id": proposal["proposal_id"], "command_id": command_id}
                assert client.post("/api/sandbox/apply-proposal", json=apply).json() == {"id": command_id, "status": "pending"}
                assert client.post("/api/sandbox/apply-proposal", json=apply).json()["id"] == command_id
                assert client.post("/api/sandbox/apply-proposal", json=apply | {"command_id": str(uuid.uuid4())}).status_code == 400
                with Session(server._db.engine) as db:
                    stored = db.get(Proposal, proposal["proposal_id"])
                    assert stored.payload["sandbox_instrument_id"] == "future-uid"
                bad = copy.deepcopy(data)
                bad["source"] = "synthetic"
                assert client.post("/api/sandbox/preview", json=request | {"data": bad}).status_code == 400
        finally:
            server._db = None
