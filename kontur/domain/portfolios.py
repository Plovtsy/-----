"""Portfolio input and reproducible synthetic examples."""
import math
import random
from datetime import date, timedelta
from .core import InputError, money_number, number

def validate(raw):
    if not isinstance(raw, dict):
        raise InputError("Ожидается JSON-объект")
    history = raw.get("history")
    if not isinstance(history, list) or not 41 <= len(history) <= 3000:
        raise InputError("Нужно от 41 до 3000 дневных наблюдений")
    positions = raw.get("positions")
    if not isinstance(positions, list) or not 1 <= len(positions) <= 30:
        raise InputError("Нужно от 1 до 30 позиций")
    clean_positions = []
    seen = set()
    for p in positions:
        if not isinstance(p, dict):
            raise InputError("Некорректная позиция")
        ticker = p.get("ticker", "")
        if not isinstance(ticker, str) or not ticker.isascii() or not ticker.replace("_", "").isalnum() or len(ticker) > 20 or ticker in {"date", "index", "future", "event"} or ticker in seen:
            raise InputError("Тикеры должны быть уникальными латинскими идентификаторами")
        seen.add(ticker)
        clean_positions.append({"ticker": ticker, "quantity": number(p.get("quantity"), "Количество", 0.000001, 1e8)})
    clean = []
    previous = ""
    for row in history:
        if not isinstance(row, dict):
            raise InputError("Некорректная строка истории")
        d = row.get("date", "")
        try:
            if date.fromisoformat(d).isoformat() != d or d <= previous:
                raise ValueError()
        except (ValueError, TypeError):
            raise InputError("Даты YYYY-MM-DD должны идти строго по возрастанию без повторов") from None
        previous = d
        item = {"date": d}
        for k in ["index", "future", *seen]:
            item[k] = money_number(row.get(k), f"Цена {k} на {d}", 0.000001, 1e9, 9)
        item["event"] = number(row.get("event", 0), "Риск события", 0, 1)
        clean.append(item)
    settings = raw.get("settings", {})
    contract = raw.get("contract", {})
    if not isinstance(settings, dict) or not isinstance(contract, dict):
        raise InputError("Некорректные настройки")
    expiry = contract.get("expiry")
    if expiry is not None:
        try:
            expiry = date.fromisoformat(expiry).isoformat()
        except (TypeError, ValueError):
            raise InputError("Некорректная экспирация") from None
    max_contracts = number(settings.get("max_contracts", 100), "Максимум контрактов", 0, 100)
    if max_contracts != int(max_contracts):
        raise InputError("Максимум контрактов должен быть целым числом")
    min_rebalance_days = number(settings.get("min_rebalance_days", 3), "Пауза между изменениями, дней", 0, 30)
    if min_rebalance_days != int(min_rebalance_days):
        raise InputError("Пауза между изменениями должна быть целым числом дней")
    return {
        "history": clean, "positions": clean_positions,
        "free_cash": money_number(raw.get("free_cash", 150000), "Свободные деньги", 0, 1e10),
        "settings": {"max_hedge": number(settings.get("max_hedge", .65), "Лимит хеджа", 0, .8),
                     "max_contracts": int(max_contracts),
                     "min_rebalance_days": int(min_rebalance_days),
                     "margin_buffer": number(settings.get("margin_buffer", 1.5), "Запас ГО", 1, 5),
                     "cost_bps": number(settings.get("cost_bps", 3), "Издержки, б.п.", 0, 100)},
        "contract": {"id": "DEMO-INDEX", "point_value": money_number(contract.get("point_value", 10), "Стоимость пункта", .000001, 1e6, 9),
                     "margin": money_number(contract.get("margin", 12000), "ГО на контракт", 1, 1e9), "expiry": expiry},
        "source": "import" if raw.get("source") == "import" else "synthetic",
    }

def demo(scenario="storm"):
    if scenario not in {"storm", "calm", "recovery"}:
        raise InputError("Неизвестный сценарий")
    rng = random.Random(73)
    prices = {"index": 3000., "future": 3000., "SBER": 280., "LKOH": 6800., "GAZP": 140., "YDEX": 4000.}
    betas = {"SBER": 1.1, "LKOH": .8, "GAZP": 1.25, "YDEX": 1.35}
    rows, d = [], date(2025, 1, 9)
    while len(rows) < 200:
        if d.weekday() < 5:
            i = len(rows)
            stress = (scenario == "storm" and i >= 176) or (scenario == "recovery" and 105 <= i < 135)
            r = rng.gauss(-.007 if stress else .0006, .023 if stress else .008)
            prices["index"] *= 1 + r
            prices["future"] = prices["index"] * (1 + .001 * math.sin(i / 9))
            for ticker, beta in betas.items():
                prices[ticker] *= 1 + beta * r + rng.gauss(0, .004)
            rows.append({"date": d.isoformat(), **{k: round(v, 5) for k, v in prices.items()}, "event": .9 if stress else 0})
        d += timedelta(days=1)
    return {"history": rows, "positions": [{"ticker": t, "quantity": q} for t, q in zip(betas, [1200, 45, 1500, 60])],
            "free_cash": 180000, "settings": {"max_hedge": .65, "max_contracts": 100, "min_rebalance_days": 3, "margin_buffer": 1.5, "cost_bps": 3},
            "contract": {"point_value": 10, "margin": 12000}, "source": "synthetic"}
