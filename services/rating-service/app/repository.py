"""Repositorio de valoraciones: contrato común y dos implementaciones.

* ``SqlRatingRepository`` persiste en PostgreSQL (entorno Docker).
* ``InMemoryRatingRepository`` permite arrancar y probar el servicio sin
  infraestructura. Los datos se pierden al reiniciar el proceso.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import RatingModel


@dataclass(frozen=True, slots=True)
class RatingAggregate:
    """Media y número de votos de un nivel."""

    average_rating: float
    total_votes: int


EMPTY_AGGREGATE = RatingAggregate(average_rating=0.0, total_votes=0)


def _aggregate(total_votes: int, stars_sum: float) -> RatingAggregate:
    """Calcula la media redondeada a un decimal y acotada a [0.0, 5.0]."""
    if total_votes <= 0:
        return EMPTY_AGGREGATE
    average = stars_sum / total_votes
    # Redondeo half-up: 4.25 -> 4.3 (round() de Python usaría banker's).
    rounded = math.floor(average * 10 + 0.5) / 10
    return RatingAggregate(
        average_rating=min(max(rounded, 0.0), 5.0),
        total_votes=total_votes,
    )


class RatingRepository(Protocol):
    """Operaciones que necesita la capa HTTP de valoraciones."""

    async def upsert(self, *, level_id: str, user_id: str, stars: int) -> None: ...

    async def summary(self, level_id: str) -> RatingAggregate: ...

    async def summaries(
        self,
        level_ids: list[str],
    ) -> dict[str, RatingAggregate]: ...


class InMemoryRatingRepository:
    """Valoraciones efímeras protegidas por el propio event loop."""

    def __init__(self) -> None:
        self._ratings: dict[tuple[str, str], int] = {}

    async def upsert(self, *, level_id: str, user_id: str, stars: int) -> None:
        self._ratings[(level_id, user_id)] = stars

    async def summary(self, level_id: str) -> RatingAggregate:
        return (await self.summaries([level_id]))[level_id]

    async def summaries(
        self,
        level_ids: list[str],
    ) -> dict[str, RatingAggregate]:
        totals: dict[str, tuple[int, int]] = {}
        for (level_id, _), stars in self._ratings.items():
            count, stars_sum = totals.get(level_id, (0, 0))
            totals[level_id] = (count + 1, stars_sum + stars)
        result: dict[str, RatingAggregate] = {}
        for level_id in level_ids:
            count, stars_sum = totals.get(level_id, (0, 0))
            result[level_id] = _aggregate(count, float(stars_sum))
        return result


class SqlRatingRepository:
    """Valoraciones persistidas en PostgreSQL."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def upsert(self, *, level_id: str, user_id: str, stars: int) -> None:
        statement = select(RatingModel).where(
            RatingModel.level_id == level_id,
            RatingModel.user_id == user_id,
        )
        async with self._session_factory() as session:
            rating = await session.scalar(statement)
            if rating is None:
                session.add(
                    RatingModel(level_id=level_id, user_id=user_id, stars=stars)
                )
            else:
                rating.stars = stars
            try:
                await session.commit()
            except IntegrityError:
                # Dos peticiones del mismo usuario han insertado a la vez: la
                # clave primaria compuesta decide y la perdedora actualiza.
                await session.rollback()
                rating = await session.scalar(statement)
                if rating is None:
                    raise
                rating.stars = stars
                await session.commit()

    async def summary(self, level_id: str) -> RatingAggregate:
        return (await self.summaries([level_id]))[level_id]

    async def summaries(
        self,
        level_ids: list[str],
    ) -> dict[str, RatingAggregate]:
        if not level_ids:
            return {}

        statement = (
            select(
                RatingModel.level_id,
                func.count(RatingModel.user_id),
                func.sum(RatingModel.stars),
            )
            .where(RatingModel.level_id.in_(level_ids))
            .group_by(RatingModel.level_id)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()

        aggregates = {
            row[0]: _aggregate(int(row[1]), float(row[2] or 0.0))
            for row in rows
        }
        return {level_id: aggregates.get(level_id, EMPTY_AGGREGATE) for level_id in level_ids}
