"""Progress Service — libreta, sesiones, espacio de trabajo y estadísticas.

Endpoints públicos (JWT obligatorio):

* Libreta general y por caso con versión optimista.
* Inicio, reanudación y reinicio de partidas.
* Tachados y selección de acusación del tablero.
* Resumen e historial del perfil.

Endpoints internos (clave compartida):

* Registro idempotente de intentos enviado por Playground Engine.
* Purga de todos los datos de una cuenta desde User Service.

La persistencia es PostgreSQL cuando ``DATABASE_URL`` está definida. Sin esa
variable el servicio arranca con un repositorio en memoria para desarrollo y
pruebas manuales.
"""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncEngine

from .config import Settings
from .database import init_database
from .repository import (
    GENERAL_SCOPE,
    CaseDetail,
    InMemoryProgressRepository,
    NoteConflictError,
    NoteRecord,
    ProgressRepository,
    SessionRecord,
    SqlProgressRepository,
    StatsRecord,
    SummaryRecord,
    WorkspaceConflictError,
    WorkspaceRecord,
)
from .schemas import (
    AttemptCreate,
    AttemptDetailResponse,
    AttemptRecordResponse,
    CaseDetailResponse,
    CaseHistoryResponse,
    CaseStateResponse,
    LevelStatsResponse,
    NoteResponse,
    NoteUpdate,
    PurgeResponse,
    RestartRequest,
    SessionResponse,
    SessionStartRequest,
    SummaryResponse,
    WorkspaceResponse,
    WorkspaceUpdate,
)
from .security import (
    AuthenticatedUser,
    decode_access_token,
    internal_key_matches,
    user_from_payload,
)

logger = logging.getLogger(__name__)

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
CARD_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_SCOPE_LENGTH = 80
MAX_CARDS = 200
HISTORY_STATUSES = {"solved", "unsolved", "replay"}


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    app.state.settings = settings
    engine: AsyncEngine | None = None

    try:
        if settings.database_url:
            engine, session_factory = await init_database(settings.database_url)
            repository: ProgressRepository = SqlProgressRepository(
                session_factory
            )
            logger.info("Progreso persistido en PostgreSQL")
        else:
            repository = InMemoryProgressRepository()
            logger.warning(
                "DATABASE_URL no definida: progreso en memoria "
                "(se pierde al reiniciar)"
            )

        app.state.engine = engine
        app.state.repository = repository

        if settings.jwt_secret_key is None:
            logger.warning(
                "JWT_SECRET_KEY/APP_SECRET_KEY no definida: los endpoints "
                "públicos de progreso quedarán deshabilitados"
            )
        if settings.internal_api_key is None:
            logger.warning(
                "INTERNAL_API_KEY/APP_SECRET_KEY no definida: los endpoints "
                "internos quedan sin autenticación (solo desarrollo)"
            )

        yield
    finally:
        if engine is not None:
            await engine.dispose()


app = FastAPI(
    title="Crime Board — Progress Service",
    description=(
        "Libreta privada, espacio de trabajo, sesiones, intentos y "
        "estadísticas de Crime Board."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(Settings.from_env().cors_allow_origins),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Dependencias
# ---------------------------------------------------------------------------
def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_repository(request: Request) -> ProgressRepository:
    return request.app.state.repository


SettingsDep = Annotated[Settings, Depends(get_settings)]
RepositoryDep = Annotated[ProgressRepository, Depends(get_repository)]


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def require_user(
    settings: SettingsDep,
    authorization: Annotated[
        str | None,
        Header(description="JWT emitido por el User Service (prefijo Bearer)"),
    ] = None,
) -> AuthenticatedUser:
    """Exige una sesión válida; el progreso siempre es privado."""
    if settings.jwt_secret_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "La autenticación no está configurada en el servicio de "
                "progreso. Define JWT_SECRET_KEY/APP_SECRET_KEY."
            ),
        )

    token = _bearer_token(authorization)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Debes iniciar sesión para acceder a tu progreso",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(
        token,
        secret_key=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
    )
    user = user_from_payload(payload) if payload else None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión no válida o caducada. Vuelve a iniciar sesión.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUserDep = Annotated[AuthenticatedUser, Depends(require_user)]


async def require_internal_key(
    settings: SettingsDep,
    x_internal_api_key: Annotated[
        str | None,
        Header(
            alias="X-Internal-Api-Key",
            description="Clave compartida entre servicios",
        ),
    ] = None,
) -> None:
    """Protege los endpoints internos de intentos y purga."""
    if not internal_key_matches(
        x_internal_api_key,
        settings.internal_api_key,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clave interna no válida",
            headers={"WWW-Authenticate": "ApiKey"},
        )


InternalKeyDep = Annotated[None, Depends(require_internal_key)]


# ---------------------------------------------------------------------------
# Validaciones y conversores
# ---------------------------------------------------------------------------
def _validate_level_id(level_id: str) -> str:
    value = level_id.strip()
    if not SLUG_PATTERN.fullmatch(value):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="level_id no válido",
        )
    return value


def _normalise_scope(scope: str) -> tuple[str, str]:
    """Devuelve ``(scope público, level_key interno)``."""
    value = scope.strip()
    if value in {"general", GENERAL_SCOPE}:
        return "general", GENERAL_SCOPE
    if len(value) > MAX_SCOPE_LENGTH or not SLUG_PATTERN.fullmatch(value):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="scope no válido",
        )
    return value, value


def _normalise_card_ids(card_ids: list[str]) -> list[str]:
    if len(card_ids) > MAX_CARDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Como máximo se admiten {MAX_CARDS} tarjetas",
        )
    normalised: list[str] = []
    seen: set[str] = set()
    for raw in card_ids:
        card_id = raw.strip()
        if not CARD_ID_PATTERN.fullmatch(card_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Identificador de tarjeta no válido: {raw}",
            )
        if card_id not in seen:
            seen.add(card_id)
            normalised.append(card_id)
    return normalised


def _to_note(record: NoteRecord | None, scope: str) -> NoteResponse:
    if record is None:
        return NoteResponse(scope=scope, content="", version=0)
    return NoteResponse(
        scope=scope,
        content=record.content,
        version=record.version,
        updated_at=record.updated_at,
    )


def _to_workspace(record: WorkspaceRecord) -> WorkspaceResponse:
    return WorkspaceResponse(
        crossed_out_ids=list(record.crossed_out_ids),
        accusation_ids=list(record.accusation_ids),
        version=record.version,
        updated_at=record.updated_at,
    )


def _to_session(record: SessionRecord) -> SessionResponse:
    return SessionResponse(
        session_id=record.session_id,
        level_id=record.level_id,
        status=record.status,
        is_replay=record.is_replay,
        started_at=record.started_at,
        last_activity_at=record.last_activity_at,
        attempts_count=record.attempts_count,
        first_solved_at=record.first_solved_at,
    )


def _to_stats(record: StatsRecord) -> LevelStatsResponse:
    return LevelStatsResponse(
        level_id=record.level_id,
        first_solved_at=record.first_solved_at,
        first_solve_attempts=record.first_solve_attempts,
        total_attempts=record.total_attempts,
        total_solves=record.total_solves,
        replay_count=record.replay_count,
        last_played_at=record.last_played_at,
    )


def _to_attempt_detail(attempt) -> AttemptDetailResponse:
    return AttemptDetailResponse(
        attempt_id=attempt.attempt_id,
        attempted_at=attempt.attempted_at,
        solved=attempt.solved,
        selected_card_ids=list(attempt.selected_card_ids),
        elapsed_ms=attempt.elapsed_ms,
        first_solve=attempt.first_solve,
    )


def _to_summary(record: SummaryRecord) -> SummaryResponse:
    return SummaryResponse(
        cases_played=record.cases_played,
        cases_solved=record.cases_solved,
        solve_rate=round(record.solve_rate, 4),
        first_solve_attempts_total=record.first_solve_attempts_total,
        average_attempts_to_first_solve=round(
            record.average_attempts_to_first_solve,
            2,
        ),
        total_attempts=record.total_attempts,
        replay_count=record.replay_count,
        last_solved_at=record.last_solved_at,
    )


def _to_case_detail(detail: CaseDetail) -> CaseDetailResponse:
    return CaseDetailResponse(
        stats=_to_stats(detail.stats) if detail.stats else None,
        sessions=[_to_session(session) for session in detail.sessions],
        attempts=[
            _to_attempt_detail(attempt) for attempt in detail.attempts
        ],
    )


def _conflict_note(record: NoteRecord | None, scope: str) -> dict:
    return {
        "message": "La libreta ha cambiado en otro dispositivo",
        "current": _to_note(record, scope).model_dump(mode="json"),
    }


def _conflict_workspace(record: WorkspaceRecord) -> dict:
    return {
        "message": "El espacio de trabajo ha cambiado en otro dispositivo",
        "current": _to_workspace(record).model_dump(mode="json"),
    }


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------
@app.get("/health", tags=["ops"], summary="Sonda de salud")
async def health() -> dict[str, str]:
    """Liveness/readiness usada por Docker, Compose y el CI."""
    return {"status": "ok", "service": "progress-service"}


@app.get(
    "/api/internal/progress/health",
    tags=["internal"],
    summary="Sonda interna de salud",
    include_in_schema=False,
)
async def internal_health(_: InternalKeyDep) -> dict[str, str]:
    return {"status": "ok", "service": "progress-service"}


# ---------------------------------------------------------------------------
# Libreta
# ---------------------------------------------------------------------------
@app.get(
    "/api/progress/notes/{scope}",
    response_model=NoteResponse,
    tags=["notes"],
    summary="Lee la libreta general o la de un caso",
)
async def get_note(
    scope: str,
    repository: RepositoryDep,
    current_user: CurrentUserDep,
) -> NoteResponse:
    public_scope, level_key = _normalise_scope(scope)
    record = await repository.get_note(
        user_id=current_user.user_id,
        level_key=level_key,
    )
    return _to_note(record, public_scope)


@app.put(
    "/api/progress/notes/{scope}",
    response_model=NoteResponse,
    tags=["notes"],
    summary="Guarda la libreta con control de versión",
)
async def put_note(
    scope: str,
    payload: NoteUpdate,
    repository: RepositoryDep,
    current_user: CurrentUserDep,
) -> NoteResponse:
    public_scope, level_key = _normalise_scope(scope)
    try:
        record = await repository.upsert_note(
            user_id=current_user.user_id,
            level_key=level_key,
            content=payload.content,
            base_version=payload.base_version,
        )
    except NoteConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_conflict_note(error.current, public_scope),
        ) from error
    return _to_note(record, public_scope)


# ---------------------------------------------------------------------------
# Espacio de trabajo y sesiones
# ---------------------------------------------------------------------------
@app.post(
    "/api/progress/cases/{level_id}/sessions",
    response_model=SessionResponse,
    tags=["sessions"],
    summary="Inicia o reanuda la partida activa",
)
async def start_session(
    level_id: str,
    repository: RepositoryDep,
    current_user: CurrentUserDep,
    payload: SessionStartRequest | None = None,
) -> SessionResponse:
    validated = _validate_level_id(level_id)
    session = await repository.get_or_create_session(
        user_id=current_user.user_id,
        level_id=validated,
        client_session_key=payload.client_session_id if payload else None,
    )
    return _to_session(session)


@app.post(
    "/api/progress/cases/{level_id}/restart",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["sessions"],
    summary="Cierra la partida activa y abre una repetición",
)
async def restart_case(
    level_id: str,
    repository: RepositoryDep,
    current_user: CurrentUserDep,
    payload: RestartRequest | None = None,
) -> SessionResponse:
    validated = _validate_level_id(level_id)
    session = await repository.restart_session(
        user_id=current_user.user_id,
        level_id=validated,
        client_session_key=payload.session_id if payload else None,
    )
    return _to_session(session)


@app.get(
    "/api/progress/cases/{level_id}/state",
    response_model=CaseStateResponse,
    tags=["sessions"],
    summary="Sesión activa, workspace y resumen del nivel",
)
async def get_case_state(
    level_id: str,
    repository: RepositoryDep,
    current_user: CurrentUserDep,
) -> CaseStateResponse:
    validated = _validate_level_id(level_id)
    session, workspace, stats = await repository.get_state(
        user_id=current_user.user_id,
        level_id=validated,
    )
    return CaseStateResponse(
        session=_to_session(session) if session else None,
        workspace=_to_workspace(workspace),
        stats=_to_stats(stats) if stats else None,
    )


@app.put(
    "/api/progress/cases/{level_id}/workspace",
    response_model=WorkspaceResponse,
    tags=["sessions"],
    summary="Guarda tachados y selección de acusación",
)
async def put_workspace(
    level_id: str,
    payload: WorkspaceUpdate,
    repository: RepositoryDep,
    current_user: CurrentUserDep,
) -> WorkspaceResponse:
    validated = _validate_level_id(level_id)
    crossed_out = _normalise_card_ids(payload.crossed_out_ids)
    accusation = _normalise_card_ids(payload.accusation_ids)
    try:
        record = await repository.upsert_workspace(
            user_id=current_user.user_id,
            level_id=validated,
            crossed_out_ids=crossed_out,
            accusation_ids=accusation,
            base_version=payload.base_version,
        )
    except WorkspaceConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_conflict_workspace(error.current),
        ) from error
    return _to_workspace(record)


# ---------------------------------------------------------------------------
# Estadísticas e historial
# ---------------------------------------------------------------------------
@app.get(
    "/api/progress/me/summary",
    response_model=SummaryResponse,
    tags=["stats"],
    summary="KPIs globales del perfil",
)
async def get_summary(
    repository: RepositoryDep,
    current_user: CurrentUserDep,
) -> SummaryResponse:
    summary = await repository.summary(user_id=current_user.user_id)
    return _to_summary(summary)


@app.get(
    "/api/progress/me/cases",
    response_model=CaseHistoryResponse,
    tags=["stats"],
    summary="Historial paginado de casos",
)
async def get_case_history(
    repository: RepositoryDep,
    current_user: CurrentUserDep,
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="solved | unsolved | replay"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CaseHistoryResponse:
    if status_filter is not None and status_filter not in HISTORY_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status debe ser solved, unsolved o replay",
        )
    items, total = await repository.list_cases(
        user_id=current_user.user_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )
    return CaseHistoryResponse(
        items=[_to_stats(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/progress/me/cases/{level_id}",
    response_model=CaseDetailResponse,
    tags=["stats"],
    summary="Sesiones e intentos de un caso",
)
async def get_case_detail(
    level_id: str,
    repository: RepositoryDep,
    current_user: CurrentUserDep,
) -> CaseDetailResponse:
    validated = _validate_level_id(level_id)
    detail = await repository.get_case_detail(
        user_id=current_user.user_id,
        level_id=validated,
    )
    return _to_case_detail(detail)


# ---------------------------------------------------------------------------
# Endpoints internos
# ---------------------------------------------------------------------------
@app.post(
    "/api/internal/progress/attempts",
    response_model=AttemptRecordResponse,
    tags=["internal"],
    summary="Registra un intento de acusación",
    include_in_schema=False,
)
async def record_attempt(
    payload: AttemptCreate,
    response: Response,
    repository: RepositoryDep,
    _: InternalKeyDep,
) -> AttemptRecordResponse:
    level_id = _validate_level_id(payload.level_id)
    selected = _normalise_card_ids(payload.selected_card_ids)
    result = await repository.record_attempt(
        user_id=payload.user_id.strip(),
        level_id=level_id,
        client_attempt_id=payload.client_attempt_id.strip(),
        session_key=payload.session_key,
        solved=payload.solved,
        selected_card_ids=selected,
        elapsed_ms=payload.elapsed_ms,
    )
    response.status_code = (
        status.HTTP_201_CREATED
        if result.recorded
        else status.HTTP_200_OK
    )
    logger.info(
        "Intento %s: usuario=%s nivel=%s resuelto=%s primera=%s",
        "registrado" if result.recorded else "idempotente",
        payload.user_id,
        level_id,
        payload.solved,
        result.first_solve,
    )
    return AttemptRecordResponse(
        recorded=result.recorded,
        session_id=result.session_id,
        attempt_number=result.attempt_number,
        first_solve=result.first_solve,
        is_replay=result.is_replay,
        first_solve_attempts=result.first_solve_attempts,
    )


@app.delete(
    "/api/internal/progress/users/{user_id}",
    response_model=PurgeResponse,
    tags=["internal"],
    summary="Purga todos los datos de una cuenta",
    include_in_schema=False,
)
async def purge_user(
    user_id: str,
    repository: RepositoryDep,
    _: InternalKeyDep,
) -> PurgeResponse:
    cleaned = user_id.strip()
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="user_id no válido",
        )
    deleted = await repository.purge_user(user_id=cleaned)
    logger.info("Progreso purgado para el usuario %s: %s", cleaned, deleted)
    return PurgeResponse(deleted=deleted)
