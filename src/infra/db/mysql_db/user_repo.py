"""用户 Repo — users 表 CRUD。"""

from typing import Optional
from sqlalchemy import select
from src.infra.db.models.user import UserModel


class UserRepo:
    """用户 CRUD 仓库。"""

    def __init__(self, session_factory):
        self._sf = session_factory

    async def add_user(self, user_id: str, account: str, password_hash: str) -> None:
        async with self._sf() as session:
            user = UserModel(id=user_id, account=account, password=password_hash)
            session.add(user)
            await session.commit()

    async def get_user_by_account(self, account: str) -> Optional[UserModel]:
        async with self._sf() as session:
            stmt = select(UserModel).where(UserModel.account == account)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_user_token(self, user_id: str, token: str) -> None:
        async with self._sf() as session:
            user = await session.get(UserModel, user_id)
            if user:
                user.token = token
                await session.commit()

    async def get_user_by_token(self, token: str) -> Optional[UserModel]:
        async with self._sf() as session:
            stmt = select(UserModel).where(UserModel.token == token)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()
