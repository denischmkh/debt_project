# --- WebSocket Manager ---
from collections import defaultdict

from sqlalchemy import select, or_
from sqlalchemy.orm import aliased
from starlette.websockets import WebSocket

from app.database import Debt, async_session, User
from app.schemas import UserSchema, DebtReadSchema


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