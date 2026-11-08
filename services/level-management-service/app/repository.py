"""Repositorio de niveles: contrato único, dos implementaciones.

* ``SqlLevelRepository`` usa PostgreSQL/SQLAlchemy y es la fuente de verdad en
  el entorno Docker.
* ``InMemoryLevelRepository`` permite arrancar el servicio sin infraestructura
  (desarrollo local, pruebas manuales y demos). Los datos se pierden al
  reiniciar el proceso.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from .models import CardModel, LevelCreate, LevelModel, normalize_alias


class LevelAlreadyExistsError(Exception):
    """El ``level_id`` solicitado ya está ocupado."""

    def __init__(self, level_id: str) -> None:
        super().__init__(f"El nivel '{level_id}' ya existe")
        self.level_id = level_id


class LevelRepository(Protocol):
    """Operaciones que necesita la capa HTTP del catálogo."""

    async def create(
        self,
        payload: LevelCreate,
        *,
        creator_username: str = "Anónimo",
    ) -> LevelModel: ...

    async def get(self, level_id: str) -> LevelModel | None: ...

    async def list_page(
        self,
        *,
        limit: int,
        offset: int,
        creator_username: str | None = None,
    ) -> tuple[list[LevelModel], int]: ...


def generate_level_id() -> str:
    """Identificador opaco y único; el prefijo facilita leer los logs."""
    return f"lvl_{uuid4().hex}"


def _build_level_model(
    payload: LevelCreate,
    level_id: str,
    *,
    creator_username: str = "Anónimo",
    created_at: datetime | None = None,
) -> LevelModel:
    """Traduce el esquema Pydantic validado a entidades SQLAlchemy."""
    level = LevelModel(
        level_id=level_id,
        title=payload.title,
        description=payload.description,
        difficulty=payload.difficulty,
        creator_username=creator_username.strip()[:64] or "Anónimo",
        solution_card_ids=list(payload.solution_card_ids),
        reveal_solution_composition=payload.reveal_solution_composition,
        cards=[
            CardModel(
                level_id=level_id,
                id=card.id,
                type=card.type,
                title=card.title,
                description=card.description,
                image_url=card.image_url,
                hidden=card.hidden,
                aliases=[normalize_alias(alias) for alias in card.aliases],
                position=position,
            )
            for position, card in enumerate(payload.cards)
        ],
    )
    if created_at is not None:
        # La BD usa server_default; el repositorio en memoria lo fija aquí para
        # que ambos modos devuelvan exactamente la misma forma de respuesta.
        level.created_at = created_at
    return level


class InMemoryLevelRepository:
    """Catálogo efímero protegido por el propio event loop de asyncio."""

    def __init__(self) -> None:
        self._levels: dict[str, LevelModel] = {}

    async def create(
        self,
        payload: LevelCreate,
        *,
        creator_username: str = "Anónimo",
    ) -> LevelModel:
        level_id = payload.level_id or generate_level_id()
        if level_id in self._levels:
            raise LevelAlreadyExistsError(level_id)

        level = _build_level_model(
            payload,
            level_id,
            creator_username=creator_username,
            created_at=datetime.now(UTC),
        )
        self._levels[level_id] = level
        return level

    async def get(self, level_id: str) -> LevelModel | None:
        return self._levels.get(level_id)

    async def list_page(
        self,
        *,
        limit: int,
        offset: int,
        creator_username: str | None = None,
    ) -> tuple[list[LevelModel], int]:
        ordered = sorted(
            self._levels.values(),
            key=lambda level: (level.created_at, level.level_id),
            reverse=True,
        )
        if creator_username:
            expected = creator_username.lower()
            ordered = [
                level
                for level in ordered
                if level.creator_username.lower() == expected
            ]
        return ordered[offset : offset + limit], len(ordered)


class SqlLevelRepository:
    """Catálogo persistido en PostgreSQL."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        payload: LevelCreate,
        *,
        creator_username: str = "Anónimo",
    ) -> LevelModel:
        level_id = payload.level_id or generate_level_id()
        level = _build_level_model(
            payload,
            level_id,
            creator_username=creator_username,
        )

        async with self._session_factory() as session:
            session.add(level)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise LevelAlreadyExistsError(level_id) from exc

            # ``created_at`` lo calcula PostgreSQL; refrescamos solo esa columna
            # para no recargar las tarjetas ya presentes en memoria.
            await session.refresh(level, attribute_names=["created_at"])
            return level

    async def get(self, level_id: str) -> LevelModel | None:
        statement = (
            select(LevelModel)
            .options(selectinload(LevelModel.cards))
            .where(LevelModel.level_id == level_id)
        )
        async with self._session_factory() as session:
            return await session.scalar(statement)

    async def list_page(
        self,
        *,
        limit: int,
        offset: int,
        creator_username: str | None = None,
    ) -> tuple[list[LevelModel], int]:
        statement = (
            select(LevelModel)
            .options(selectinload(LevelModel.cards))
            .order_by(LevelModel.created_at.desc(), LevelModel.level_id.asc())
            .offset(offset)
            .limit(limit)
        )
        count_statement = select(func.count()).select_from(LevelModel)
        if creator_username:
            condition = func.lower(LevelModel.creator_username) == (
                creator_username.lower()
            )
            statement = statement.where(condition)
            count_statement = count_statement.where(condition)

        async with self._session_factory() as session:
            levels = list((await session.scalars(statement)).all())
            total = await session.scalar(count_statement) or 0

        return levels, total
