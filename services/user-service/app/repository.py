"""Repositorio de cuentas: contrato común y dos implementaciones.

* ``SqlUserRepository`` persiste en PostgreSQL (entorno Docker).
* ``InMemoryUserRepository`` permite arrancar y probar el servicio sin
  infraestructura. Los datos se pierden al reiniciar el proceso.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import UserModel


class UserAlreadyExistsError(Exception):
    """El nombre de usuario o el correo ya están registrados."""

    def __init__(self, field: str) -> None:
        super().__init__(f"Ya existe una cuenta con ese {field}")
        self.field = field


class UserRepository(Protocol):
    """Operaciones que necesita la capa HTTP de cuentas."""

    async def create(
        self,
        *,
        user_id: str,
        username: str,
        email: str,
        password_hash: str,
    ) -> UserModel: ...

    async def get(self, user_id: str) -> UserModel | None: ...

    async def get_by_username(self, username: str) -> UserModel | None: ...

    async def get_by_email(self, email: str) -> UserModel | None: ...

    async def delete(self, user_id: str) -> bool: ...


def generate_user_id() -> str:
    """Identificador opaco; el prefijo facilita leer los logs."""
    return f"usr_{uuid4().hex}"


class InMemoryUserRepository:
    """Directorio efímero protegido por el propio event loop."""

    def __init__(self) -> None:
        self._users: dict[str, UserModel] = {}

    async def create(
        self,
        *,
        user_id: str,
        username: str,
        email: str,
        password_hash: str,
    ) -> UserModel:
        if await self.get_by_username(username) is not None:
            raise UserAlreadyExistsError("nombre de usuario")
        if await self.get_by_email(email) is not None:
            raise UserAlreadyExistsError("correo electrónico")

        user = UserModel(
            user_id=user_id,
            username=username,
            email=email,
            password_hash=password_hash,
        )
        # La BD usa server_default; aquí se fija para que ambos modos
        # devuelvan exactamente la misma forma de respuesta.
        user.created_at = datetime.now(UTC)
        self._users[user_id] = user
        return user

    async def get(self, user_id: str) -> UserModel | None:
        return self._users.get(user_id)

    async def get_by_username(self, username: str) -> UserModel | None:
        needle = username.casefold()
        return next(
            (
                user
                for user in self._users.values()
                if user.username.casefold() == needle
            ),
            None,
        )

    async def get_by_email(self, email: str) -> UserModel | None:
        needle = email.casefold()
        return next(
            (
                user
                for user in self._users.values()
                if user.email.casefold() == needle
            ),
            None,
        )

    async def delete(self, user_id: str) -> bool:
        return self._users.pop(user_id, None) is not None


class SqlUserRepository:
    """Directorio persistido en PostgreSQL."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        *,
        user_id: str,
        username: str,
        email: str,
        password_hash: str,
    ) -> UserModel:
        user = UserModel(
            user_id=user_id,
            username=username,
            email=email,
            password_hash=password_hash,
        )
        async with self._session_factory() as session:
            session.add(user)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                field = "nombre de usuario" if "username" in str(exc) else "correo"
                raise UserAlreadyExistsError(field) from exc

            await session.refresh(user, attribute_names=["created_at"])
            return user

    async def get(self, user_id: str) -> UserModel | None:
        async with self._session_factory() as session:
            return await session.get(UserModel, user_id)

    async def get_by_username(self, username: str) -> UserModel | None:
        statement = select(UserModel).where(
            func.lower(UserModel.username) == username.lower()
        )
        async with self._session_factory() as session:
            return await session.scalar(statement)

    async def get_by_email(self, email: str) -> UserModel | None:
        statement = select(UserModel).where(
            func.lower(UserModel.email) == email.lower()
        )
        async with self._session_factory() as session:
            return await session.scalar(statement)

    async def delete(self, user_id: str) -> bool:
        statement = delete(UserModel).where(UserModel.user_id == user_id)
        async with self._session_factory() as session:
            result = await session.execute(statement)
            await session.commit()
            return bool(result.rowcount)
