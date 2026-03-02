import os
from collections import defaultdict

import httpx
import jinja2
from fastapi import APIRouter, HTTPException, Body, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from six import assertCountEqual
from sqlalchemy import select, update, or_, delete
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

from app.database import Base, engine, async_session, User, Debt, DebtClosingConfirmation
from app.schemas import UserSchema, DebtCreateSchema, DebtReadSchema, DebtUpdateSchema, UserUpdateSchema
from dotenv import load_dotenv

from app.utils import get_debt_full_info, send_notification_to_users, get_debt_confirmation, get_user_by_telegram_id
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

        # Логика подтверждения для должника
        if updated_debt_data.get("is_paid") and current_debt.debtor_id == current_user_id:
            debt_confirmation = await get_debt_confirmation(debt_id=debt_id)

            if not debt_confirmation:
                current_user = await get_user_by_telegram_id(telegram_id=current_user_id)

                # Безопасное получение имени (на случай если current_user это объект или None)
                user_name = current_user.name if current_user else "Неизвестный"

                new_debt_confirmation = DebtClosingConfirmation(
                    debt_id=debt_id,
                    message=f"Должник {user_name} хочет закрыть сумму на {current_debt.amount} {current_debt.currency}",
                )

                session.add(new_debt_confirmation)
                await session.commit()

                # Обновляем объект для получения ID (если нужно)
                await session.refresh(new_debt_confirmation)

                # Безопасная отправка уведомления
                try:
                    await send_notification_to_users(
                        telegram_id=current_debt.creditor_id,
                        message=new_debt_confirmation.message
                    )
                except Exception as e:
                    # Если уведомление не ушло (например, бот заблокирован),
                    # это не должно ломать логику создания запроса. Просто логируем.
                    print(f"ERROR: Failed to send notification: {e}")

                # Возвращаем ошибку 400, которую фронтенд поймет как успех создания запроса
                raise HTTPException(
                    status_code=400,
                    detail=f'Запрос отправлен. Ожидайте подтверждения от {current_debt.creditor.name}'
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f'Запрос уже отправлен. Ожидайте подтверждения от {current_debt.creditor.name}'
                )

        if not updated_debt_data:
            raise HTTPException(status_code=400, detail="No data to update")

        # Логика удаления подтверждения при реальном закрытии (кредитором или после подтверждения)
        if updated_debt_data.get("is_paid"):
            # Безопасное удаление, если подтверждение существует
            stmt_del = delete(DebtClosingConfirmation).where(DebtClosingConfirmation.debt_id == debt_id)
            await session.execute(stmt_del)
            # Не коммитим здесь, коммит произойдет после update debt одной транзакцией

        stmt = update(Debt).where(Debt.id == debt_id).values(**updated_debt_data)
        result = await session.execute(stmt)

        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Debt not found")

        await session.commit()

    # --- Уведомления об обновлении ---
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

    # Также оборачиваем в try-except, чтобы не падало при ответе
    try:
        await ws_manager.broadcast_user_update(updated_debt.debtor.telegram_id)
        await ws_manager.broadcast_user_update(updated_debt.creditor.telegram_id)
        await send_notification_to_users(updated_debt.debtor.telegram_id, debtor_msg)
        await send_notification_to_users(updated_debt.creditor.telegram_id, creditor_msg)
    except Exception as e:
        print(f"ERROR: Post-update notification failed: {e}")

    return updated_debt