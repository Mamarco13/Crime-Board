"""Level Management Service — API del catálogo de niveles.

Responsabilidades del servicio:

* Publicar niveles (tarjetas + combinación ganadora).
* Servir el catálogo paginado y el detalle de cada nivel para el tablero.
* Exponer la solución únicamente por un canal interno autenticado, para que
  Playground Engine pueda validar acusaciones sin filtrarla al cliente final.

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
from .models import (
    Card,
    CardAdmin,
    CardType,
    LevelAdmin,
    LevelCreate,
    LevelDetail,
    LevelModel,
    LevelPage,
    LevelSummary,
)
from .repository import (
    InMemoryLevelRepository,
    LevelAlreadyExistsError,
    LevelRepository,
    SqlLevelRepository,
)
from .security import AuthenticatedUser, decode_access_token, user_from_payload
from .seed import seed_demo_level

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ciclo de vida: una única instancia de repositorio/engine por proceso
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
            repository: LevelRepository = SqlLevelRepository(session_factory)
            logger.info("Catálogo persistido en PostgreSQL")
        else:
            repository = InMemoryLevelRepository()
            logger.warning(
                "DATABASE_URL no definida: catálogo en memoria "
                "(los niveles se pierden al reiniciar)"
            )

        app.state.engine = engine
        app.state.repository = repository

        if settings.seed_demo_data:
            await seed_demo_level(repository)

        if settings.internal_api_key is None:
            logger.warning(
                "INTERNAL_API_KEY/APP_SECRET_KEY no definida: el endpoint "
                "interno de solución queda sin autenticación (solo desarrollo)"
            )

        if settings.jwt_secret_key is None:
            logger.warning(
                "JWT_SECRET_KEY/APP_SECRET_KEY no definida: no se podrán "
                "publicar niveles autenticados (solo lectura del catálogo)"
            )

        yield
    finally:
        if engine is not None:
            await engine.dispose()


app = FastAPI(
    title="Crime Board — Level Management Service",
    description=(
        "Catálogo de niveles y tarjetas de Crime Board. La solución del caso "
        "solo se expone en el endpoint interno autenticado."
    ),
    version="0.3.0",
    lifespan=lifespan,
)

# El tablero es una SPA estática que puede servirse desde otro origen (o
# abrirse como file://). Los orígenes se configuran con CORS_ALLOW_ORIGINS.
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


def get_repository(request: Request) -> LevelRepository:
    return request.app.state.repository


SettingsDep = Annotated[Settings, Depends(get_settings)]
RepositoryDep = Annotated[LevelRepository, Depends(get_repository)]


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def require_creator(
    settings: SettingsDep,
    authorization: Annotated[
        str | None,
        Header(description="JWT emitido por el User Service (prefijo Bearer)"),
    ] = None,
) -> AuthenticatedUser:
    """Exige una sesión válida para publicar un caso.

    Un invitado (sin cabecera Authorization) recibe 401. Un token caducado o
    manipulado también se rechaza. La identidad del creador nunca se toma del
    cuerpo de la petición: se extrae del JWT firmado.
    """
    if settings.jwt_secret_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "La autenticación no está configurada en el catálogo. "
                "Define JWT_SECRET_KEY/APP_SECRET_KEY."
            ),
        )

    token = _bearer_token(authorization)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Debes iniciar sesión para publicar un caso",
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


CurrentUserDep = Annotated[AuthenticatedUser, Depends(require_creator)]


async def require_internal_api_key(
    settings: SettingsDep,
    x_internal_api_key: Annotated[
        str | None,
        Header(alias="X-Internal-Api-Key", description="Clave compartida entre servicios"),
    ] = None,
) -> None:
    """Protege los endpoints que revelan la solución del caso.

    Si no hay clave configurada se permite el acceso y se avisa en el arranque:
    así el entorno local funciona sin secretos, mientras que Docker Compose
    inyecta ``APP_SECRET_KEY`` en ambos servicios.
    """
    expected = settings.internal_api_key
    if expected is None:
        return
    if x_internal_api_key is None or not secrets.compare_digest(
        x_internal_api_key.encode("utf-8"),
        expected.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clave interna no válida",
            headers={"WWW-Authenticate": "ApiKey"},
        )


# ---------------------------------------------------------------------------
# Conversores modelo ORM → esquema de salida
# ---------------------------------------------------------------------------
def _solution_composition(level: LevelModel) -> dict[str, int]:
    """Recuento de la solución por tipo, en el orden estable de ``CardType``."""
    card_types = {card.id: card.type for card in level.cards}
    counts: dict[str, int] = {}
    for card_id in level.solution_card_ids:
        card_type = card_types.get(card_id)
        if card_type is not None:
            counts[card_type.value] = counts.get(card_type.value, 0) + 1
    return {
        card_type.value: counts[card_type.value]
        for card_type in CardType
        if counts.get(card_type.value)
    }


def _composition_fields(level: LevelModel) -> dict[str, object]:
    """Campos derivados que se publican en el detalle o se ocultan por completo."""
    if not level.reveal_solution_composition:
        return {
            "solution_composition": None,
            "solution_total": None,
            "solution_composition_hidden": True,
        }
    return {
        "solution_composition": _solution_composition(level),
        "solution_total": len(level.solution_card_ids),
        "solution_composition_hidden": False,
    }


def _to_detail(level: LevelModel) -> LevelDetail:
    """Vista pública: solo tarjetas visibles y el total del expediente."""
    return LevelDetail(
        level_id=level.level_id,
        title=level.title,
        description=level.description,
        difficulty=level.difficulty,
        creator_username=level.creator_username,
        cards=[
            Card.model_validate(card) for card in level.cards if not card.hidden
        ],
        card_count=len(level.cards),
        created_at=level.created_at,
        **_composition_fields(level),
    )


def _to_admin(level: LevelModel) -> LevelAdmin:
    """Vista interna: incluye ocultas, alias y solución."""
    return LevelAdmin(
        level_id=level.level_id,
        title=level.title,
        description=level.description,
        difficulty=level.difficulty,
        creator_username=level.creator_username,
        cards=[CardAdmin.model_validate(card) for card in level.cards],
        card_count=len(level.cards),
        solution_card_ids=list(level.solution_card_ids),
        reveal_solution_composition=level.reveal_solution_composition,
        created_at=level.created_at,
        **_composition_fields(level),
    )


def _to_summary(level: LevelModel) -> LevelSummary:
    return LevelSummary(
        level_id=level.level_id,
        title=level.title,
        description=level.description,
        difficulty=level.difficulty,
        creator_username=level.creator_username,
        card_count=len(level.cards),
        created_at=level.created_at,
    )


async def _load_level_or_404(
    repository: LevelRepository,
    level_id: str,
) -> LevelModel:
    level = await repository.get(level_id)
    if level is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Nivel '{level_id}' no encontrado",
        )
    return level


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------
@app.get("/health", tags=["ops"], summary="Sonda de salud")
async def health() -> dict[str, str]:
    """Liveness/readiness usada por Docker, Compose y el CI."""
    return {"status": "ok", "service": "level-management"}


# ---------------------------------------------------------------------------
# Catálogo público
# ---------------------------------------------------------------------------
@app.post(
    "/api/levels/",
    response_model=LevelAdmin,
    status_code=status.HTTP_201_CREATED,
    tags=["levels"],
    summary="Publica un nuevo nivel",
)
async def create_level(
    payload: LevelCreate,
    repository: RepositoryDep,
    current_user: CurrentUserDep,
) -> LevelAdmin:
    """Valida y persiste un nivel completo.

    Requiere un JWT válido del User Service: los invitados deben registrarse
    para publicar. ``creator_username`` se toma del token, no del payload.
    La respuesta incluye ``solution_card_ids`` porque es una operación de
    autoría; los GET públicos nunca la devuelven.
    """
    try:
        level = await repository.create(
            payload,
            creator_username=current_user.username,
        )
    except LevelAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe un nivel con level_id '{exc.level_id}'",
        ) from exc

    logger.info(
        "Nivel creado: %s (%d tarjetas) por %s",
        level.level_id,
        len(level.cards),
        level.creator_username,
    )
    return _to_admin(level)


@app.get(
    "/api/levels/",
    response_model=LevelPage,
    tags=["levels"],
    summary="Lista el catálogo paginado",
)
async def list_levels(
    repository: RepositoryDep,
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Máximo de niveles por página"),
    ] = 20,
    offset: Annotated[int, Query(ge=0, description="Niveles a saltar")] = 0,
) -> LevelPage:
    levels, total = await repository.list_page(limit=limit, offset=offset)
    return LevelPage(
        items=[_to_summary(level) for level in levels],
        total=total,
        limit=limit,
        offset=offset,
    )


@app.get(
    "/api/levels/{level_id}",
    response_model=LevelDetail,
    tags=["levels"],
    summary="Obtiene un nivel para renderizar el tablero",
)
async def get_level(
    level_id: str,
    repository: RepositoryDep,
) -> LevelDetail:
    """Detalle público del nivel.

    ``solution_card_ids`` se omite deliberadamente para que un cliente no pueda
    resolver el caso leyendo la respuesta de red.
    """
    level = await _load_level_or_404(repository, level_id)
    return _to_detail(level)


# ---------------------------------------------------------------------------
# Canal interno (Playground Engine)
# ---------------------------------------------------------------------------
@app.get(
    "/api/levels/{level_id}/internal",
    response_model=LevelAdmin,
    tags=["internal"],
    summary="Nivel completo, incluida la solución",
    include_in_schema=False,
)
async def get_level_internal(
    level_id: str,
    repository: RepositoryDep,
    _: Annotated[None, Depends(require_internal_api_key)],
) -> LevelAdmin:
    """Uso exclusivo entre microservicios; requiere ``X-Internal-Api-Key``."""
    level = await _load_level_or_404(repository, level_id)
    return _to_admin(level)
