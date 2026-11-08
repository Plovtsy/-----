"""Local FastAPI server for Контур. No live trading endpoint or client-supplied target."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import date, datetime, timezone
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from kontur.infrastructure.broker import SandboxBroker
from kontur.infrastructure.database import Database
from kontur.domain.engine import InputError, backtest, demo, validate
from kontur.robot import money

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title="Контур", docs_url=None, redoc_url=None)
_db = None


def db():
    global _db
    if _db is None:
        _db = Database()
    return _db


class LocalOnly(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0]
        origin = request.headers.get("origin")
        if host not in {"localhost", "127.0.0.1", "testserver"}:
            return JSONResponse({"error": "Доступ разрешен только через localhost"}, status_code=403)
        if origin and origin not in {f"http://{request.headers.get('host')}", f"https://{request.headers.get('host')}", "http://localhost:5173"}:
            return JSONResponse({"error": "Запрос с другого сайта запрещен"}, status_code=403)
        content_length = request.headers.get("content-length", "0")
        if not content_length.isdigit():
            return JSONResponse({"error": "Некорректная длина запроса"}, status_code=400)
        if request.method in {"POST", "PUT", "PATCH"} and int(content_length) > 3_000_000:
            return JSONResponse({"error": "Размер запроса превышает 3 МБ"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response


app.add_middleware(LocalOnly)


@app.exception_handler(InputError)
async def input_error(_request, error: InputError):
    from fastapi.responses import JSONResponse
    return JSONResponse({"error": str(error)}, status_code=400)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/ready")
def ready():
    try:
        db().state()
        return {"status": "ready"}
    except Exception:
        raise HTTPException(503, "Хранилище недоступно") from None


@app.get("/api/status")
def status():
    return {"mode": "demo", "sandbox_configured": bool(os.environ.get("TINVEST_SANDBOX_TOKEN")), "live_trading": False}


@app.get("/api/demo")
def demo_data(scenario: str = "storm"):
    return demo(scenario)


@app.post("/api/analyze")
def analyze_data(payload: dict):
    return db().propose(validate(payload))


@app.post("/api/backtest")
def backtest_data(payload: dict):
    return backtest(validate(payload))


@app.get("/api/paper")
def paper():
    return db().state()


class ProposalId(BaseModel):
    proposal_id: str


@app.post("/api/paper/execute")
def paper_execute(payload: ProposalId):
    return db().execute(payload.proposal_id)


@app.post("/api/paper/reset")
def paper_reset():
    db().reset()
    return db().state()


@app.get("/api/events")
async def events(request: Request):
    async def stream():
        revision = None
        while not await request.is_disconnected():
            state = await asyncio.to_thread(db().state)
            if state["revision"] != revision:
                revision = state["revision"]
                yield f"event: state\ndata: {json.dumps(state, ensure_ascii=False)}\n\n"
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(3)
    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/sandbox/accounts")
def sandbox_accounts():
    return SandboxBroker().accounts()


class AccountName(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@app.post("/api/sandbox/accounts")
def sandbox_create(payload: AccountName):
    return SandboxBroker().open_account(payload.name)


class AccountId(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)


@app.post("/api/sandbox/portfolio")
def sandbox_portfolio(payload: AccountId):
    return SandboxBroker().portfolio(payload.account_id)


@app.post("/api/sandbox/positions")
def sandbox_positions(payload: AccountId):
    return SandboxBroker().positions(payload.account_id)


@app.post("/api/sandbox/orders")
def sandbox_orders(payload: AccountId):
    return SandboxBroker().orders(payload.account_id)


class PayIn(AccountId):
    amount_rub: str


@app.post("/api/sandbox/pay-in")
def sandbox_pay_in(payload: PayIn):
    try:
        amount = Decimal(payload.amount_rub)
    except InvalidOperation:
        raise InputError("Некорректная сумма пополнения") from None
    return SandboxBroker().pay_in(payload.account_id, amount)


@app.get("/api/sandbox/futures")
def sandbox_futures(query: str):
    return SandboxBroker().search_futures(query)


@app.get("/api/sandbox/future/{instrument_id}")
def sandbox_future(instrument_id: str):
    return SandboxBroker().inspect_future(instrument_id)


@app.get("/api/sandbox/candles/{instrument_id}")
def sandbox_candles(instrument_id: str, days: int = 90):
    return SandboxBroker().candles(instrument_id, days)


class SandboxPreview(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)
    instrument_id: str = Field(min_length=1, max_length=100)
    data: dict


@app.post("/api/sandbox/preview")
def sandbox_preview(payload: SandboxPreview):
    if not os.environ.get("TINVEST_SANDBOX_TOKEN"):
        raise InputError("Токен песочницы не задан")
    if payload.account_id != os.environ.get("TINVEST_SANDBOX_ACCOUNT_ID"):
        raise InputError("Выберите выделенный тестовый счет TINVEST_SANDBOX_ACCOUNT_ID")
    data = validate(payload.data)
    if data["source"] != "import":
        raise InputError("Для Sandbox нужна собственная проверенная история CSV; синтетический пример не подходит")
    last = data["history"][-1]
    if (date.today() - date.fromisoformat(last["date"])).days not in range(0, 6):
        raise InputError("Последняя дата CSV старше 5 дней или находится в будущем")
    broker = SandboxBroker()
    positions = broker.positions(payload.account_id)
    portfolio = broker.portfolio(payload.account_id)
    spec = broker.inspect_future(payload.instrument_id)
    future = spec.get("future", {}).get("instrument", {})
    quote = (spec.get("price", {}).get("last_prices") or [{}])[0]
    margin = spec.get("margin", {})
    if future.get("uid") != payload.instrument_id or future.get("currency", "").lower() != "rub" or future.get("lot") != 1:
        raise InputError("Нужен найденный через API рублевый фьючерс с лотом 1")
    if future.get("asset_type", "").lower() != "index":
        raise InputError("Выбранный фьючерс не привязан к индексу")
    if not quote.get("time") or not 0 <= (datetime.now(timezone.utc) - datetime.fromisoformat(quote["time"].replace("Z", "+00:00"))).total_seconds() <= 300:
        raise InputError("Нет свежей котировки фьючерса")
    price = money(quote.get("price"))
    tick = money(margin.get("min_price_increment"))
    tick_value = money(margin.get("min_price_increment_amount"))
    collateral = money(margin.get("initial_margin_on_sell"))
    if min(price, tick, tick_value, collateral) <= 0:
        raise InputError("У брокера нет полной спецификации цены и ГО")
    if abs(Decimal(str(last["future"])) - price) / price > Decimal("0.05"):
        raise InputError("Цена фьючерса в CSV отличается от свежей котировки более чем на 5%")
    shares = {}
    for p in portfolio.get("positions", []):
        quantity = money(p.get("quantity"))
        if quantity == 0 or p.get("instrument_type", "").lower() == "futures":
            continue
        if p.get("instrument_type", "").lower() != "share" or not p.get("ticker"):
            raise InputError("На счете есть актив, который расчет портфеля не поддерживает")
        if quantity < 0 or p["ticker"] in shares:
            raise InputError("Позиции акций брокера требуют ручной сверки")
        current_price = p.get("current_price")
        if not current_price or current_price.get("currency", "").lower() != "rub":
            raise InputError("У акции нет текущей рублевой цены")
        shares[p["ticker"]] = (quantity, money(current_price))
    expected = {p["ticker"]: Decimal(str(p["quantity"])) for p in data["positions"]}
    if not shares or {k: v[0] for k, v in shares.items()} != expected:
        raise InputError("Тикеры или количества в CSV не совпадают с акциями тестового счета")
    for ticker, (_, current_price) in shares.items():
        if current_price <= 0 or abs(Decimal(str(last[ticker])) - current_price) / current_price > Decimal("0.05"):
            raise InputError(f"Цена {ticker} в CSV отличается от текущей цены более чем на 5%")
    futures = positions.get("futures", [])
    if any(f.get("instrument_uid") != payload.instrument_id and int(f.get("balance", 0)) for f in futures):
        raise InputError("На тестовом счете есть другой фьючерс")
    balance = sum(int(f.get("balance", 0)) for f in futures if f.get("instrument_uid") == payload.instrument_id)
    if balance > 0:
        raise InputError("На счете есть длинная позиция по фьючерсу")
    expiry = future.get("expiration_date")
    if not expiry:
        raise InputError("Неизвестна дата экспирации контракта")
    cash = sum(money(v) for v in positions.get("money", []) if v.get("currency", "").lower() == "rub")
    data["free_cash"] = str(cash)
    data["contract"] = {"id": payload.instrument_id, "point_value": str(tick_value / tick),
                        "margin": str(collateral), "expiry": expiry[:10]}
    return db().sandbox_propose("sandbox:" + payload.account_id, data, payload.instrument_id, -balance)


class SandboxApply(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)
    proposal_id: uuid.UUID
    command_id: uuid.UUID


@app.post("/api/sandbox/apply-proposal")
def sandbox_apply_proposal(payload: SandboxApply):
    if not os.environ.get("TINVEST_SANDBOX_TOKEN"):
        raise InputError("Токен песочницы не задан")
    if payload.account_id != os.environ.get("TINVEST_SANDBOX_ACCOUNT_ID"):
        raise InputError("Выберите выделенный тестовый счет TINVEST_SANDBOX_ACCOUNT_ID")
    return db().enqueue_proposal("sandbox:" + payload.account_id, str(payload.proposal_id), str(payload.command_id))


class OrderCommand(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)
    instrument_id: str = Field(min_length=1, max_length=100)
    quantity: int = Field(ge=1, le=100)
    direction: str
    command_id: uuid.UUID


@app.post("/api/sandbox/command")
def sandbox_command(payload: OrderCommand):
    if payload.direction not in {"BUY", "SELL"}:
        raise InputError("Направление должно быть BUY или SELL")
    if not os.environ.get("TINVEST_SANDBOX_TOKEN"):
        raise InputError("Токен песочницы не задан")
    if payload.account_id != os.environ.get("TINVEST_SANDBOX_ACCOUNT_ID"):
        raise InputError("Команды разрешены только для выделенного тестового счета TINVEST_SANDBOX_ACCOUNT_ID")
    return db().enqueue("sandbox:" + payload.account_id, "place_order", payload.model_dump(mode="json"), str(payload.command_id))


class CloseCommand(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)
    instrument_id: str = Field(min_length=1, max_length=100)
    command_id: uuid.UUID


@app.post("/api/sandbox/close")
def sandbox_close(payload: CloseCommand):
    if not os.environ.get("TINVEST_SANDBOX_TOKEN"):
        raise InputError("Токен песочницы не задан")
    if payload.account_id != os.environ.get("TINVEST_SANDBOX_ACCOUNT_ID"):
        raise InputError("Команды разрешены только для выделенного тестового счета TINVEST_SANDBOX_ACCOUNT_ID")
    return db().enqueue("sandbox:" + payload.account_id, "close_hedge", payload.model_dump(mode="json"), str(payload.command_id))


class CancelCommand(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)
    order_id: uuid.UUID
    command_id: uuid.UUID


@app.post("/api/sandbox/cancel")
def sandbox_cancel(payload: CancelCommand):
    if not os.environ.get("TINVEST_SANDBOX_TOKEN"):
        raise InputError("Токен песочницы не задан")
    if payload.account_id != os.environ.get("TINVEST_SANDBOX_ACCOUNT_ID"):
        raise InputError("Команды разрешены только для выделенного тестового счета TINVEST_SANDBOX_ACCOUNT_ID")
    return db().enqueue("sandbox:" + payload.account_id, "cancel_order", payload.model_dump(mode="json"), str(payload.command_id))


@app.get("/api/sandbox/command/{command_id}")
def sandbox_command_state(command_id: uuid.UUID):
    from sqlalchemy.orm import Session
    from kontur.infrastructure.database import Command
    with Session(db().engine) as session:
        command = session.get(Command, str(command_id))
        if command is None:
            raise HTTPException(404, "Команда не найдена")
        return {"id": command.id, "status": command.status, "error": command.error}


class HaltCommand(AccountId):
    halted: bool


@app.post("/api/sandbox/halt")
def sandbox_halt(payload: HaltCommand):
    from sqlalchemy.orm import Session
    from kontur.infrastructure.database import Account
    with Session(db().engine) as session:
        account = db().account(session, "sandbox:" + payload.account_id, "sandbox")
        account.halted = payload.halted
        session.commit()
    return {"halted": payload.halted}


@app.post("/api/{path:path}")
def missing_api(path: str):
    raise HTTPException(404, "Не найдено")


@app.get("/{path:path}")
def frontend(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Не найдено")
    dist = ROOT / "frontend" / "dist"
    if dist.exists():
        candidate = (dist / path).resolve()
        if candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")
    legacy = ROOT / "static" / (path or "index.html")
    if legacy.is_file() and legacy.resolve().is_relative_to((ROOT / "static").resolve()):
        return FileResponse(legacy)
    raise HTTPException(404, "Не найдено")


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
