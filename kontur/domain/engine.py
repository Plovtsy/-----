"""Compatibility facade for the split risk and strategy modules."""
from .core import InputError, number
from .portfolios import validate, demo
from .risk import returns, corr, features
from .strategy import Regime, plan, analyze
from .backtesting import max_drawdown, backtest

__all__ = ["InputError", "number", "validate", "demo", "returns", "corr", "features", "Regime", "plan", "analyze", "max_drawdown", "backtest"]
