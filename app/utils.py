import os

from aiogram import Bot
from dotenv import load_dotenv
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.database import async_session, User, Debt, DebtClosingConfirmation
from app.schemas import UserSchema, DebtReadSchema, DebtClosingConfirmationSchema


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


async def send_notification_to_users(telegram_id: int, message: str):
    load_dotenv()
    bot = Bot(token=os.getenv("BOT_TOKEN"))
    await bot.send_message(chat_id=telegram_id, text=message)

async def get_user_by_telegram_id(telegram_id: int) -> UserSchema:
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = (await session.execute(stmt)).scalar_one_or_none()
        if result:
            return UserSchema.model_validate(result)
        raise HTTPException(status_code=404, detail="User not found")


async def get_debt_confirmation(debt_id: int) -> DebtClosingConfirmationSchema | None:
    async with async_session() as session:
        debtor_alias = aliased(User)
        creditor_alias = aliased(User)
        stmt = (select(DebtClosingConfirmation, Debt, debtor_alias, creditor_alias)
                .join(Debt, Debt.id == DebtClosingConfirmation.debt_id)
                .join(debtor_alias, Debt.debtor_id == debtor_alias.telegram_id)
                .join(creditor_alias, Debt.creditor_id == creditor_alias.telegram_id)
                .where(DebtClosingConfirmation.debt_id == debt_id))
        result = (await session.execute(stmt)).one_or_none()
        if not result:
            return None
        debt_closing_confirmation, debt, debtor, creditor = result
        return DebtClosingConfirmationSchema(
            **debt_closing_confirmation.__dict__,
            debt=DebtReadSchema(
                **debt.__dict__,
                created_at=str(debt.created_at),
                creditor=UserSchema.model_validate(creditor),
                debtor=UserSchema.model_validate(debtor),
            )
        )
