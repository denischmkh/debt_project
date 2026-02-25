from datetime import datetime

from pydantic import BaseModel, ConfigDict
from enum import Enum


class CurrencyEnum(str, Enum):
    USD = "USD"
    EUR = "EUR"
    UAH = "UAH"

class UserSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str
    telegram_id: int

class UserUpdateSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str


class DebtUpdateSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    amount: float | None = None
    currency: str | None = None
    description: str | None = None
    is_paid: bool | None = False

class DebtCreateSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    creditor_id: int
    debtor_id: int
    amount: float
    currency: str
    description: str | None = None
    is_paid: bool = False


class DebtReadSchema(DebtCreateSchema):
    id: int
    creditor: UserSchema
    debtor: UserSchema
    created_at: str