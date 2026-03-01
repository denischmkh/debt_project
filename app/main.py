import os
from collections import defaultdict

import httpx
import jinja2
from fastapi import FastAPI, HTTPException, Body, Path
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, update, or_
from sqlalchemy.orm import aliased
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.templating import Jinja2Templates
from starlette.websockets import WebSocket, WebSocketDisconnect
from contextlib import asynccontextmanager


from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from fastapi_cache.decorator import cache

from redis import asyncio as aioredis

from aiogram import Bot

from app.database import Base, engine, async_session, User, Debt
from app.schemas import UserSchema, DebtCreateSchema, DebtReadSchema, DebtUpdateSchema, UserUpdateSchema
from app.deploy import router as deploy_router
from dotenv import load_dotenv

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        redis = aioredis.from_url(os.getenv("REDIS_URL"))
        FastAPICache.init(RedisBackend(redis), prefix="fastapi-cache")
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield

bot = Bot(token=os.getenv("BOT_TOKEN"))

app = FastAPI(lifespan=lifespan)

app.include_router(deploy_router)

app.add_middleware(CORSMiddleware, allow_origins=["*"])


# --- Утилиты ---

async def get_debt_full_info(debt_id: int) -> DebtReadSchema:
    async with async_session() as session:
        debtor_alias = aliased(User)
        creditor_alias = aliased(User)
        # ВАЖНО: Джойнимся по telegram_id
        stmt = (
            select(Debt, debtor_alias, creditor_alias)
            .join(debtor_alias, Debt.debtor_id == debtor_alias.telegram_id)
            .join(creditor_alias, Debt.creditor_id == creditor_alias.telegram_id)
            .where(Debt.id == debt_id)
        )
        result = await session.execute(stmt)
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Debt not found")

        debt, debtor, creditor = row
        return DebtReadSchema(
            id=debt.id,
            amount=debt.amount,
            currency=debt.currency,
            description=debt.description,
            is_paid=debt.is_paid,
            creditor_id=creditor.telegram_id,
            debtor_id=debtor.telegram_id,
            creditor=UserSchema.model_validate(creditor),
            debtor=UserSchema.model_validate(debtor),
            created_at=str(debt.created_at),
        )


# --- WebSocket Manager ---

class WebsocketManager:
    def __init__(self):
        # Используем defaultdict(list) для автоматического создания списка при обращении
        self.active_connections: dict[int, list[WebSocket]] = defaultdict(list)

    async def get_user_debts(self, tg_id: int):
        async with async_session() as session:
            debtor_alias, creditor_alias = aliased(User), aliased(User)
            stmt = (
                select(Debt, debtor_alias, creditor_alias)
                .join(debtor_alias, Debt.debtor_id == debtor_alias.telegram_id)
                .join(creditor_alias, Debt.creditor_id == creditor_alias.telegram_id)
                .where(or_(Debt.creditor_id == tg_id, Debt.debtor_id == tg_id))
            )
            rows = (await session.execute(stmt)).all()
            return [
                DebtReadSchema(
                    id=debt.id,
                    amount=debt.amount,
                    currency=debt.currency,
                    description=debt.description,
                    is_paid=debt.is_paid,
                    debtor=UserSchema.model_validate(debtor),
                    creditor=UserSchema.model_validate(creditor),
                    debtor_id=debt.debtor_id,
                    creditor_id=debt.creditor_id,
                    created_at=str(debt.created_at),
                ) for debt, debtor, creditor in rows
            ]

    async def connect(self, tg_id: int, ws: WebSocket):
        await ws.accept()
        # Теперь просто добавляем новый сокет в список этого пользователя
        self.active_connections[tg_id].append(ws)
        await self.broadcast_user_update(tg_id)

    def disconnect(self, telegram_id: int, ws: WebSocket):
        # Удаляем конкретный сокет из списка
        if telegram_id in self.active_connections:
            if ws in self.active_connections[telegram_id]:
                self.active_connections[telegram_id].remove(ws)
            # Если у пользователя больше нет активных соединений, удаляем ключ
            if not self.active_connections[telegram_id]:
                del self.active_connections[telegram_id]

    async def broadcast_user_update(self, telegram_id: int):
        # Если пользователя нет в словаре или список пуст — выходим
        if telegram_id not in self.active_connections or not self.active_connections[telegram_id]:
            return

        data = await self.get_user_debts(telegram_id)
        payload = [d.model_dump() for d in data]

        # Перебираем все сокеты пользователя (для всех его устройств)
        # Делаем срез [:], чтобы безопасно удалять элементы во время итерации
        for ws in self.active_connections[telegram_id][:]:
            try:
                await ws.send_json(payload)
            except Exception:
                # Если сокет закрыт или недоступен, удаляем его из списка
                self.disconnect(telegram_id, ws)

ws_manager = WebsocketManager()

async def send_notification_to_users(telegram_id: int, message: str):
    await bot.send_message(chat_id=telegram_id, text=message)

async def get_user_by_telegram_id(telegram_id: int) -> UserSchema:
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = (await session.execute(stmt)).scalar_one_or_none()
        if result:
            return UserSchema.model_validate(result)
        raise HTTPException(status_code=404, detail="User not found")

# --- Роутеры ---

templates = Jinja2Templates(directory="templates")

@app.get("/")
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/user/exist", response_model=UserSchema)
async def check_user_exist(user_id: int):
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == user_id)
        result = (await session.execute(stmt)).scalar_one_or_none()
        if result:
            return UserSchema.model_validate(result)
        raise HTTPException(status_code=404, detail="User not found")


@app.post("/user/create", response_model=UserSchema)
async def create_user(user: UserSchema):
    async with async_session() as session:
        new_user = User(name=user.name, telegram_id=user.telegram_id)
        session.add(new_user)
        try:
            await session.commit()
            await session.refresh(new_user)
            return UserSchema.model_validate(new_user)
        except Exception:
            await session.rollback()
            raise HTTPException(status_code=400, detail="User already exists")


@app.post("/user/update/{telegram_id}", response_model=UserSchema)
async def update_user(user: UserUpdateSchema = Body(...), telegram_id: int = Path(...)):
    async with async_session() as session:
        stmt = update(User).where(User.telegram_id == telegram_id).values(name=user.name)
        await session.execute(stmt)
        await session.commit()

    # Теперь запрос к базе вернет уже обновленное имя
    updated_user = await check_user_exist(telegram_id)
    return updated_user

@app.get('/users', response_model=list[UserSchema])
async def get_users():
    async with async_session() as session:
        stmt = select(User)
        result = (await session.execute(stmt)).scalars().all()
        return [UserSchema.model_validate(user) for user in result]

@app.post("/debt/create")
async def create_debt(debt_data: DebtCreateSchema):
    async with async_session() as session:
        # Прямая вставка без поиска маппинга
        new_debt = Debt(
            creditor_id=debt_data.creditor_id,
            debtor_id=debt_data.debtor_id,
            amount=debt_data.amount,
            currency=debt_data.currency,
            description=debt_data.description
        )
        session.add(new_debt)
        await session.commit()
        await session.refresh(new_debt)

    # Уведомляем обоих участников
    await ws_manager.broadcast_user_update(debt_data.creditor_id)
    await ws_manager.broadcast_user_update(debt_data.debtor_id)
    await send_notification_to_users(debt_data.creditor_id, f"Новый должник {get_user_by_telegram_id(debt_data.debtor_id)} - сумма {debt_data.amount} {debt_data.currency}")
    await send_notification_to_users(debt_data.debtor_id, f"У вас новый долг перед {get_user_by_telegram_id(debt_data.creditor_id)} - сумма {debt_data.amount} {debt_data.currency}")
    return await get_debt_full_info(new_debt.id)


@app.patch('/debt/update/{debt_id}', response_model=DebtReadSchema)
async def update_debt(updated_schema: DebtUpdateSchema, debt_id: int = Path(...)):
    async with async_session() as session:
        updated_debt_data = updated_schema.model_dump(
            exclude_unset=True,
            exclude_none=True
        )

        if not updated_debt_data:
            raise HTTPException(status_code=400, detail="No data to update")

        stmt = update(Debt).where(Debt.id == debt_id).values(**updated_debt_data)
        result = await session.execute(stmt)

        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Debt not found")

        await session.commit()

    updated_debt = await get_debt_full_info(debt_id=debt_id)

    amount_str = f"{updated_debt.amount} {updated_debt.currency}"
    desc_str = f" ({updated_debt.description})" if updated_debt.description else ""

    status_icon = "✅" if updated_debt.is_paid else "🔄"
    action_text = "отмечен как ОПЛАЧЕН" if updated_debt.is_paid else "ОБНОВЛЕН"

    debtor_msg = (
        f"{status_icon} Ваш долг перед {updated_debt.creditor.name} {action_text}.\n"
        f"Сумма: {amount_str}{desc_str}"
    )

    creditor_msg = (
        f"{status_icon} Долг от {updated_debt.debtor.name} {action_text}.\n"
        f"Сумма: {amount_str}{desc_str}"
    )

    await ws_manager.broadcast_user_update(updated_debt.debtor.telegram_id)
    await ws_manager.broadcast_user_update(updated_debt.creditor.telegram_id)

    await send_notification_to_users(updated_debt.debtor.telegram_id, debtor_msg)
    await send_notification_to_users(updated_debt.creditor.telegram_id, creditor_msg)

    return updated_debt


@app.websocket('/ws/{telegram_id}')
async def websocket_endpoint(websocket: WebSocket, telegram_id: int):
    await ws_manager.connect(telegram_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(telegram_id, websocket)


@app.get("/currency")
@cache(600)
async def get_currency():
    url = "https://api.monobank.ua/bank/currency"

    # Коды валют по ISO 4217
    USD_CODE = 840
    EUR_CODE = 978
    UAH_CODE = 980

    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        if response.status_code != 200:
            return {"error": "Не удалось получить данные"}

        data = response.json()

    # Сначала найдем курс USD/UAH, чтобы через него вычислить стоимость UAH в долларах
    usd_to_uah_rate = 0
    for item in data:
        if item['currencyCodeA'] == USD_CODE and item['currencyCodeB'] == UAH_CODE:
            # Считаем среднее между покупкой и продажей
            usd_to_uah_rate = (item['rateBuy'] + item['rateSell']) / 2
            break

    # Итоговый словарь. USD всегда 1 (доллар за доллар)
    result = {
        "USD": 1.0
    }

    if usd_to_uah_rate > 0:
        # 1. Считаем UAH (сколько долларов в 1 гривне)
        result["UAH"] = round(1 / usd_to_uah_rate, 6)

        # 2. Считаем остальные валюты
        for item in data:
            # Ищем Евро к Гривне
            if item['currencyCodeA'] == EUR_CODE and item['currencyCodeB'] == UAH_CODE:
                eur_to_uah_avg = (item['rateBuy'] + item['rateSell']) / 2
                # Переводим Евро в Доллары через Гривну (EUR -> UAH -> USD)
                result["EUR"] = round(eur_to_uah_avg / usd_to_uah_rate, 4)

            # Если в API есть прямой курс EUR/USD (такое бывает), можно использовать его
            elif item['currencyCodeA'] == EUR_CODE and item['currencyCodeB'] == USD_CODE:
                result["EUR"] = round((item['rateBuy'] + item['rateSell']) / 2, 4)
    return result