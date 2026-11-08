"""T-Invest SDK adapter pinned to the SandboxClient target."""
from __future__ import annotations

import dataclasses
import os
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from enum import Enum

from ..domain.engine import InputError


def public(value):
    if type(value) is object:
        return None
    if dataclasses.is_dataclass(value):
        return {k: public(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [public(v) for v in value]
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items()}
    return value


class SandboxBroker:
    def __init__(self):
        token = os.environ.get("TINVEST_SANDBOX_TOKEN", "")
        if not token:
            raise InputError("Задайте TINVEST_SANDBOX_TOKEN на сервере")
        self.token = token

    def client(self):
        from t_tech.invest.sandbox.client import SandboxClient
        return SandboxClient(self.token, app_name="kontur-sandbox-mvp")

    def _read(self, service: str, method: str, **kwargs):
        """Two bounded retries for transient reads only; writes are never retried."""
        import grpc
        for attempt in range(3):
            try:
                with self.client() as c:
                    return public(getattr(getattr(c, service), method)(**kwargs))
            except grpc.RpcError as error:
                if error.code() not in {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED,
                                        grpc.StatusCode.RESOURCE_EXHAUSTED} or attempt == 2:
                    raise InputError("T-Invest недоступен; проверьте сеть и лимиты API") from None
                time.sleep(.5 * 2**attempt)

    def accounts(self):
        return self._read("sandbox", "get_sandbox_accounts")

    def open_account(self, name: str):
        if not 1 <= len(name) <= 80:
            raise InputError("Название счета должно содержать от 1 до 80 символов")
        with self.client() as c:
            return public(c.sandbox.open_sandbox_account(name=name))

    def pay_in(self, account_id: str, rubles: Decimal):
        from t_tech.invest import MoneyValue
        if not Decimal("0") < rubles <= Decimal("30000000") or rubles.as_tuple().exponent < -2:
            raise InputError("Пополнение: от 0.01 до 30 000 000 ₽, не более двух знаков после запятой")
        kopecks = int(rubles * 100)
        with self.client() as c:
            return public(c.sandbox.sandbox_pay_in(account_id=account_id,
                amount=MoneyValue(currency="rub", units=kopecks // 100, nano=(kopecks % 100) * 10_000_000)))

    def portfolio(self, account_id: str):
        return self._read("sandbox", "get_sandbox_portfolio", account_id=account_id)

    def positions(self, account_id: str):
        return self._read("sandbox", "get_sandbox_positions", account_id=account_id)

    def orders(self, account_id: str):
        return self._read("sandbox", "get_sandbox_orders", account_id=account_id)

    def order_state(self, account_id: str, order_id: str):
        return self._read("sandbox", "get_sandbox_order_state", account_id=account_id, order_id=order_id)

    def cancel(self, account_id: str, order_id: str):
        with self.client() as c:
            return public(c.sandbox.cancel_sandbox_order(account_id=account_id, order_id=order_id))

    def search_futures(self, query: str):
        from t_tech.invest import InstrumentType
        if not 2 <= len(query) <= 80:
            raise InputError("Поисковый запрос: от 2 до 80 символов")
        with self.client() as c:
            raw = public(c.instruments.find_instrument(query=query, instrument_kind=InstrumentType.INSTRUMENT_TYPE_FUTURES))
        return raw

    def inspect_future(self, instrument_id: str):
        from t_tech.invest import InstrumentIdType
        with self.client() as c:
            future = public(c.instruments.future_by(id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_UID, id=instrument_id))
            margin = public(c.instruments.get_futures_margin(instrument_id=instrument_id))
            price = public(c.market_data.get_last_prices(instrument_id=[instrument_id]))
            status = public(c.market_data.get_trading_status(instrument_id=instrument_id))
        return {"future": future, "margin": margin, "price": price, "status": status}

    def candles(self, instrument_id: str, days: int = 90):
        from t_tech.invest import CandleInterval
        if not 41 <= days <= 365:
            raise InputError("История: от 41 до 365 дней")
        now = datetime.now(timezone.utc)
        with self.client() as c:
            return public(c.market_data.get_candles(instrument_id=instrument_id, from_=now-timedelta(days=days),
                to=now, interval=CandleInterval.CANDLE_INTERVAL_DAY))

    def submit_market(self, account_id: str, instrument_id: str, quantity: int, direction: str, order_id: str):
        from t_tech.invest import OrderDirection, OrderType
        if quantity <= 0 or quantity > 100:
            raise InputError("Количество контрактов вне допустимого диапазона")
        order_direction = (OrderDirection.ORDER_DIRECTION_SELL if direction == "SELL" else
                           OrderDirection.ORDER_DIRECTION_BUY if direction == "BUY" else None)
        if order_direction is None:
            raise InputError("Недопустимое направление")
        with self.client() as c:
            return public(c.sandbox.post_sandbox_order(account_id=account_id, instrument_id=instrument_id,
                quantity=quantity, direction=order_direction, order_type=OrderType.ORDER_TYPE_MARKET, order_id=order_id))
