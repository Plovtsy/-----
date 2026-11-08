"""PostgreSQL persistence for demo and Sandbox state."""
from __future__ import annotations

import os
import time
import uuid
from decimal import Decimal
from sqlalchemy import Boolean, ForeignKey, Integer, JSON, Numeric, String, create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from ..domain.engine import InputError, analyze
from ..domain.context import snapshot_context


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    mode: Mapped[str] = mapped_column(String(10))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    contracts: Mapped[int] = mapped_column(Integer, default=0)
    cash: Mapped[Decimal | None] = mapped_column(Numeric(22, 6), nullable=True)
    context: Mapped[str | None] = mapped_column(String(64), nullable=True)
    halted: Mapped[bool] = mapped_column(Boolean, default=False)
    reconciled: Mapped[bool] = mapped_column(Boolean, default=False)


class Proposal(Base):
    __tablename__ = "proposals_v2"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_key: Mapped[str] = mapped_column(ForeignKey("accounts.key"))
    created: Mapped[Decimal] = mapped_column(Numeric(20, 3))
    revision: Mapped[int] = mapped_column(Integer)
    context: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    executed: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_key: Mapped[str] = mapped_column(ForeignKey("accounts.key"))
    created: Mapped[Decimal] = mapped_column(Numeric(20, 3))
    kind: Mapped[str] = mapped_column(String(30))
    detail: Mapped[dict] = mapped_column(JSON)


class Command(Base):
    __tablename__ = "commands"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_key: Mapped[str] = mapped_column(ForeignKey("accounts.key"))
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30))
    created: Mapped[Decimal] = mapped_column(Numeric(20, 3))
    payload: Mapped[dict] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    command_id: Mapped[str] = mapped_column(ForeignKey("commands.id"), unique=True)
    account_key: Mapped[str] = mapped_column(ForeignKey("accounts.key"))
    instrument_id: Mapped[str] = mapped_column(String(100))
    direction: Mapped[str] = mapped_column(String(5))
    quantity: Mapped[int] = mapped_column(Integer)
    filled: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30))
    broker_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created: Mapped[Decimal] = mapped_column(Numeric(20, 3))


class Fill(Base):
    __tablename__ = "fills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal | None] = mapped_column(Numeric(22, 9), nullable=True)
    created: Mapped[Decimal] = mapped_column(Numeric(20, 3))


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_key: Mapped[str] = mapped_column(ForeignKey("accounts.key"))
    captured: Mapped[Decimal] = mapped_column(Numeric(20, 3))
    source: Mapped[str] = mapped_column(String(20))
    data: Mapped[dict] = mapped_column(JSON)


class Checkpoint(Base):
    __tablename__ = "robot_checkpoints"
    account_key: Mapped[str] = mapped_column(ForeignKey("accounts.key"), primary_key=True)
    updated: Mapped[Decimal] = mapped_column(Numeric(20, 3))
    state: Mapped[dict] = mapped_column(JSON)


class Database:
    def __init__(self, url: str | None = None):
        self.url = url or os.environ.get("DATABASE_URL")
        if not self.url:
            raise RuntimeError("Задайте DATABASE_URL для PostgreSQL")
        if make_url(self.url).get_backend_name() != "postgresql":
            raise ValueError("Поддерживается только PostgreSQL")
        self.engine = create_engine(self.url, pool_pre_ping=True)

    def account(self, db: Session, key: str, mode: str) -> Account:
        account = db.get(Account, key)
        if account is None:
            account = Account(key=key, mode=mode, revision=0, contracts=0, halted=False, reconciled=False)
            db.add(account)
            db.flush()
        return account

    def state(self, key="demo:local", mode="demo"):
        with Session(self.engine) as db:
            a = self.account(db, key, mode)
            events = db.scalars(select(Event).where(Event.account_key == key).order_by(Event.id.desc()).limit(30)).all()
            result = {"revision": a.revision, "contracts": a.contracts, "cash": float(a.cash) if a.cash is not None else None,
                      "halted": a.halted, "reconciled": a.reconciled,
                      "journal": [{"id": e.id, "created": float(e.created), "kind": e.kind, "detail": e.detail} for e in events]}
            db.commit()
            return result

    def propose(self, data, key="demo:local"):
        with Session(self.engine) as db:
            a = self.account(db, key, "demo")
            context = snapshot_context(data)
            compatible = a.context is None or a.context == context
            effective = dict(data)
            if compatible and a.cash is not None:
                effective["free_cash"] = str(a.cash)
            last_action = db.scalars(select(Event).where(Event.account_key == key, Event.kind.in_(["fill", "reset"]))
                                     .order_by(Event.id.desc()).limit(1)).first()
            last_date = last_action.detail.get("as_of") if last_action and last_action.kind == "fill" else None
            result = analyze(effective, a.contracts if compatible else 0, last_date if compatible else None)
            proposal_id = str(uuid.uuid4())
            db.add(Proposal(id=proposal_id, account_key=key, created=Decimal(str(time.time())), revision=a.revision,
                            context=context, payload=data, result=result))
            db.add(MarketSnapshot(id=str(uuid.uuid4()), account_key=key, captured=Decimal(str(time.time())), source=data["source"], data=data))
            db.commit()
            return result | {"proposal_id": proposal_id, "paper_compatible": compatible}

    def execute(self, proposal_id, key="demo:local"):
        with Session(self.engine) as db:
            p = db.get(Proposal, proposal_id)
            if p is None or p.account_key != key:
                raise InputError("Предложение не найдено: пересчитайте хедж")
            a = db.get(Account, key, with_for_update=True)
            db.refresh(p)
            if p.executed is not None:
                return p.executed
            if a.revision != p.revision or time.time() - float(p.created) > 300:
                raise InputError("Предложение устарело: пересчитайте хедж")
            if a.context and a.context != p.context:
                raise InputError("Для другого набора данных сначала сбросьте виртуальный счет")
            plan = p.result["plan"]
            exact_cost = Decimal(str(plan.get("cost_decimal", plan["cost"])))
            cash = (a.cash if a.cash is not None else Decimal(str(p.payload["free_cash"]))) - exact_cost
            fill = {"proposal_id": proposal_id, "delta": plan["delta"], "contracts": plan["target"],
                    "cost": float(exact_cost), "cash": float(cash), "revision": a.revision + 1,
                    "as_of": p.result["as_of"], "mode": "paper"}
            a.revision += 1
            a.contracts = plan["target"]
            a.cash = cash
            a.context = p.context
            p.executed = fill
            db.add(Event(account_key=key, created=Decimal(str(time.time())), kind="fill", detail=fill))
            db.commit()
            return fill

    def reset(self, key="demo:local"):
        with Session(self.engine) as db:
            a = self.account(db, key, "demo")
            a.revision += 1
            a.contracts = 0
            a.cash = None
            a.context = None
            db.add(Event(account_key=key, created=Decimal(str(time.time())), kind="reset", detail={}))
            db.commit()

    def enqueue(self, key: str, kind: str, payload: dict, command_id: str):
        with Session(self.engine) as db:
            existing = db.get(Command, command_id)
            if existing:
                if existing.account_key != key or existing.kind != kind or existing.payload != payload:
                    raise InputError("ID команды уже занят другим запросом")
                return {"id": existing.id, "status": existing.status}
            self.account(db, key, "sandbox")
            c = Command(id=command_id, account_key=key, kind=kind, status="pending", created=Decimal(str(time.time())), payload=payload)
            db.add(c)
            db.add(Event(account_key=key, created=c.created, kind="command", detail={"id": command_id, "kind": kind}))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                existing = db.get(Command, command_id)
                if existing is None or existing.account_key != key or existing.kind != kind or existing.payload != payload:
                    raise InputError("ID команды уже занят другим запросом") from None
                return {"id": existing.id, "status": existing.status}
            return {"id": command_id, "status": "pending"}

    def sandbox_propose(self, key: str, data: dict, instrument_id: str, current: int):
        with Session(self.engine) as db:
            account = self.account(db, key, "sandbox")
            if account.contracts != current:
                raise InputError("Позиция брокера не совпадает с журналом робота")
            result = analyze(data, current)
            proposal_id = str(uuid.uuid4())
            context = snapshot_context(data)
            created = Decimal(str(time.time()))
            db.add(Proposal(id=proposal_id, account_key=key, created=created, revision=account.revision,
                            context=context, payload=data | {"sandbox_instrument_id": instrument_id}, result=result))
            db.add(MarketSnapshot(id=str(uuid.uuid4()), account_key=key, captured=created,
                                  source="sandbox_import", data=data))
            db.commit()
            return result | {"proposal_id": proposal_id, "paper_compatible": False,
                             "sandbox_instrument_id": instrument_id}

    def enqueue_proposal(self, key: str, proposal_id: str, command_id: str):
        with Session(self.engine) as db:
            used_command = db.get(Command, command_id)
            if used_command and used_command.payload.get("proposal_id") != proposal_id:
                raise InputError("ID команды уже занят другим запросом")
            if used_command and used_command.account_key == key:
                return {"id": used_command.id, "status": used_command.status}
            proposal = db.get(Proposal, proposal_id, with_for_update=True)
            if proposal is None or proposal.account_key != key or proposal.payload.get("source") != "import":
                raise InputError("Предложение Sandbox не найдено")
            if proposal.executed is not None:
                existing = proposal.executed.get("command_id")
                if existing == command_id:
                    command = db.get(Command, command_id)
                    return {"id": command_id, "status": command.status if command else "pending"}
                raise InputError("Предложение уже отправлено: сделайте новый расчет")
            account = db.get(Account, key, with_for_update=True)
            if account.halted or not account.reconciled:
                raise InputError("Робот остановлен или счет не сверен")
            if account.revision != proposal.revision or time.time() - float(proposal.created) > 300:
                raise InputError("Предложение устарело: пересчитайте хедж")
            plan = proposal.result["plan"]
            if plan["delta"] == 0 or plan["current"] != account.contracts:
                raise InputError("Позиция изменилась: пересчитайте хедж")
            payload = {"account_id": key.removeprefix("sandbox:"),
                       "instrument_id": proposal.payload["sandbox_instrument_id"],
                       "quantity": abs(plan["delta"]), "direction": plan["action"],
                       "proposal_id": proposal_id, "expected_contracts": plan["current"]}
            command = Command(id=command_id, account_key=key, kind="place_order", status="pending",
                              created=Decimal(str(time.time())), payload=payload)
            db.add(command)
            proposal.executed = {"command_id": command_id}
            db.add(Event(account_key=key, created=command.created, kind="command",
                         detail={"id": command_id, "kind": "place_order", "proposal_id": proposal_id}))
            db.commit()
            return {"id": command_id, "status": "pending"}
