"""Past-only portfolio and index statistics."""
import math
import statistics as stats
from decimal import Decimal


def portfolio_value(data, row):
    return sum((Decimal(str(p["quantity"])) * Decimal(str(row[p["ticker"]])) for p in data["positions"]), Decimal(0))

def returns(values):
    return [b / a - 1 for a, b in zip(values, values[1:])]


def corr(a, b):
    if len(a) < 2 or stats.pstdev(a) < 1e-12 or stats.pstdev(b) < 1e-12:
        return 0.0
    return max(-1, min(1, stats.covariance(a, b) / (stats.stdev(a) * stats.stdev(b))))

def features(data, end):
    rows = data["history"][max(0, end - 60):end + 1]
    portfolio = [sum(p["quantity"] * float(r[p["ticker"]]) for p in data["positions"]) for r in rows]
    market = [float(r["index"]) for r in rows]
    pr, mr = returns(portfolio), returns(market)
    variance = stats.variance(mr) if len(mr) > 1 else 0
    beta = stats.covariance(pr, mr) / variance if variance > 1e-12 else 0
    asset_returns = [returns([float(r[p["ticker"]]) for r in rows[-21:]]) for p in data["positions"]]
    pairs = [corr(a, b) for i, a in enumerate(asset_returns) for b in asset_returns[i + 1:]]
    return {"value": portfolio[-1], "beta": beta, "fit": corr(pr, mr),
            "volatility": stats.stdev(mr[-20:]) * math.sqrt(252),
            "drawdown": market[-1] / max(market[-21:]) - 1,
            "momentum": market[-1] / market[-21] - 1,
            "correlation": stats.mean(pairs) if pairs else 0,
            "event": rows[-1]["event"]}
