"""SQLite paper ledger: atomic fills, optimistic revision and idempotency."""
import hashlib
import json
import sqlite3
import time
import uuid

from ..domain.engine import InputError, analyze


class Ledger:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
                  contracts INTEGER NOT NULL, cash REAL, context TEXT);
              INSERT OR IGNORE INTO account VALUES (1,0,0,NULL,NULL);
              CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, created REAL, revision INTEGER,
                  context TEXT, payload TEXT, result TEXT, executed TEXT);
              CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY, created REAL, kind TEXT, detail TEXT);
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def context(data):
        snapshot = {k: v for k, v in data.items() if k not in {"settings", "source"}}
        # The original journal hashed JSON numbers. Preserve that identity for
        # migrated rows while keeping decimal text in calculations and storage.
        snapshot["free_cash"] = float(snapshot["free_cash"])
        snapshot["contract"] = {**snapshot["contract"],
                                "point_value": float(snapshot["contract"]["point_value"]),
                                "margin": float(snapshot["contract"]["margin"])}
        snapshot["history"] = [{k: (v if k == "date" else float(v)) for k, v in row.items()} for row in snapshot["history"]]
        return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()

    def state(self):
        with self.connect() as db:
            account = dict(db.execute("SELECT * FROM account WHERE id=1").fetchone())
            account.pop("context")
            account["journal"] = [dict(r) | {"detail": json.loads(r["detail"])} for r in db.execute("SELECT * FROM journal ORDER BY id DESC LIMIT 30")]
            return account

    def propose(self, data):
        context = self.context(data)
        with self.connect() as db:
            account = db.execute("SELECT * FROM account WHERE id=1").fetchone()
            compatible = account["context"] is None or account["context"] == context
            effective = dict(data)
            if compatible and account["cash"] is not None:
                effective["free_cash"] = account["cash"]
            result = analyze(effective, account["contracts"] if compatible else 0)
            proposal_id = str(uuid.uuid4())
            db.execute("DELETE FROM proposals WHERE created < ? AND executed IS NULL", (time.time() - 3600,))
            db.execute("INSERT INTO proposals VALUES (?,?,?,?,?,?,NULL)",
                       (proposal_id, time.time(), account["revision"], context, json.dumps(data), json.dumps(result)))
        return result | {"proposal_id": proposal_id, "paper_compatible": compatible}

    def execute(self, proposal_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            proposal = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            if proposal is None:
                raise InputError("Предложение не найдено: пересчитайте хедж")
            if proposal["executed"]:
                return json.loads(proposal["executed"])
            account = db.execute("SELECT * FROM account WHERE id=1").fetchone()
            if proposal["revision"] != account["revision"] or time.time() - proposal["created"] > 300:
                raise InputError("Предложение устарело: пересчитайте хедж")
            if account["context"] and proposal["context"] != account["context"]:
                raise InputError("Для другого набора данных сначала сбросьте виртуальный счет")
            result, data = json.loads(proposal["result"]), json.loads(proposal["payload"])
            p = result["plan"]
            cash = float(account["cash"] if account["cash"] is not None else data["free_cash"]) - p["cost"]
            fill = {"proposal_id": proposal_id, "delta": p["delta"], "contracts": p["target"], "cost": p["cost"], "cash": cash,
                    "revision": account["revision"] + 1, "mode": "paper"}
            db.execute("UPDATE account SET revision=?, contracts=?, cash=?, context=? WHERE id=1",
                       (fill["revision"], p["target"], cash, proposal["context"]))
            db.execute("UPDATE proposals SET executed=? WHERE id=?", (json.dumps(fill), proposal_id))
            db.execute("INSERT INTO journal(created,kind,detail) VALUES (?,?,?)", (time.time(), "fill", json.dumps(fill)))
            return fill

    def reset(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE account SET revision=revision+1,contracts=0,cash=NULL,context=NULL WHERE id=1")
            db.execute("INSERT INTO journal(created,kind,detail) VALUES (?,?,?)", (time.time(), "reset", '{}'))
