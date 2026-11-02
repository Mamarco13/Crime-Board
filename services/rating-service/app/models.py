"""Modelo de persistencia de las valoraciones.

Cada usuario puede tener como máximo un voto por nivel: la clave primaria
compuesta ``(level_id, user_id)`` lo garantiza a nivel de base de datos, de
modo que volver a valorar siempre actualiza la fila existente.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base declarativa de SQLAlchemy 2.x."""


class RatingModel(Base):
    """Valoración (1-5 estrellas) de un nivel por parte de un usuario."""

    __tablename__ = "level_ratings"
    __table_args__ = (
        CheckConstraint(
            "stars >= 1 AND stars <= 5",
            name="ck_level_ratings_stars",
        ),
    )

    level_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stars: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
