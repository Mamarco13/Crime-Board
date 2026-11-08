"""Conexión asíncrona a PostgreSQL para el User Service.

El MVP crea la tabla ``users`` al arrancar con ``Base.metadata.create_all``.
Cuando el esquema evolucione, este es el punto de entrada para Alembic.
"""

from __future__ import annotations

from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .models import Base


def normalise_database_url(database_url: str) -> str:
    """Fuerza el driver asyncpg en URLs PostgreSQL estándar."""
    if database_url.startswith("postgres://"):
        return "postgresql+asyncpg://" + database_url.removeprefix("postgres://")
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url.removeprefix("postgresql://")
    if "://" not in database_url:
        raise ArgumentError(
            "DATABASE_URL debe ser una URL SQLAlchemy (por ejemplo "
            "postgresql+asyncpg://usuario:clave@host:5432/basedatos)"
        )
    return database_url


async def init_database(
    database_url: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Crea el engine, verifica la conexión y garantiza la tabla ``users``."""
    engine = create_async_engine(
        normalise_database_url(database_url),
        pool_pre_ping=True,
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    except Exception:
        await engine.dispose()
        raise

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory
