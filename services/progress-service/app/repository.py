"""Repositorio de progreso: reglas comunes y dos implementaciones.

* ``SqlProgressRepository`` persiste en PostgreSQL (entorno Docker).
* ``InMemoryProgressRepository`` permite arrancar y probar el servicio sin
  infraestructura. Los datos se pierden al reiniciar el proceso.

Las reglas de negocio viven aquí para que la capa HTTP sea solo traducción de
contratos. La primera resolución se calcula dentro de la misma transacción que
registra el intento y nunca se reescribe en repeticiones.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import (
    CaseAttemptModel,
    CaseNoteModel,
    CaseSessionModel,
    CaseWorkspaceModel,
    UserLevelStatsModel,
)

GENERAL_SCOPE = "__general__"


def _now() -> datetime:
    return datetime.now(UTC)


def _dedupe(values: list[str]) -> list[str]:
    """Mantiene el orden y elimina duplicados sin depender de ``set``."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


@dataclass(frozen=True, slots=True)
class NoteRecord:
    user_id: str
    level_key: str
    content: str
    version: int
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    user_id: str
    level_id: str
    crossed_out_ids: list[str]
    accusation_ids: list[str]
    version: int
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: str
    user_id: str
    level_id: str
    status: str
    is_replay: bool
    replay_of: str | None
    started_at: datetime
    last_activity_at: datetime
    finished_at: datetime | None
    solved_at: datetime | None
    attempts_count: int
    first_solved_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StatsRecord:
    user_id: str
    level_id: str
    first_solved_at: datetime | None
    first_solve_attempts: int | None
    total_attempts: int
    total_solves: int
    replay_count: int
    last_played_at: datetime


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    attempt_id: str
    client_attempt_id: str
    session_id: str
    user_id: str
    level_id: str
    attempted_at: datetime
    solved: bool
    selected_card_ids: list[str]
    elapsed_ms: int
    first_solve: bool
    attempt_number: int


@dataclass(frozen=True, slots=True)
class AttemptResult:
    recorded: bool
    idempotent: bool
    session_id: str
    attempt_number: int
    first_solve: bool
    is_replay: bool
    first_solve_attempts: int | None


@dataclass(frozen=True, slots=True)
class SummaryRecord:
    cases_played: int
    cases_solved: int
    solve_rate: float
    first_solve_attempts_total: int
    average_attempts_to_first_solve: float
    total_attempts: int
    replay_count: int
    last_solved_at: datetime | None


@dataclass(frozen=True, slots=True)
class CaseDetail:
    stats: StatsRecord | None
    sessions: list[SessionRecord]
    attempts: list[AttemptRecord]


class NoteConflictError(Exception):
    """La versión enviada por el cliente ya no es la actual."""

    def __init__(self, current: NoteRecord | None) -> None:
        super().__init__("La libreta ha cambiado en otro dispositivo")
        self.current = current


class WorkspaceConflictError(Exception):
    """El espacio de trabajo cambió en otra pestaña o dispositivo."""

    def __init__(self, current: WorkspaceRecord) -> None:
        super().__init__("El espacio de trabajo ha cambiado en otro dispositivo")
        self.current = current


class ProgressRepository(Protocol):
    """Operaciones que necesita la capa HTTP de progreso."""

    async def get_note(
        self,
        *,
        user_id: str,
        level_key: str,
    ) -> NoteRecord | None: ...

    async def upsert_note(
        self,
        *,
        user_id: str,
        level_key: str,
        content: str,
        base_version: int | None,
    ) -> NoteRecord: ...

    async def get_workspace(
        self,
        *,
        user_id: str,
        level_id: str,
    ) -> WorkspaceRecord: ...

    async def upsert_workspace(
        self,
        *,
        user_id: str,
        level_id: str,
        crossed_out_ids: list[str],
        accusation_ids: list[str],
        base_version: int | None,
    ) -> WorkspaceRecord: ...

    async def get_or_create_session(
        self,
        *,
        user_id: str,
        level_id: str,
        client_session_key: str | None = None,
    ) -> SessionRecord: ...

    async def restart_session(
        self,
        *,
        user_id: str,
        level_id: str,
        client_session_key: str | None = None,
    ) -> SessionRecord: ...

    async def get_state(
        self,
        *,
        user_id: str,
        level_id: str,
    ) -> tuple[SessionRecord | None, WorkspaceRecord, StatsRecord | None]: ...

    async def record_attempt(
        self,
        *,
        user_id: str,
        level_id: str,
        client_attempt_id: str,
        session_key: str | None,
        solved: bool,
        selected_card_ids: list[str],
        elapsed_ms: int,
    ) -> AttemptResult: ...

    async def summary(self, *, user_id: str) -> SummaryRecord: ...

    async def list_cases(
        self,
        *,
        user_id: str,
        status_filter: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[StatsRecord], int]: ...

    async def get_case_detail(
        self,
        *,
        user_id: str,
        level_id: str,
    ) -> CaseDetail: ...

    async def purge_user(self, *, user_id: str) -> dict[str, int]: ...


def _empty_workspace(user_id: str, level_id: str) -> WorkspaceRecord:
    return WorkspaceRecord(
        user_id=user_id,
        level_id=level_id,
        crossed_out_ids=[],
        accusation_ids=[],
        version=0,
        updated_at=None,
    )


def _summary_from_stats(
    stats: list[StatsRecord],
    last_solved_at: datetime | None,
) -> SummaryRecord:
    cases_played = len(stats)
    solved = [row for row in stats if row.first_solved_at is not None]
    cases_solved = len(solved)
    first_attempts = sum(row.first_solve_attempts or 0 for row in solved)
    total_attempts = sum(row.total_attempts for row in stats)
    replay_count = sum(row.replay_count for row in stats)
    average = (first_attempts / cases_solved) if cases_solved else 0.0
    if last_solved_at is None and solved:
        last_solved_at = max(
            row.first_solved_at for row in solved if row.first_solved_at
        )
    return SummaryRecord(
        cases_played=cases_played,
        cases_solved=cases_solved,
        solve_rate=(cases_solved / cases_played) if cases_played else 0.0,
        first_solve_attempts_total=first_attempts,
        average_attempts_to_first_solve=average,
        total_attempts=total_attempts,
        replay_count=replay_count,
        last_solved_at=last_solved_at,
    )


class InMemoryProgressRepository:
    """Progreso efímero protegido por el propio event loop."""

    def __init__(self) -> None:
        self._notes: dict[tuple[str, str], NoteRecord] = {}
        self._workspaces: dict[tuple[str, str], WorkspaceRecord] = {}
        self._sessions: dict[str, SessionRecord] = {}
        self._attempts: dict[str, AttemptRecord] = {}
        self._attempt_keys: dict[tuple[str, str], str] = {}
        self._stats: dict[tuple[str, str], StatsRecord] = {}

    # -- Libreta ---------------------------------------------------------
    async def get_note(
        self,
        *,
        user_id: str,
        level_key: str,
    ) -> NoteRecord | None:
        return self._notes.get((user_id, level_key))

    async def upsert_note(
        self,
        *,
        user_id: str,
        level_key: str,
        content: str,
        base_version: int | None,
    ) -> NoteRecord:
        current = self._notes.get((user_id, level_key))
        current_version = current.version if current else 0
        if base_version is not None and base_version != current_version:
            raise NoteConflictError(current)
        record = NoteRecord(
            user_id=user_id,
            level_key=level_key,
            content=content,
            version=current_version + 1,
            updated_at=_now(),
        )
        self._notes[(user_id, level_key)] = record
        return record

    # -- Espacio de trabajo ---------------------------------------------
    async def get_workspace(
        self,
        *,
        user_id: str,
        level_id: str,
    ) -> WorkspaceRecord:
        return self._workspaces.get(
            (user_id, level_id),
            _empty_workspace(user_id, level_id),
        )

    async def upsert_workspace(
        self,
        *,
        user_id: str,
        level_id: str,
        crossed_out_ids: list[str],
        accusation_ids: list[str],
        base_version: int | None,
    ) -> WorkspaceRecord:
        current = await self.get_workspace(user_id=user_id, level_id=level_id)
        if base_version is not None and base_version != current.version:
            raise WorkspaceConflictError(current)
        record = WorkspaceRecord(
            user_id=user_id,
            level_id=level_id,
            crossed_out_ids=_dedupe(crossed_out_ids),
            accusation_ids=_dedupe(accusation_ids),
            version=current.version + 1,
            updated_at=_now(),
        )
        self._workspaces[(user_id, level_id)] = record
        return record

    # -- Sesiones --------------------------------------------------------
    def _active_session(
        self,
        user_id: str,
        level_id: str,
    ) -> SessionRecord | None:
        for session in self._sessions.values():
            if (
                session.user_id == user_id
                and session.level_id == level_id
                and session.status == "active"
            ):
                return session
        return None

    def _first_session(
        self,
        user_id: str,
        level_id: str,
    ) -> SessionRecord | None:
        candidates = [
            session
            for session in self._sessions.values()
            if session.user_id == user_id and session.level_id == level_id
        ]
        return min(candidates, key=lambda row: row.started_at, default=None)

    def _view_session(
        self,
        session: SessionRecord,
        stats: StatsRecord | None,
    ) -> SessionRecord:
        return replace(
            session,
            first_solved_at=stats.first_solved_at if stats else None,
        )

    def _create_session(
        self,
        *,
        user_id: str,
        level_id: str,
        client_session_key: str | None,
        is_replay: bool,
        now: datetime,
        stats: StatsRecord | None,
    ) -> SessionRecord:
        first = self._first_session(user_id, level_id)
        session = SessionRecord(
            session_id=str(uuid4()),
            user_id=user_id,
            level_id=level_id,
            status="active",
            is_replay=is_replay,
            replay_of=first.session_id if first else None,
            started_at=now,
            last_activity_at=now,
            finished_at=None,
            solved_at=None,
            attempts_count=0,
        )
        self._sessions[session.session_id] = session
        if (
            is_replay
            and stats is not None
            and stats.first_solved_at is not None
        ):
            updated = replace(
                stats,
                replay_count=stats.replay_count + 1,
                last_played_at=now,
            )
            self._stats[(user_id, level_id)] = updated
        return session

    def _ensure_active_session(
        self,
        *,
        user_id: str,
        level_id: str,
        client_session_key: str | None,
        now: datetime,
        stats: StatsRecord | None,
    ) -> SessionRecord:
        active = self._active_session(user_id, level_id)
        if active is not None:
            return active
        is_replay = bool(stats and stats.first_solved_at is not None)
        return self._create_session(
            user_id=user_id,
            level_id=level_id,
            client_session_key=client_session_key,
            is_replay=is_replay,
            now=now,
            stats=stats,
        )

    async def get_or_create_session(
        self,
        *,
        user_id: str,
        level_id: str,
        client_session_key: str | None = None,
    ) -> SessionRecord:
        now = _now()
        stats = self._stats.get((user_id, level_id))
        session = self._ensure_active_session(
            user_id=user_id,
            level_id=level_id,
            client_session_key=client_session_key,
            now=now,
            stats=stats,
        )
        stats = self._stats.get((user_id, level_id))
        return self._view_session(session, stats)

    async def restart_session(
        self,
        *,
        user_id: str,
        level_id: str,
        client_session_key: str | None = None,
    ) -> SessionRecord:
        now = _now()
        active = self._active_session(user_id, level_id)
        if active is not None:
            closed = replace(
                active,
                status="abandoned",
                finished_at=now,
                last_activity_at=now,
            )
            self._sessions[closed.session_id] = closed

        stats = self._stats.get((user_id, level_id))
        first = self._first_session(user_id, level_id)
        is_replay = first is not None
        session = self._create_session(
            user_id=user_id,
            level_id=level_id,
            client_session_key=client_session_key,
            is_replay=is_replay,
            now=now,
            stats=stats,
        )
        stats = self._stats.get((user_id, level_id))
        return self._view_session(session, stats)

    async def get_state(
        self,
        *,
        user_id: str,
        level_id: str,
    ) -> tuple[SessionRecord | None, WorkspaceRecord, StatsRecord | None]:
        stats = self._stats.get((user_id, level_id))
        active = self._active_session(user_id, level_id)
        session = self._view_session(active, stats) if active else None
        workspace = await self.get_workspace(
            user_id=user_id,
            level_id=level_id,
        )
        return session, workspace, stats

    # -- Intentos --------------------------------------------------------
    def _existing_attempt(
        self,
        user_id: str,
        client_attempt_id: str,
    ) -> AttemptRecord | None:
        attempt_id = self._attempt_keys.get((user_id, client_attempt_id))
        return self._attempts.get(attempt_id) if attempt_id else None

    async def record_attempt(
        self,
        *,
        user_id: str,
        level_id: str,
        client_attempt_id: str,
        session_key: str | None,
        solved: bool,
        selected_card_ids: list[str],
        elapsed_ms: int,
    ) -> AttemptResult:
        existing = self._existing_attempt(user_id, client_attempt_id)
        if existing is not None:
            stats = self._stats.get((user_id, level_id))
            session = self._sessions[existing.session_id]
            return AttemptResult(
                recorded=False,
                idempotent=True,
                session_id=existing.session_id,
                attempt_number=existing.attempt_number,
                first_solve=existing.first_solve,
                is_replay=session.is_replay,
                first_solve_attempts=(
                    stats.first_solve_attempts if stats else None
                ),
            )

        now = _now()
        stats = self._stats.get((user_id, level_id))
        session = self._ensure_active_session(
            user_id=user_id,
            level_id=level_id,
            client_session_key=session_key,
            now=now,
            stats=stats,
        )
        stats = self._stats.get((user_id, level_id))
        if stats is None:
            stats = StatsRecord(
                user_id=user_id,
                level_id=level_id,
                first_solved_at=None,
                first_solve_attempts=None,
                total_attempts=0,
                total_solves=0,
                replay_count=0,
                last_played_at=now,
            )

        attempt_number = session.attempts_count + 1
        session = replace(
            session,
            attempts_count=attempt_number,
            last_activity_at=now,
        )
        stats = replace(
            stats,
            total_attempts=stats.total_attempts + 1,
            last_played_at=now,
        )
        first_solve = False
        if solved:
            stats = replace(
                stats,
                total_solves=stats.total_solves + 1,
            )
            if stats.first_solved_at is None:
                stats = replace(
                    stats,
                    first_solved_at=now,
                    first_solve_attempts=stats.total_attempts,
                )
                first_solve = True
            session = replace(
                session,
                status="solved",
                finished_at=now,
                solved_at=now,
            )

        self._sessions[session.session_id] = session
        self._stats[(user_id, level_id)] = stats
        attempt = AttemptRecord(
            attempt_id=str(uuid4()),
            client_attempt_id=client_attempt_id,
            session_id=session.session_id,
            user_id=user_id,
            level_id=level_id,
            attempted_at=now,
            solved=solved,
            selected_card_ids=list(selected_card_ids),
            elapsed_ms=elapsed_ms,
            first_solve=first_solve,
            attempt_number=attempt_number,
        )
        self._attempts[attempt.attempt_id] = attempt
        self._attempt_keys[(user_id, client_attempt_id)] = attempt.attempt_id
        return AttemptResult(
            recorded=True,
            idempotent=False,
            session_id=session.session_id,
            attempt_number=attempt_number,
            first_solve=first_solve,
            is_replay=session.is_replay,
            first_solve_attempts=stats.first_solve_attempts,
        )

    # -- Estadísticas ----------------------------------------------------
    async def summary(self, *, user_id: str) -> SummaryRecord:
        stats = [
            row for (owner, _), row in self._stats.items() if owner == user_id
        ]
        solved_at = [
            session.solved_at
            for session in self._sessions.values()
            if session.user_id == user_id and session.solved_at is not None
        ]
        return _summary_from_stats(
            stats,
            max(solved_at) if solved_at else None,
        )

    async def list_cases(
        self,
        *,
        user_id: str,
        status_filter: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[StatsRecord], int]:
        rows = [
            row for (owner, _), row in self._stats.items() if owner == user_id
        ]
        if status_filter == "solved":
            rows = [row for row in rows if row.first_solved_at is not None]
        elif status_filter == "unsolved":
            rows = [row for row in rows if row.first_solved_at is None]
        elif status_filter == "replay":
            rows = [row for row in rows if row.replay_count > 0]
        rows.sort(key=lambda row: row.last_played_at, reverse=True)
        return rows[offset : offset + limit], len(rows)

    async def get_case_detail(
        self,
        *,
        user_id: str,
        level_id: str,
    ) -> CaseDetail:
        stats = self._stats.get((user_id, level_id))
        sessions = [
            self._view_session(session, stats)
            for session in self._sessions.values()
            if session.user_id == user_id and session.level_id == level_id
        ]
        sessions.sort(key=lambda row: row.started_at, reverse=True)
        attempts = [
            attempt
            for attempt in self._attempts.values()
            if attempt.user_id == user_id and attempt.level_id == level_id
        ]
        attempts.sort(key=lambda row: row.attempted_at, reverse=True)
        return CaseDetail(stats=stats, sessions=sessions, attempts=attempts)

    async def purge_user(self, *, user_id: str) -> dict[str, int]:
        keys = [key for key in self._notes if key[0] == user_id]
        for key in keys:
            del self._notes[key]
        workspace_keys = [
            key for key in self._workspaces if key[0] == user_id
        ]
        for key in workspace_keys:
            del self._workspaces[key]
        attempt_ids = [
            key
            for key, attempt in self._attempts.items()
            if attempt.user_id == user_id
        ]
        for attempt_id in attempt_ids:
            del self._attempts[attempt_id]
        attempt_keys = [
            key for key in self._attempt_keys if key[0] == user_id
        ]
        for key in attempt_keys:
            del self._attempt_keys[key]
        session_ids = [
            key
            for key, session in self._sessions.items()
            if session.user_id == user_id
        ]
        for session_id in session_ids:
            del self._sessions[session_id]
        stats_keys = [key for key in self._stats if key[0] == user_id]
        for key in stats_keys:
            del self._stats[key]
        return {
            "notes": len(keys),
            "workspaces": len(workspace_keys),
            "attempts": len(attempt_ids),
            "sessions": len(session_ids),
            "stats": len(stats_keys),
        }


def _note_from_row(row: CaseNoteModel | None) -> NoteRecord | None:
    if row is None:
        return None
    return NoteRecord(
        user_id=row.user_id,
        level_key=row.level_key,
        content=row.content,
        version=row.version,
        updated_at=row.updated_at,
    )


def _workspace_from_row(row: CaseWorkspaceModel | None) -> WorkspaceRecord:
    if row is None:
        return _empty_workspace("", "")
    return WorkspaceRecord(
        user_id=row.user_id,
        level_id=row.level_id,
        crossed_out_ids=list(row.crossed_out_ids or []),
        accusation_ids=list(row.accusation_ids or []),
        version=row.version,
        updated_at=row.updated_at,
    )


def _session_from_row(
    row: CaseSessionModel,
    first_solved_at: datetime | None,
) -> SessionRecord:
    return SessionRecord(
        session_id=row.session_id,
        user_id=row.user_id,
        level_id=row.level_id,
        status=row.status,
        is_replay=row.is_replay,
        replay_of=row.replay_of,
        started_at=row.started_at,
        last_activity_at=row.last_activity_at,
        finished_at=row.finished_at,
        solved_at=row.solved_at,
        attempts_count=row.attempts_count,
        first_solved_at=first_solved_at,
    )


def _stats_from_row(row: UserLevelStatsModel) -> StatsRecord:
    return StatsRecord(
        user_id=row.user_id,
        level_id=row.level_id,
        first_solved_at=row.first_solved_at,
        first_solve_attempts=row.first_solve_attempts,
        total_attempts=row.total_attempts,
        total_solves=row.total_solves,
        replay_count=row.replay_count,
        last_played_at=row.last_played_at,
    )


def _attempt_from_row(row: CaseAttemptModel) -> AttemptRecord:
    return AttemptRecord(
        attempt_id=row.attempt_id,
        client_attempt_id=row.client_attempt_id,
        session_id=row.session_id,
        user_id=row.user_id,
        level_id=row.level_id,
        attempted_at=row.attempted_at,
        solved=row.solved,
        selected_card_ids=list(row.selected_card_ids or []),
        elapsed_ms=row.elapsed_ms,
        first_solve=row.first_solve,
        attempt_number=row.attempt_number,
    )


def _attempt_result(
    attempt: AttemptRecord,
    session: CaseSessionModel | None,
    stats: UserLevelStatsModel | None,
    *,
    recorded: bool,
) -> AttemptResult:
    return AttemptResult(
        recorded=recorded,
        idempotent=not recorded,
        session_id=attempt.session_id,
        attempt_number=attempt.attempt_number,
        first_solve=attempt.first_solve,
        is_replay=session.is_replay if session else False,
        first_solve_attempts=stats.first_solve_attempts if stats else None,
    )


class SqlProgressRepository:
    """Progreso persistido en PostgreSQL."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    # -- Libreta ---------------------------------------------------------
    async def get_note(
        self,
        *,
        user_id: str,
        level_key: str,
    ) -> NoteRecord | None:
        async with self._session_factory() as db:
            row = await db.get(CaseNoteModel, (user_id, level_key))
            return _note_from_row(row)

    async def upsert_note(
        self,
        *,
        user_id: str,
        level_key: str,
        content: str,
        base_version: int | None,
    ) -> NoteRecord:
        for attempt in range(2):
            try:
                return await self._upsert_note_once(
                    user_id=user_id,
                    level_key=level_key,
                    content=content,
                    base_version=base_version,
                )
            except IntegrityError:
                if attempt == 1:
                    raise
        raise RuntimeError("No se pudo guardar la libreta")

    async def _upsert_note_once(
        self,
        *,
        user_id: str,
        level_key: str,
        content: str,
        base_version: int | None,
    ) -> NoteRecord:
        async with self._session_factory() as db:
            async with db.begin():
                row = await db.get(
                    CaseNoteModel,
                    (user_id, level_key),
                    with_for_update=True,
                )
                current_version = row.version if row else 0
                if (
                    base_version is not None
                    and base_version != current_version
                ):
                    raise NoteConflictError(_note_from_row(row))
                now = _now()
                if row is None:
                    row = CaseNoteModel(
                        user_id=user_id,
                        level_key=level_key,
                        content=content,
                        version=1,
                        updated_at=now,
                    )
                    db.add(row)
                else:
                    row.content = content
                    row.version += 1
                    row.updated_at = now
            return _note_from_row(row)  # type: ignore[return-value]

    # -- Espacio de trabajo ---------------------------------------------
    async def get_workspace(
        self,
        *,
        user_id: str,
        level_id: str,
    ) -> WorkspaceRecord:
        async with self._session_factory() as db:
            row = await db.get(CaseWorkspaceModel, (user_id, level_id))
            if row is None:
                return _empty_workspace(user_id, level_id)
            return _workspace_from_row(row)

    async def upsert_workspace(
        self,
        *,
        user_id: str,
        level_id: str,
        crossed_out_ids: list[str],
        accusation_ids: list[str],
        base_version: int | None,
    ) -> WorkspaceRecord:
        for attempt in range(2):
            try:
                return await self._upsert_workspace_once(
                    user_id=user_id,
                    level_id=level_id,
                    crossed_out_ids=crossed_out_ids,
                    accusation_ids=accusation_ids,
                    base_version=base_version,
                )
            except IntegrityError:
                if attempt == 1:
                    raise
        raise RuntimeError("No se pudo guardar el espacio de trabajo")

    async def _upsert_workspace_once(
        self,
        *,
        user_id: str,
        level_id: str,
        crossed_out_ids: list[str],
        accusation_ids: list[str],
        base_version: int | None,
    ) -> WorkspaceRecord:
        async with self._session_factory() as db:
            async with db.begin():
                row = await db.get(
                    CaseWorkspaceModel,
                    (user_id, level_id),
                    with_for_update=True,
                )
                current_version = row.version if row else 0
                if (
                    base_version is not None
                    and base_version != current_version
                ):
                    raise WorkspaceConflictError(
                        _workspace_from_row(row)
                        if row
                        else _empty_workspace(user_id, level_id)
                    )
                now = _now()
                values = {
                    "crossed_out_ids": _dedupe(crossed_out_ids),
                    "accusation_ids": _dedupe(accusation_ids),
                }
                if row is None:
                    row = CaseWorkspaceModel(
                        user_id=user_id,
                        level_id=level_id,
                        version=1,
                        updated_at=now,
                        **values,
                    )
                    db.add(row)
                else:
                    row.crossed_out_ids = values["crossed_out_ids"]
                    row.accusation_ids = values["accusation_ids"]
                    row.version += 1
                    row.updated_at = now
            return _workspace_from_row(row)

    # -- Sesiones --------------------------------------------------------
    async def _create_session(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        level_id: str,
        client_session_key: str | None,
        now: datetime,
        stats: UserLevelStatsModel | None,
        is_replay: bool,
    ) -> CaseSessionModel:
        first_id = await db.scalar(
            select(CaseSessionModel.session_id)
            .where(
                CaseSessionModel.user_id == user_id,
                CaseSessionModel.level_id == level_id,
            )
            .order_by(CaseSessionModel.started_at)
            .limit(1)
        )
        session = CaseSessionModel(
            session_id=str(uuid4()),
            user_id=user_id,
            level_id=level_id,
            status="active",
            is_replay=is_replay,
            replay_of=first_id,
            client_session_key=client_session_key,
            started_at=now,
            last_activity_at=now,
            attempts_count=0,
        )
        db.add(session)
        if (
            is_replay
            and stats is not None
            and stats.first_solved_at is not None
        ):
            stats.replay_count += 1
            stats.last_played_at = now
        return session

    async def get_or_create_session(
        self,
        *,
        user_id: str,
        level_id: str,
        client_session_key: str | None = None,
    ) -> SessionRecord:
        for attempt in range(2):
            try:
                return await self._get_or_create_session_once(
                    user_id=user_id,
                    level_id=level_id,
                    client_session_key=client_session_key,
                )
            except IntegrityError:
                if attempt == 1:
                    raise
        raise RuntimeError("No se pudo abrir la partida")

    async def _get_or_create_session_once(
        self,
        *,
        user_id: str,
        level_id: str,
        client_session_key: str | None,
    ) -> SessionRecord:
        async with self._session_factory() as db:
            async with db.begin():
                stats = await db.get(
                    UserLevelStatsModel,
                    (user_id, level_id),
                    with_for_update=True,
                )
                session = await db.scalar(
                    select(CaseSessionModel)
                    .where(
                        CaseSessionModel.user_id == user_id,
                        CaseSessionModel.level_id == level_id,
                        CaseSessionModel.status == "active",
                    )
                    .with_for_update()
                )
                if session is None:
                    is_replay = bool(
                        stats and stats.first_solved_at is not None
                    )
                    session = await self._create_session(
                        db,
                        user_id=user_id,
                        level_id=level_id,
                        client_session_key=client_session_key,
                        now=_now(),
                        stats=stats,
                        is_replay=is_replay,
                    )
            return _session_from_row(
                session,
                stats.first_solved_at if stats else None,
            )

    async def restart_session(
        self,
        *,
        user_id: str,
        level_id: str,
        client_session_key: str | None = None,
    ) -> SessionRecord:
        for attempt in range(2):
            try:
                return await self._restart_session_once(
                    user_id=user_id,
                    level_id=level_id,
                    client_session_key=client_session_key,
                )
            except IntegrityError:
                if attempt == 1:
                    raise
        raise RuntimeError("No se pudo reiniciar la partida")

    async def _restart_session_once(
        self,
        *,
        user_id: str,
        level_id: str,
        client_session_key: str | None,
    ) -> SessionRecord:
        async with self._session_factory() as db:
            async with db.begin():
                now = _now()
                stats = await db.get(
                    UserLevelStatsModel,
                    (user_id, level_id),
                    with_for_update=True,
                )
                active = await db.scalar(
                    select(CaseSessionModel)
                    .where(
                        CaseSessionModel.user_id == user_id,
                        CaseSessionModel.level_id == level_id,
                        CaseSessionModel.status == "active",
                    )
                    .with_for_update()
                )
                if active is not None:
                    active.status = "abandoned"
                    active.finished_at = now
                    active.last_activity_at = now
                first_id = await db.scalar(
                    select(CaseSessionModel.session_id)
                    .where(
                        CaseSessionModel.user_id == user_id,
                        CaseSessionModel.level_id == level_id,
                    )
                    .order_by(CaseSessionModel.started_at)
                    .limit(1)
                )
                session = await self._create_session(
                    db,
                    user_id=user_id,
                    level_id=level_id,
                    client_session_key=client_session_key,
                    now=now,
                    stats=stats,
                    is_replay=first_id is not None,
                )
            return _session_from_row(
                session,
                stats.first_solved_at if stats else None,
            )

    async def get_state(
        self,
        *,
        user_id: str,
        level_id: str,
    ) -> tuple[SessionRecord | None, WorkspaceRecord, StatsRecord | None]:
        async with self._session_factory() as db:
            stats = await db.get(UserLevelStatsModel, (user_id, level_id))
            session = await db.scalar(
                select(CaseSessionModel).where(
                    CaseSessionModel.user_id == user_id,
                    CaseSessionModel.level_id == level_id,
                    CaseSessionModel.status == "active",
                )
            )
            workspace = await db.get(
                CaseWorkspaceModel,
                (user_id, level_id),
            )
            return (
                _session_from_row(
                    session,
                    stats.first_solved_at if stats else None,
                )
                if session
                else None,
                _workspace_from_row(workspace)
                if workspace
                else _empty_workspace(user_id, level_id),
                _stats_from_row(stats) if stats else None,
            )

    # -- Intentos --------------------------------------------------------
    async def record_attempt(
        self,
        *,
        user_id: str,
        level_id: str,
        client_attempt_id: str,
        session_key: str | None,
        solved: bool,
        selected_card_ids: list[str],
        elapsed_ms: int,
    ) -> AttemptResult:
        for attempt in range(2):
            try:
                return await self._record_attempt_once(
                    user_id=user_id,
                    level_id=level_id,
                    client_attempt_id=client_attempt_id,
                    session_key=session_key,
                    solved=solved,
                    selected_card_ids=selected_card_ids,
                    elapsed_ms=elapsed_ms,
                )
            except IntegrityError:
                if attempt == 1:
                    raise
        raise RuntimeError("No se pudo registrar el intento")

    async def _record_attempt_once(
        self,
        *,
        user_id: str,
        level_id: str,
        client_attempt_id: str,
        session_key: str | None,
        solved: bool,
        selected_card_ids: list[str],
        elapsed_ms: int,
    ) -> AttemptResult:
        async with self._session_factory() as db:
            async with db.begin():
                existing = await db.scalar(
                    select(CaseAttemptModel).where(
                        CaseAttemptModel.user_id == user_id,
                        CaseAttemptModel.client_attempt_id
                        == client_attempt_id,
                    )
                )
                if existing is not None:
                    session = await db.get(
                        CaseSessionModel,
                        existing.session_id,
                    )
                    stats = await db.get(
                        UserLevelStatsModel,
                        (user_id, level_id),
                    )
                    return _attempt_result(
                        _attempt_from_row(existing),
                        session,
                        stats,
                        recorded=False,
                    )

                now = _now()
                stats = await db.get(
                    UserLevelStatsModel,
                    (user_id, level_id),
                    with_for_update=True,
                )
                if stats is None:
                    stats = UserLevelStatsModel(
                        user_id=user_id,
                        level_id=level_id,
                        total_attempts=0,
                        total_solves=0,
                        replay_count=0,
                        last_played_at=now,
                    )
                    db.add(stats)
                    await db.flush()

                session = await db.scalar(
                    select(CaseSessionModel)
                    .where(
                        CaseSessionModel.user_id == user_id,
                        CaseSessionModel.level_id == level_id,
                        CaseSessionModel.status == "active",
                    )
                    .with_for_update()
                )
                if session is None:
                    session = await self._create_session(
                        db,
                        user_id=user_id,
                        level_id=level_id,
                        client_session_key=session_key,
                        now=now,
                        stats=stats,
                        is_replay=stats.first_solved_at is not None,
                    )

                session.attempts_count += 1
                session.last_activity_at = now
                stats.total_attempts += 1
                stats.last_played_at = now
                first_solve = False
                if solved:
                    stats.total_solves += 1
                    if stats.first_solved_at is None:
                        stats.first_solved_at = now
                        stats.first_solve_attempts = stats.total_attempts
                        first_solve = True
                    session.status = "solved"
                    session.finished_at = now
                    session.solved_at = now

                attempt = CaseAttemptModel(
                    attempt_id=str(uuid4()),
                    client_attempt_id=client_attempt_id,
                    session_id=session.session_id,
                    user_id=user_id,
                    level_id=level_id,
                    attempted_at=now,
                    solved=solved,
                    selected_card_ids=list(selected_card_ids),
                    elapsed_ms=elapsed_ms,
                    attempt_number=session.attempts_count,
                    first_solve=first_solve,
                )
                db.add(attempt)
            return AttemptResult(
                recorded=True,
                idempotent=False,
                session_id=session.session_id,
                attempt_number=session.attempts_count,
                first_solve=first_solve,
                is_replay=session.is_replay,
                first_solve_attempts=stats.first_solve_attempts,
            )

    # -- Estadísticas ----------------------------------------------------
    async def summary(self, *, user_id: str) -> SummaryRecord:
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(UserLevelStatsModel).where(
                        UserLevelStatsModel.user_id == user_id
                    )
                )
            ).all()
            last_solved = await db.scalar(
                select(func.max(CaseSessionModel.solved_at)).where(
                    CaseSessionModel.user_id == user_id
                )
            )
        return _summary_from_stats(
            [_stats_from_row(row) for row in rows],
            last_solved,
        )

    async def list_cases(
        self,
        *,
        user_id: str,
        status_filter: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[StatsRecord], int]:
        statement = select(UserLevelStatsModel).where(
            UserLevelStatsModel.user_id == user_id
        )
        if status_filter == "solved":
            statement = statement.where(
                UserLevelStatsModel.first_solved_at.is_not(None)
            )
        elif status_filter == "unsolved":
            statement = statement.where(
                UserLevelStatsModel.first_solved_at.is_(None)
            )
        elif status_filter == "replay":
            statement = statement.where(
                UserLevelStatsModel.replay_count > 0
            )
        count_statement = select(func.count()).select_from(
            statement.subquery()
        )
        async with self._session_factory() as db:
            total = await db.scalar(count_statement) or 0
            rows = (
                await db.scalars(
                    statement.order_by(
                        UserLevelStatsModel.last_played_at.desc()
                    )
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        return [_stats_from_row(row) for row in rows], int(total)

    async def get_case_detail(
        self,
        *,
        user_id: str,
        level_id: str,
    ) -> CaseDetail:
        async with self._session_factory() as db:
            stats = await db.get(
                UserLevelStatsModel,
                (user_id, level_id),
            )
            sessions = (
                await db.scalars(
                    select(CaseSessionModel)
                    .where(
                        CaseSessionModel.user_id == user_id,
                        CaseSessionModel.level_id == level_id,
                    )
                    .order_by(CaseSessionModel.started_at.desc())
                )
            ).all()
            attempts = (
                await db.scalars(
                    select(CaseAttemptModel)
                    .where(
                        CaseAttemptModel.user_id == user_id,
                        CaseAttemptModel.level_id == level_id,
                    )
                    .order_by(CaseAttemptModel.attempted_at.desc())
                )
            ).all()
        first_solved_at = stats.first_solved_at if stats else None
        return CaseDetail(
            stats=_stats_from_row(stats) if stats else None,
            sessions=[
                _session_from_row(row, first_solved_at) for row in sessions
            ],
            attempts=[_attempt_from_row(row) for row in attempts],
        )

    async def purge_user(self, *, user_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        async with self._session_factory() as db, db.begin():
            attempts = await db.execute(
                delete(CaseAttemptModel).where(
                    CaseAttemptModel.user_id == user_id
                )
            )
            counts["attempts"] = attempts.rowcount or 0
            sessions = await db.execute(
                delete(CaseSessionModel).where(
                    CaseSessionModel.user_id == user_id
                )
            )
            counts["sessions"] = sessions.rowcount or 0
            stats = await db.execute(
                delete(UserLevelStatsModel).where(
                    UserLevelStatsModel.user_id == user_id
                )
            )
            counts["stats"] = stats.rowcount or 0
            notes = await db.execute(
                delete(CaseNoteModel).where(
                    CaseNoteModel.user_id == user_id
                )
            )
            counts["notes"] = notes.rowcount or 0
            workspaces = await db.execute(
                delete(CaseWorkspaceModel).where(
                    CaseWorkspaceModel.user_id == user_id
                )
            )
            counts["workspaces"] = workspaces.rowcount or 0
        return counts
