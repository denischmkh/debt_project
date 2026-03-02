import os
from collections import defaultdict

import httpx
import jinja2
from fastapi import APIRouter, HTTPException, Body, Path, Query
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
from dotenv import load_dotenv

from app.utils import get_debt_full_info, send_notification_to_users
from app.ws import ws_manager

load_dotenv()

router = APIRouter(tags=["v2_routers"], prefix="/v2")

@router.patch('/debt/update/{debt_id}', response_model=DebtReadSchema)
async def update_debt(
        updated_schema: DebtUpdateSchema,
        debt_id: int = Path(...),
        current_user_id: int = Query(...),
):
    current_debt = await get_debt_full_info(debt_id)
    async with async_session() as session:
        updated_debt_data = updated_schema.model_dump(
            exclude_unset=True,
            exclude_none=True
        )
        if current_debt.debtor_id == current_user_id:
            pass

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