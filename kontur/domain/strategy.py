"""Deterministic regime and hedge sizing rules."""
from datetime import date
from decimal import Decimal
from .risk import features, portfolio_value

def D(value):
    return Decimal(str(value))

class Regime:
    def __init__(self):
        self.value = "normal"
        self.calm = 0

    def update(self, metrics):
        vol, dd, momentum, correlation, event = (metrics[k] for k in ("volatility", "drawdown", "momentum", "correlation", "event"))
        if vol >= .30 or dd <= -.07 or (correlation >= .8 and momentum <= -.03) or (event >= .8 and vol >= .20):
            target = "storm"
        elif vol >= .20 or dd <= -.035 or event >= .7:
            target = "caution"
        else:
            target = "normal"
        ranks = {"normal": 0, "caution": 1, "storm": 2}
        if ranks[target] >= ranks[self.value]:
            self.value, self.calm = target, 0
        else:
            self.calm += 1
            if self.calm >= 3:
                self.value, self.calm = target, 0
        return self.value

def plan(data, metrics, regime, cash, current=0, end=None, last_rebalance_date=None):
    row = data["history"][-1 if end is None else end]
    spec, settings = data["contract"], data["settings"]
    notional = D(row["future"]) * D(spec["point_value"])
    exposure = max(Decimal(0), portfolio_value(data, row) * D(metrics["beta"]))
    fraction = min({"normal": 0, "caution": .25, "storm": .65}[regime], settings["max_hedge"])
    desired = int(exposure * D(fraction) // notional)
    warnings = []
    if metrics["fit"] < .35 or metrics["beta"] <= 0:
        desired = 0
        warnings.append("Слабая или отрицательная связь с индексом: открытие хеджа заблокировано")
    if spec["expiry"]:
        days_to_expiry = (date.fromisoformat(spec["expiry"]) - date.fromisoformat(row["date"])).days
        if days_to_expiry <= 0:
            desired = 0
            warnings.append("Контракт истек или истекает сегодня: требуется закрытие и смена контракта")
        elif days_to_expiry <= 5:
            desired = min(desired, current)
            warnings.append("До экспирации ≤ 5 дней: требуется смена контракта; увеличение позиции заблокировано")
    # Reserve both buffered collateral and the transaction cost, including a close.
    per_contract = notional * D(settings["cost_bps"]) / D(10000)
    cash_value = D(cash)
    budget = max(Decimal(0), cash_value - current * per_contract)
    margin_per_contract = D(spec["margin"]) * D(settings["margin_buffer"])
    affordable = max(0, int(budget // (margin_per_contract + per_contract)))
    target = min(desired, affordable, settings["max_contracts"])
    if target < desired:
        warnings.append("Размер хеджа ограничен лимитом контрактов, свободными деньгами или запасом ГО")
    if fraction and desired == 0 and exposure > 0 and metrics["fit"] >= .35:
        warnings.append("Лимита недостаточно для одного контракта или сработало ограничение инструмента")
    if last_rebalance_date and target > current and (date.fromisoformat(row["date"]) - date.fromisoformat(last_rebalance_date)).days < settings.get("min_rebalance_days", 3):
        target = current
        warnings.append("Пауза между изменениями хеджа: новое изменение пока заблокировано")
    delta = target - current
    fee = (abs(delta) * per_contract).quantize(Decimal("0.000001"))
    if cash_value < fee:
        warnings.append("Денег недостаточно даже для издержек закрытия; модель допускает отрицательный остаток")
    hedge_notional = target * notional
    return {"target": target, "current": current, "delta": delta,
            "action": "SELL" if delta > 0 else "BUY" if delta < 0 else "HOLD",
            "notional": float(notional), "hedge_notional": float(hedge_notional),
            "coverage": float(hedge_notional / exposure) if exposure else 0,
            "margin": float(target * D(spec["margin"])), "reserved": float(target * margin_per_contract),
            "cost": float(fee), "cost_decimal": str(fee), "hedge_notional_decimal": str(hedge_notional),
            "warnings": warnings}


def analyze(data, current=0, last_rebalance_date=None):
    state = Regime()
    for end in range(40, len(data["history"])):
        metrics = features(data, end)
        regime = state.update(metrics)
    result = plan(data, metrics, regime, data["free_cash"], current, last_rebalance_date=last_rebalance_date)
    reasons = [f"Волатильность за 20 дней: {metrics['volatility']:.1%}",
               f"Просадка индекса за 20 дней: {metrics['drawdown']:.1%}",
               f"Средняя корреляция акций: {metrics['correlation']:.2f}"]
    if metrics["event"]:
        reasons.append(f"Заданный риск события: {metrics['event']:.0%}")
    scenarios = []
    for shock in [-.15, -.10, -.05, .05, .10]:
        stock_pnl = portfolio_value(data, data["history"][-1]) * D(metrics["beta"]) * D(shock)
        hedge_pnl = -D(result["hedge_notional_decimal"]) * D(shock)
        scenarios.append({"shock": shock, "unhedged": float(stock_pnl), "hedged": float(stock_pnl + hedge_pnl - D(result["cost_decimal"]))})
    return {"metrics": metrics, "regime": regime, "plan": result, "reasons": reasons, "scenarios": scenarios,
            "as_of": data["history"][-1]["date"], "source": data["source"]}
