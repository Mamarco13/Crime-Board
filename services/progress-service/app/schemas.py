"""Contratos HTTP del Progress Service.

Los esquemas públicos cubren libreta, espacio de trabajo, sesiones e
historial. Los internos registran intentos de acusación enviados por el
Playground Engine y purgan los datos de una cuenta.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NoteUpdate(BaseModel):
    """Payload de ``PUT /api/progress/notes/{scope}``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    content: str = Field(max_length=20_000)
    base_version: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Versión que el cliente cree tener. Si no coincide, la respuesta "
            "es 409 y devuelve el contenido actual."
        ),
    )


class NoteResponse(BaseModel):
    """Libreta devuelta al cliente. ``version=0`` significa que aún no existe."""

    scope: str
    content: str
    version: int = Field(ge=0)
    updated_at: datetime | None = None


class WorkspaceUpdate(BaseModel):
    """Tachados y selección de acusación de un caso."""

    model_config = ConfigDict(extra="forbid")

    crossed_out_ids: list[str] = Field(default_factory=list, max_length=200)
    accusation_ids: list[str] = Field(default_factory=list, max_length=200)
    base_version: int | None = Field(
        default=None,
        ge=0,
        description="Versión esperada del espacio de trabajo.",
    )


class WorkspaceResponse(BaseModel):
    """Estado del tablero con control de versión optimista."""

    crossed_out_ids: list[str]
    accusation_ids: list[str]
    version: int = Field(ge=0)
    updated_at: datetime | None = None


class SessionStartRequest(BaseModel):
    """Cuerpo opcional para identificar la sesión del navegador."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Identificador local del navegador, solo para trazabilidad.",
    )


class RestartRequest(BaseModel):
    """Payload de ``POST /api/progress/cases/{level_id}/restart``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Identificador local del navegador que pide el reinicio.",
    )
    reason: str | None = Field(default=None, max_length=64)


class SessionResponse(BaseModel):
    """Partida activa o recién creada."""

    session_id: str
    level_id: str
    status: str
    is_replay: bool
    started_at: datetime
    last_activity_at: datetime
    attempts_count: int = Field(ge=0)
    first_solved_at: datetime | None = None


class LevelStatsResponse(BaseModel):
    """Resumen materializado de un usuario en un nivel."""

    level_id: str
    first_solved_at: datetime | None = None
    first_solve_attempts: int | None = Field(default=None, ge=1)
    total_attempts: int = Field(ge=0)
    total_solves: int = Field(ge=0)
    replay_count: int = Field(ge=0)
    last_played_at: datetime


class CaseStateResponse(BaseModel):
    """Todo lo que el tablero necesita para reanudar una partida."""

    session: SessionResponse | None = None
    workspace: WorkspaceResponse
    stats: LevelStatsResponse | None = None


class SummaryResponse(BaseModel):
    """KPIs globales del perfil."""

    cases_played: int = Field(ge=0)
    cases_solved: int = Field(ge=0)
    solve_rate: float = Field(ge=0.0, le=1.0)
    first_solve_attempts_total: int = Field(ge=0)
    average_attempts_to_first_solve: float = Field(ge=0.0)
    total_attempts: int = Field(ge=0)
    replay_count: int = Field(ge=0)
    last_solved_at: datetime | None = None


class CaseHistoryResponse(BaseModel):
    """Página de historial de casos."""

    items: list[LevelStatsResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class AttemptDetailResponse(BaseModel):
    """Intento de acusación visible en el detalle de un caso."""

    attempt_id: str
    attempted_at: datetime
    solved: bool
    selected_card_ids: list[str]
    elapsed_ms: int = Field(ge=0)
    first_solve: bool


class CaseDetailResponse(BaseModel):
    """Sesiones e intentos de un nivel concreto."""

    stats: LevelStatsResponse | None = None
    sessions: list[SessionResponse]
    attempts: list[AttemptDetailResponse]


class AttemptCreate(BaseModel):
    """Payload interno de ``POST /api/internal/progress/attempts``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_id: str = Field(min_length=1, max_length=64)
    level_id: str = Field(min_length=2, max_length=64)
    session_key: str | None = Field(default=None, max_length=64)
    client_attempt_id: str = Field(min_length=1, max_length=64)
    solved: bool
    selected_card_ids: list[str] = Field(min_length=1, max_length=200)
    elapsed_ms: int = Field(ge=0)


class AttemptRecordResponse(BaseModel):
    """Resultado del registro interno de un intento."""

    recorded: bool
    session_id: str
    attempt_number: int = Field(ge=1)
    first_solve: bool
    is_replay: bool
    first_solve_attempts: int | None = Field(default=None, ge=1)


class PurgeResponse(BaseModel):
    """Conteos eliminados al purgar una cuenta (idempotente)."""

    deleted: dict[str, int]
