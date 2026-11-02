"""Rating Service — valoraciones de 1 a 5 estrellas de los niveles.

Responsabilidades:

* Registrar el voto de un usuario autenticado. Si el usuario ya había votado
  ese nivel, su puntuación se actualiza (un voto por usuario y nivel).
* Exponer la media redondeada a un decimal y el número de votos de un nivel.
* Ofrecer una consulta por lotes para que la galería del frontend pinte todas
  las tarjetas con una sola petición.

Los endpoints de lectura son públicos (incluidos invitados). La escritura exige
un JWT válido emitido por el User Service: la identidad se extrae del token y
nunca se confía en el ``user_id`` del cuerpo.

La persistencia es PostgreSQL (SQLAlchemy asíncrono) cuando ``DATABASE_URL``
está definida. Sin esa variable el servicio arranca con un repositorio en
memoria, útil para desarrollo y pruebas manuales.
"""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncEngine

from .config import Settings
from .database import init_database
from .repository import (
    EMPTY_AGGREGATE,
    InMemoryRatingRepository,
    RatingAggregate,
    RatingRepository,
    SqlRatingRepository,
)
from .schemas import RatingBatchRequest, RatingCreate, RatingSummary
from .security import (
    AuthenticatedUser,
    decode_access_token,
    user_from_payload,
)

logger = logging.getLogger(__name__)

MAX_LEVEL_IDS = 100
MAX_LEVEL_ID_LENGTH = 64


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
            repository: RatingRepository = SqlRatingRepository(session_factory)
            logger.info("Valoraciones persistidas en PostgreSQL")
        else:
            repository = InMemoryRatingRepository()
            logger.warning(
                "DATABASE_URL no definida: valoraciones en memoria "
                "(se pierden al reiniciar)"
            )

        app.state.engine = engine
        app.state.repository = repository

        if settings.jwt_secret_key is None:
            logger.warning(
                "JWT_SECRET_KEY/APP_SECRET_KEY no definida: los votos "
                "autenticados quedarán deshabilitados"
            )

        yield
    finally:
        if engine is not None:
            await engine.dispose()


app = FastAPI(
    title="Crime Board — Rating Service",
    description=(
        "Valoraciones de 1 a 5 estrellas de los niveles. Expone medias y "
        "conteos públicos para las tarjetas de la galería."
    ),
    version="1.0.0",
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


def get_repository(request: Request) -> RatingRepository:
    return request.app.state.repository


SettingsDep = Annotated[Settings, Depends(get_settings)]
RepositoryDep = Annotated[RatingRepository, Depends(get_repository)]


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
    """Exige una sesión válida para registrar una valoración.

    Un invitado (sin cabecera Authorization) recibe 401; un token caducado o
    manipulado también se rechaza.
    """
    if settings.jwt_secret_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "La autenticación no está configurada en el servicio de "
                "valoraciones. Define JWT_SECRET_KEY/APP_SECRET_KEY."
            ),
        )

    token = _bearer_token(authorization)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Debes iniciar sesión para valorar este caso",
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


# ---------------------------------------------------------------------------
# Conversores
# ---------------------------------------------------------------------------
def _to_summary(level_id: str, aggregate: RatingAggregate) -> RatingSummary:
    return RatingSummary(
        level_id=level_id,
        average_rating=aggregate.average_rating,
        total_votes=aggregate.total_votes,
    )


def _normalise_level_ids(raw: list[str]) -> list[str]:
    """Limpia, deduplica y valida la lista de identificadores de nivel."""
    normalised: list[str] = []
    seen: set[str] = set()
    for value in raw:
        level_id = value.strip()
        if not level_id:
            continue
        if len(level_id) > MAX_LEVEL_ID_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Cada level_id debe tener como máximo "
                    f"{MAX_LEVEL_ID_LENGTH} caracteres"
                ),
            )
        if level_id not in seen:
            seen.add(level_id)
            normalised.append(level_id)

    if not normalised:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debes indicar al menos un level_id",
        )
    if len(normalised) > MAX_LEVEL_IDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Como máximo se pueden consultar {MAX_LEVEL_IDS} niveles por lote",
        )
    return normalised


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------
@app.get("/health", tags=["ops"], summary="Sonda de salud")
async def health() -> dict[str, str]:
    """Liveness/readiness usada por Docker, Compose y el CI."""
    return {"status": "ok", "service": "rating-service"}


# ---------------------------------------------------------------------------
# Valoraciones
# ---------------------------------------------------------------------------
@app.post(
    "/api/ratings/",
    response_model=RatingSummary,
    tags=["ratings"],
    summary="Registra o actualiza la valoración de un nivel",
)
async def create_rating(
    payload: RatingCreate,
    repository: RepositoryDep,
    current_user: CurrentUserDep,
) -> RatingSummary:
    """Crea el voto o actualiza el existente del mismo usuario.

    El ``user_id`` del payload debe coincidir con el ``sub`` del JWT; en caso
    contrario se rechaza para impedir que una cuenta vote en nombre de otra.
    """
    if not secrets.compare_digest(
        payload.user_id.encode("utf-8"),
        current_user.user_id.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El user_id no coincide con la sesión autenticada",
        )

    await repository.upsert(
        level_id=payload.level_id,
        user_id=current_user.user_id,
        stars=payload.stars,
    )
    logger.info(
        "Valoración registrada: nivel=%s usuario=%s estrellas=%d",
        payload.level_id,
        current_user.username,
        payload.stars,
    )
    aggregate = await repository.summary(payload.level_id)
    return _to_summary(payload.level_id, aggregate)


# La ruta por lotes se declara antes de /levels/{level_id}: de lo contrario
# "batch" se interpretaría como un identificador de nivel.
@app.get(
    "/api/ratings/levels/batch",
    response_model=dict[str, RatingSummary],
    tags=["ratings"],
    summary="Medias de varios niveles en una sola petición",
)
async def get_ratings_batch(
    repository: RepositoryDep,
    level_ids: Annotated[
        list[str] | None,
        Query(
            description=(
                "Identificador de nivel repetido tantas veces como sea "
                "necesario (?level_ids=a&level_ids=b)"
            )
        ),
    ] = None,
) -> dict[str, RatingSummary]:
    """Versión GET del lote, pensada para la carga inicial de la galería."""
    if level_ids is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debes indicar al menos un level_id",
        )
    normalised = _normalise_level_ids(level_ids)
    aggregates = await repository.summaries(normalised)
    return {
        level_id: _to_summary(level_id, aggregates.get(level_id, EMPTY_AGGREGATE))
        for level_id in normalised
    }


@app.post(
    "/api/ratings/levels/batch",
    response_model=dict[str, RatingSummary],
    tags=["ratings"],
    summary="Medias de varios niveles (lote por cuerpo)",
)
async def post_ratings_batch(
    payload: RatingBatchRequest,
    repository: RepositoryDep,
) -> dict[str, RatingSummary]:
    """Versión POST del lote para clientes que prefieren enviar un cuerpo."""
    normalised = _normalise_level_ids(payload.level_ids)
    aggregates = await repository.summaries(normalised)
    return {
        level_id: _to_summary(level_id, aggregates.get(level_id, EMPTY_AGGREGATE))
        for level_id in normalised
    }


@app.get(
    "/api/ratings/levels/{level_id}",
    response_model=RatingSummary,
    tags=["ratings"],
    summary="Media y número de votos de un nivel",
)
async def get_rating_summary(
    level_id: str,
    repository: RepositoryDep,
) -> RatingSummary:
    """Devuelve ``0.0`` y ``0`` votos si el nivel aún no tiene valoraciones."""
    if not level_id.strip() or len(level_id) > MAX_LEVEL_ID_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="level_id no válido",
        )
    aggregate = await repository.summary(level_id.strip())
    return _to_summary(level_id.strip(), aggregate)
