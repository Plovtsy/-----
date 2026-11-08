"""Separate, fail-closed Sandbox command worker. Never targets a live endpoint."""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .infrastructure.broker import SandboxBroker
from .infrastructure.database import Account, Checkpoint, Command, Database, Event, Fill, Order, Proposal
from .domain.engine import InputError


def money(value):
    if not isinstance(value, dict):
        raise InputError("Нет денежной величины от брокера")
    return Decimal(str(value.get("units", 0))) + Decimal(str(value.get("nano", 0))) / Decimal("1000000000")


def preflight(spec, positions, portfolio, account: Account, command: dict):
    """Only a fresh, RUB, tradeable future; close buys cannot exceed the existing short."""
    future = spec.get("future", {}).get("instrument", {})
    margin = spec.get("margin", {})
    prices = spec.get("price", {}).get("last_prices", [])
    status = spec.get("status", {})
    if not future or future.get("uid") != command["instrument_id"]:
        raise InputError("Спецификация фьючерса не подтверждена")
    if future.get("currency", "").lower() != "rub" or future.get("lot") != 1:
        raise InputError("Поддерживается только рублевый фьючерс с лотом 1")
    if future.get("asset_type", "").lower() != "index":
        raise InputError("Поддерживается только фьючерс на индекс")
    if not future.get("api_trade_available_flag") or not status.get("api_trade_available_flag") or not status.get("market_order_available_flag"):
        raise InputError("Торговля инструментом через API сейчас недоступна")
    if "NORMAL_TRADING" not in str(status.get("trading_status", "")):
        raise InputError("Инструмент вне нормальных торгов")
    if not prices:
        raise InputError("Нет свежей цены фьючерса")
    captured = datetime.fromisoformat(prices[0]["time"].replace("Z", "+00:00"))
    if (datetime.now(timezone.utc) - captured).total_seconds() > 300:
        raise InputError("Котировка старше 5 минут")
    if money(prices[0].get("price")) <= 0 or money(margin.get("initial_margin_on_sell")) <= 0:
        raise InputError("Цена или ГО фьючерса недоступны")
    if money(margin.get("min_price_increment")) <= 0 or money(margin.get("min_price_increment_amount")) <= 0:
        raise InputError("Шаг цены или стоимость шага недоступны")
    futures = positions.get("futures", [])
    if any(f.get("instrument_uid") != command["instrument_id"] and int(f.get("balance", 0)) != 0 for f in futures):
        raise InputError("На счете есть другой фьючерс: требуется ручная сверка")
    balance = sum(int(f.get("balance", 0)) for f in futures if f.get("instrument_uid") == command["instrument_id"])
    if balance > 0 or -balance != account.contracts:
        raise InputError("Позиция брокера не совпадает с локальной: новые заявки остановлены")
    qty = command["quantity"]
    if command["direction"] == "BUY":
        if qty > account.contracts:
            raise InputError("Покупка может только закрыть существующий короткий хедж")
    else:
        expires = datetime.fromisoformat(future["expiration_date"].replace("Z", "+00:00"))
        if (expires - datetime.now(timezone.utc)).days <= 5:
            raise InputError("Экспирация близко: увеличение хеджа заблокировано")
        if account.contracts + qty > 100:
            raise InputError("Лимит 100 контрактов")
        cash = sum(money(v) for v in positions.get("money", []) if v.get("currency", "").lower() == "rub")
        needed = Decimal(account.contracts + qty) * money(margin["initial_margin_on_sell"]) * Decimal("1.5")
        if needed > cash:
            raise InputError("Недостаточно рублевого обеспечения с запасом 1.5")
        total = money(portfolio.get("total_amount_portfolio"))
        notional = money(prices[0]["price"]) * (money(margin["min_price_increment_amount"]) / money(margin["min_price_increment"]))
        if notional * (account.contracts + qty) > total * Decimal("0.8"):
            raise InputError("Лимит 80% стоимости портфеля")
    return balance


class Robot:
    def __init__(self, storage: Database | None = None, broker=None):
        self.storage = storage or Database()
        self.broker = broker or SandboxBroker()
        if not self.storage.engine.dialect.name == "postgresql":
            raise RuntimeError("Sandbox worker requires PostgreSQL advisory locks")

    def reconcile(self, db: Session, account: Account):
        account_id = account.key.removeprefix("sandbox:")
        positions = self.broker.positions(account_id)
        active = self.broker.orders(account_id)
        known = db.scalars(select(Order).where(Order.account_key == account.key)).all()
        broker_orders = {o.get("order_request_id"): o for o in active.get("orders", [])}
        for order in known:
            if order.status in {"filled", "cancelled", "rejected"}:
                continue
            broker_order = broker_orders.get(order.id)
            if broker_order is None and order.broker_order_id:
                try:
                    broker_order = self.broker.order_state(account_id, order.broker_order_id)
                except Exception:
                    order.status = "uncertain"
                    db.get(Command, order.command_id).status = "uncertain"
                    account.reconciled = False
                    continue
            if broker_order is None:
                order.status = "uncertain"
                db.get(Command, order.command_id).status = "uncertain"
                account.reconciled = False
                continue
            self.update_order(db, order, broker_order)
        unresolved = any(o.status in {"uncertain", "submitting", "submitted", "partial"} for o in known)
        if unresolved:
            account.reconciled = False
            self.checkpoint(db, account)
            return positions
        instrument_ids = {o.instrument_id for o in known}
        futures = [f for f in positions.get("futures", []) if int(f.get("balance", 0)) != 0]
        if len(instrument_ids) > 1 or any(f.get("instrument_uid") not in instrument_ids for f in futures):
            account.reconciled = False
            self.checkpoint(db, account)
            return positions
        balance = sum(int(f.get("balance", 0)) for f in futures)
        account.reconciled = balance == -account.contracts
        self.checkpoint(db, account)
        return positions

    def checkpoint(self, db: Session, account: Account):
        db.merge(Checkpoint(account_key=account.key, updated=Decimal(str(time.time())),
                            state={"reconciled": account.reconciled, "contracts": account.contracts,
                                   "revision": account.revision}))

    def update_order(self, db: Session, order: Order, state: dict):
        status = str(state.get("execution_report_status", ""))
        filled = int(state.get("lots_executed", 0))
        if filled > order.filled:
            db.add(Fill(order_id=order.id, quantity=filled-order.filled,
                        price=money(state["executed_order_price"]) if state.get("executed_order_price") else None,
                        created=Decimal(str(time.time()))))
            account = db.get(Account, order.account_key, with_for_update=True)
            account.contracts += filled-order.filled if order.direction == "SELL" else -(filled-order.filled)
            account.revision += 1
            order.filled = filled
        order.status = ("filled" if "FILL" in status and "PARTIALLY" not in status else
                        "partial" if "PARTIALLY" in status else
                        "rejected" if "REJECTED" in status else
                        "cancelled" if "CANCELLED" in status else "submitted")
        db.get(Command, order.command_id).status = order.status
        db.add(Event(account_key=order.account_key, created=Decimal(str(time.time())), kind="order_state",
                     detail={"id": order.id, "status": order.status, "filled": filled}))

    def process(self, db: Session, command: Command, account: Account):
        if account.key != "sandbox:" + os.environ.get("TINVEST_SANDBOX_ACCOUNT_ID", ""):
            raise InputError("Счет не совпадает с выделенным тестовым счетом")
        if account.halted and command.kind == "place_order":
            raise InputError("Аварийная остановка включена")
        if command.kind not in {"place_order", "close_hedge", "cancel_order"}:
            raise InputError("Неизвестная команда")
        account_id = account.key.removeprefix("sandbox:")
        payload = command.payload
        if command.kind == "cancel_order":
            previous = db.get(Order, payload["order_id"])
            if previous is None or previous.account_key != account.key or not previous.broker_order_id:
                raise InputError("Заявка для отмены не найдена")
            self.broker.cancel(account_id, previous.broker_order_id)
            self.update_order(db, previous, self.broker.order_state(account_id, previous.broker_order_id))
            command.status = previous.status
            db.commit()
            return
        positions = self.reconcile(db, account)
        db.commit()
        if not account.reconciled:
            raise InputError("После сверки есть неопределенные заявки")
        if command.kind == "close_hedge":
            balance = sum(int(f.get("balance", 0)) for f in positions.get("futures", [])
                          if f.get("instrument_uid") == payload["instrument_id"])
            if balance == 0 and account.contracts == 0:
                command.status = "filled"
                db.commit()
                return
            payload = {**payload, "direction": "BUY", "quantity": -balance}
        spec = self.broker.inspect_future(payload["instrument_id"])
        portfolio = self.broker.portfolio(account_id)
        preflight(spec, positions, portfolio, account, payload)
        if "proposal_id" in payload:
            proposal = db.get(Proposal, payload["proposal_id"])
            if (proposal is None or proposal.account_key != account.key or
                    proposal.payload.get("sandbox_instrument_id") != payload["instrument_id"] or
                    proposal.result["plan"]["delta"] != (payload["quantity"] if payload["direction"] == "SELL" else -payload["quantity"]) or
                    time.time() - float(proposal.created) > 300 or
                    payload.get("expected_contracts") != account.contracts):
                raise InputError("Предложение изменилось или устарело: пересчитайте хедж")
        if payload["direction"] == "SELL":
            previous = db.scalars(select(Order).where(Order.account_key == account.key, Order.direction == "SELL",
                                                      Order.status.not_in(["rejected", "cancelled"]))
                                  .order_by(Order.created.desc()).limit(1)).first()
            if previous and time.time() - float(previous.created) < 3600:
                raise InputError("Частота изменения защиты ограничена: подождите 1 час")
        # Stable UUID is persisted before network I/O. A timeout is never retried blindly.
        order = Order(id=str(uuid.uuid4()), command_id=command.id, account_key=account.key,
                      instrument_id=payload["instrument_id"], direction=payload["direction"],
                      quantity=payload["quantity"], filled=0, status="submitting", created=Decimal(str(time.time())))
        db.add(order)
        command.status = "submitting"
        db.commit()
        try:
            response = self.broker.submit_market(account_id, order.instrument_id, order.quantity, order.direction, order.id)
        except Exception:
            order.status = "uncertain"
            command.status = "uncertain"
            account.reconciled = False
            self.checkpoint(db, account)
            db.commit()
            return
        order.broker_order_id = response.get("order_id")
        self.update_order(db, order, response)
        command.status = order.status
        account.reconciled = False
        self.checkpoint(db, account)
        db.commit()

    def once(self):
        with Session(self.storage.engine) as db:
            command = db.scalars(select(Command).where(Command.status == "pending").order_by(Command.created).limit(1)).first()
            if command is None:
                return False
            command_id, account_key = command.id, command.account_key
        # Hold one dedicated PostgreSQL connection until broker I/O completes. A
        # Session commit would otherwise return its connection and orphan the lock.
        with self.storage.engine.connect() as lock_conn:
            got_lock = lock_conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": account_key}).scalar_one()
            if not got_lock:
                return False
            try:
                with Session(self.storage.engine) as db:
                    command = db.get(Command, command_id, with_for_update=True)
                    if command.status != "pending":
                        return False
                    account = db.get(Account, account_key, with_for_update=True)
                    try:
                        self.process(db, command, account)
                    except InputError as error:
                        command.status = "rejected"
                        command.error = str(error)
                        db.commit()
                    except Exception:
                        command.status = "uncertain"
                        account.reconciled = False
                        self.checkpoint(db, account)
                        db.commit()
                    return True
            finally:
                lock_conn.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": account_key})
                lock_conn.commit()

    def reconcile_all(self):
        with Session(self.storage.engine) as db:
            keys = db.scalars(select(Account.key).where(Account.mode == "sandbox")).all()
        for key in keys:
            with self.storage.engine.connect() as lock_conn:
                got_lock = lock_conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": key}).scalar_one()
                if not got_lock:
                    continue
                try:
                    with Session(self.storage.engine) as db:
                        account = db.get(Account, key, with_for_update=True)
                        try:
                            self.reconcile(db, account)
                        except Exception:
                            account.reconciled = False
                            self.checkpoint(db, account)
                        db.commit()
                finally:
                    lock_conn.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": key})
                    lock_conn.commit()

    async def run(self):
        await asyncio.to_thread(self.reconcile_all)
        last_reconcile = time.monotonic()
        while True:
            await asyncio.to_thread(self.once)
            if time.monotonic() - last_reconcile > 30:
                await asyncio.to_thread(self.reconcile_all)
                last_reconcile = time.monotonic()
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(Robot().run())
