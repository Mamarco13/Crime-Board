"""Modelos de persistencia del Progress Service.

El servicio guarda cinco cosas por usuario:

* ``case_notes``: libreta general y por caso, con versión optimista.
* ``case_workspace``: tarjetas tachadas y selección de acusación.
* ``case_sessions``: partidas (primera, repeticiones y abandonadas).
* ``case_attempts``: cada acusación registrada por el motor. La clave
  ``(user_id, client_attempt_id)`` hace la escritura idempotente.
* ``user_level_stats``: resumen materializado por usuario y nivel. La primera
  resolución es inmutable; las repeticiones nunca la modifican.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base declarativa de SQLAlchemy 2.x."""


class CaseSessionModel(Base):
    """Una partida de un usuario en un nivel."""

    __tablename__ = "case_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'solved', 'abandoned')",
            name="ck_case_sessions_status",
        ),
        # Solo puede haber una partida activa por usuario y nivel.
        Index(
            "ux_case_sessions_active",
            "user_id",
            "level_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_case_sessions_user_recent",
            "user_id",
            "last_activity_at",
        ),
    )

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    level_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    is_replay: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    replay_of: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("case_sessions.session_id"),
        nullable=True,
    )
    client_session_key: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    solved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    attempts_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )


class CaseAttemptModel(Base):
    """Una acusación registrada por el Playground Engine."""

    __tablename__ = "case_attempts"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "client_attempt_id",
            name="ux_case_attempts_client",
        ),
        Index(
            "ix_case_attempts_user_time",
            "user_id",
            "attempted_at",
        ),
    )

    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_attempt_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("case_sessions.session_id"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    level_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    solved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    selected_card_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    first_solve: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )


class UserLevelStatsModel(Base):
    """Resumen por usuario y nivel; la primera resolución no se reescribe."""

    __tablename__ = "user_level_stats"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    level_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_solved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    first_solve_attempts: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    total_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    total_solves: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    replay_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    last_played_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class CaseNoteModel(Base):
    """Libreta privada. ``level_key`` vale ``__general__`` para la general."""

    __tablename__ = "case_notes"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    level_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class CaseWorkspaceModel(Base):
    """Estado visual del tablero: tachados y acusación en curso."""

    __tablename__ = "case_workspace"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    level_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    crossed_out_ids: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    accusation_ids: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
