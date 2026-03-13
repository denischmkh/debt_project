import os
from datetime import datetime
from sqlalchemy import BigInteger, ForeignKey, DateTime, Float, String
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DB_URL = os.getenv(
    "DATABASE_URL",
)

engine = create_async_engine(DB_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)

class Base(AsyncAttrs, DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String)

class Debt(Base):
    __tablename__ = "debts"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    creditor_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id", ondelete="CASCADE"), index=True)
    debtor_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id", ondelete="CASCADE"), index=True)
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(nullable=True)
    is_paid: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

class DebtClosingConfirmation(Base):
    __tablename__ = "debt_closing_confirmations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    message: Mapped[str] = mapped_column(nullable=False)
    debt_id: Mapped[int] = mapped_column(ForeignKey("debts.id", ondelete="CASCADE"), unique=True, index=True)