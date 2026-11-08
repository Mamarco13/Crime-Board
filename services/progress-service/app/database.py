"""Conexión asíncrona a PostgreSQL para el Progress Service.

El MVP crea las tablas al arrancar con ``Base.metadata.create_all``. El índice
parcial que garantiza una sola partida activa por usuario y nivel se declara en
el modelo y PostgreSQL lo crea junto con la tabla. Cuando el esquema evolucione,
este es el punto de entrada para Alembic (o para migraciones idempotentes
``ALTER TABLE ... IF NOT EXISTS`` como en el resto de servicios).
"""

from __future__ import annotations

from sqlalchemy import text
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
    """Crea el engine, verifica la conexión y garantiza las tablas."""
    engine = create_async_engine(
        normalise_database_url(database_url),
        pool_pre_ping=True,
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            # Migraciones idempotentes para instalaciones creadas antes de que
            # existiera el número de intento por partida.
            migrations = (
                (
                    "ALTER TABLE case_attempts "
                    "ADD COLUMN IF NOT EXISTS attempt_number INTEGER "
                    "NOT NULL DEFAULT 1"
                ),
            )
            for statement in migrations:
                await connection.execute(text(statement))
    except Exception:
        await engine.dispose()
        raise

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory
