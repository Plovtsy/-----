"""Close-to-close historical simulation with next-interval execution."""
from decimal import Decimal
from .core import InputError
from .risk import features, portfolio_value
from .strategy import D, Regime, plan

def max_drawdown(values):
    peak, worst = values[0], 0
    for value in values:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst


def backtest(data):
    state = Regime()
    cash, contracts, costs, turnover = D(data["free_cash"]), 0, Decimal(0), 0
    curve, trades = [], []
    last_rebalance_date = None
    rows = data["history"]
    for end in range(40, len(rows)):
        row = rows[end]
        # Position decided at the previous close earns this interval's P&L.
        if end > 40:
            cash -= contracts * (D(row["future"]) - D(rows[end - 1]["future"])) * D(data["contract"]["point_value"])
        metrics = features(data, end)
        regime = state.update(metrics)
        value = portfolio_value(data, row)
        equity = value + cash
        if equity <= 0:
            raise InputError("В бэктесте исчерпан капитал; сценарий требует отдельной модели ликвидации")
        # No new trade at the final close; terminal liquidation cost is charged.
        p = plan(data, metrics, regime, cash, contracts, end, last_rebalance_date)
        if end == len(rows) - 1:
            p["target"] = 0
            p["delta"] = -contracts
            liquidation_cost = (contracts * D(row["future"]) * D(data["contract"]["point_value"]) * D(data["settings"]["cost_bps"]) / D(10000)).quantize(Decimal("0.000001"))
            p["cost"] = float(liquidation_cost)
            p["cost_decimal"] = str(liquidation_cost)
        if p["delta"]:
            cash -= D(p["cost_decimal"])
            costs += D(p["cost_decimal"])
            turnover += abs(p["delta"])
            trades.append({"date": row["date"], "delta": p["delta"], "target": p["target"], "cost": p["cost"], "regime": regime})
            contracts = p["target"]
            last_rebalance_date = row["date"]
        curve.append({"date": row["date"], "unhedged": float(value + D(data["free_cash"])),
                      "hedged": float(value + cash), "contracts": contracts, "regime": regime,
                      "cash": float(cash), "margin_deficit": cash < contracts * D(data["contract"]["margin"])})
    base = curve[0]["unhedged"]
    return {"curve": curve, "trades": trades, "summary": {
        "unhedged_return": curve[-1]["unhedged"] / base - 1, "hedged_return": curve[-1]["hedged"] / base - 1,
        "unhedged_drawdown": max_drawdown([x["unhedged"] for x in curve]),
        "hedged_drawdown": max_drawdown([x["hedged"] for x in curve]),
        "costs": float(costs), "trade_count": len(trades), "contract_turnover": turnover,
        "difference": curve[-1]["hedged"] - curve[-1]["unhedged"]}}
