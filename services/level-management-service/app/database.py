"""Conexión asíncrona a PostgreSQL mediante SQLAlchemy 2.x.

Para el MVP las tablas se crean con ``Base.metadata.create_all`` al arrancar.
Cuando el esquema empiece a evolucionar, este es el punto donde sustituirlo
por migraciones de Alembic.
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
    """Fuerza un driver asíncrono en URLs de estilo PostgreSQL/Heroku.

    Compose ya inyecta ``postgresql+asyncpg://``, pero esta normalización evita
    errores confusos si alguien reutiliza una URL de un cliente síncrono.
    """
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
    """Crea el engine, verifica la conexión y garantiza el esquema."""
    engine = create_async_engine(
        normalise_database_url(database_url),
        pool_pre_ping=True,
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            # Migraciones ligeras e idempotentes para volúmenes creados con una
            # versión anterior del esquema. En un proyecto con más historia este
            # paso se sustituiría por una migración de Alembic.
            migrations = (
                # Autoría de los casos (Hito 3).
                (
                    "ALTER TABLE levels "
                    "ADD COLUMN IF NOT EXISTS creator_username "
                    "VARCHAR(64) NOT NULL DEFAULT 'Anónimo'"
                ),
                # Tarjetas recuperables desde el archivador (Hito 2).
                (
                    "ALTER TABLE cards "
                    "ADD COLUMN IF NOT EXISTS hidden "
                    "BOOLEAN NOT NULL DEFAULT FALSE"
                ),
                (
                    "ALTER TABLE cards "
                    "ADD COLUMN IF NOT EXISTS aliases "
                    "JSON NOT NULL DEFAULT '[]'::json"
                ),
            )
            for statement in migrations:
                await connection.execute(text(statement))
    except Exception:
        await engine.dispose()
        raise

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory
