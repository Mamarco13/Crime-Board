"""Playground Engine Service — API del motor de juego.

Flujo de una partida:

1. ``POST /api/engine/interrogate``: el jugador interroga a un sospechoso y
   recibe su declaración. Si el nivel usa el archivador, no se desbloquea
   ninguna tarjeta aquí: el jugador debe buscar el término en el archivador.
2. ``POST /api/engine/archive``: el jugador teclea un código, una dirección o
   una palabra y recupera la ficha oculta que coincida.
3. ``POST /api/engine/validate``: el jugador envía su acusación. El motor pide
   la solución al catálogo interno, la compara y devuelve veredicto + feedback.

La solución nunca se expone en las respuestas de este servicio.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .archive import match_archive
from .catalog import (
    CatalogUnavailableError,
    LevelCatalog,
    LevelNotFoundError,
)
from .config import Settings
from .engine import (
    MissingClueError,
    build_interrogation,
    evaluate_accusation,
)
from .schemas import (
    AccusationRequest,
    AccusationResponse,
    ArchiveRequest,
    ArchiveResponse,
    Card,
    CardType,
    InterrogationRequest,
    InterrogationResponse,
)
from .store import STATE_PREFIX, JsonStore, build_json_store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ciclo de vida: clientes compartidos por todas las peticiones
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    store = await build_json_store(settings.redis_url)
    catalog = LevelCatalog(
        base_url=settings.level_management_url,
        internal_api_key=settings.internal_api_key,
        store=store,
        cache_ttl_seconds=settings.level_cache_ttl_seconds,
        timeout_seconds=settings.level_service_timeout_seconds,
    )

    app.state.settings = settings
    app.state.store = store
    app.state.catalog = catalog

    if settings.internal_api_key is None:
        logger.warning(
            "INTERNAL_API_KEY/APP_SECRET_KEY no definida: el catálogo debe "
            "estar en modo desarrollo para aceptar peticiones internas"
        )

    try:
        yield
    finally:
        await catalog.close()
        await store.close()


app = FastAPI(
    title="Crime Board — Playground Engine Service",
    description=(
        "Interrogatorios y validación de acusaciones del tablero. El estado "
        "efímero se guarda en Redis y la solución se consulta al catálogo "
        "interno."
    ),
    version="0.2.0",
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
# Manejo uniforme de fallos del catálogo
# ---------------------------------------------------------------------------
@app.exception_handler(LevelNotFoundError)
async def _handle_level_not_found(
    _: Request,
    exc: LevelNotFoundError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": f"Nivel '{exc.level_id}' no encontrado"},
    )


@app.exception_handler(CatalogUnavailableError)
async def _handle_catalog_unavailable(
    _: Request,
    exc: CatalogUnavailableError,
) -> JSONResponse:
    logger.error("Catálogo no disponible: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        headers={"Retry-After": "5"},
        content={"detail": str(exc)},
    )


# ---------------------------------------------------------------------------
# Dependencias
# ---------------------------------------------------------------------------
def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_catalog(request: Request) -> LevelCatalog:
    return request.app.state.catalog


def get_store(request: Request) -> JsonStore:
    return request.app.state.store


SettingsDep = Annotated[Settings, Depends(get_settings)]
CatalogDep = Annotated[LevelCatalog, Depends(get_catalog)]
StoreDep = Annotated[JsonStore, Depends(get_store)]


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------
@app.get("/health", tags=["ops"], summary="Sonda de salud")
async def health() -> dict[str, str]:
    """Liveness/readiness usada por Docker, Compose y el CI."""
    return {"status": "ok", "service": "playground-engine"}


# ---------------------------------------------------------------------------
# Interrogatorios
# ---------------------------------------------------------------------------
@app.post(
    "/api/engine/interrogate",
    response_model=InterrogationResponse,
    tags=["engine"],
    summary="Interroga a un sospechoso y desbloquea una pista",
)
async def interrogate(
    payload: InterrogationRequest,
    catalog: CatalogDep,
    store: StoreDep,
    settings: SettingsDep,
) -> InterrogationResponse:
    level = await catalog.get_level(payload.level_id)

    suspect = next(
        (card for card in level.cards if card.id == payload.suspect_card_id),
        None,
    )
    if suspect is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"La tarjeta '{payload.suspect_card_id}' no pertenece al "
                f"nivel '{level.level_id}'"
            ),
        )
    if suspect.type is not CardType.SUSPECT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"La tarjeta '{suspect.id}' es de tipo {suspect.type.value}; "
                "solo se puede interrogar a sospechosos"
            ),
        )

    state_key = (
        f"{STATE_PREFIX}interrogation:"
        f"{level.level_id}:{payload.session_id}:{suspect.id}"
    )
    previous = await store.get(state_key)
    if previous is not None and isinstance(previous.get("text"), str):
        # Misma partida y mismo sospechoso: la pista ya está en el tablón.
        return InterrogationResponse(
            suspect_card_id=suspect.id,
            unlocked_clue_text=previous["text"],
            new_card_unlocked=None,
        )

    try:
        text, clue = build_interrogation(level, suspect)
    except MissingClueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "El nivel no tiene pistas asociadas; no es posible "
                "desbloquear nuevas tarjetas"
            ),
        ) from exc

    await store.set(
        state_key,
        {"text": text, "clue_card_id": clue.id if clue else None},
        settings.interrogation_ttl_seconds,
    )
    logger.info(
        "Interrogatorio: nivel=%s sesión=%s sospechoso=%s pista=%s",
        level.level_id,
        payload.session_id,
        suspect.id,
        clue.id if clue else "-",
    )

    return InterrogationResponse(
        suspect_card_id=suspect.id,
        unlocked_clue_text=text,
        new_card_unlocked=clue,
    )


# ---------------------------------------------------------------------------
# Archivador
# ---------------------------------------------------------------------------
@app.post(
    "/api/engine/archive",
    response_model=ArchiveResponse,
    tags=["engine"],
    summary="Busca un código, dirección o palabra en el archivador",
)
async def archive(
    payload: ArchiveRequest,
    catalog: CatalogDep,
    store: StoreDep,
    settings: SettingsDep,
) -> ArchiveResponse:
    """Recupera la ficha oculta cuyo alias coincide con la consulta.

    El estado se guarda por sesión: una ficha ya recuperada se devuelve con
    ``already_unlocked=true`` para que el cliente pueda reconstruir el tablero
    tras una recarga sin volver a concederla como nueva. Las tarjetas visibles
    que coincidan también responden así, sin añadirse dos veces.
    """
    level = await catalog.get_level(payload.level_id)
    result = match_archive(level, payload.query)

    if not result.matched or result.card is None:
        logger.info(
            "Archivador sin resultados: nivel=%s sesión=%s consulta=%r",
            level.level_id,
            payload.session_id,
            payload.query,
        )
        return ArchiveResponse(
            found=False,
            card=None,
            already_unlocked=False,
            message=(
                "Registro no encontrado. Revisa el código, la dirección o la "
                "palabra y vuelve a intentarlo."
            ),
            score=None,
        )

    card = result.card
    state_key = (
        f"{STATE_PREFIX}archive:"
        f"{level.level_id}:{payload.session_id}:{card.id}"
    )
    previous = await store.get(state_key)
    already_unlocked = previous is not None or not card.hidden

    if card.hidden and previous is None:
        await store.set(
            state_key,
            {"card": card.model_dump(mode="json")},
            settings.interrogation_ttl_seconds,
        )

    if already_unlocked:
        message = "Esa ficha ya está sobre el tablero."
    else:
        message = f"Registro recuperado: {card.title}."

    logger.info(
        "Archivador: nivel=%s sesión=%s ficha=%s ya_recuperada=%s "
        "puntuación=%.3f",
        level.level_id,
        payload.session_id,
        card.id,
        already_unlocked,
        result.score,
    )

    return ArchiveResponse(
        found=True,
        card=Card.model_validate(card.model_dump()),
        already_unlocked=already_unlocked,
        message=message,
        score=result.score,
    )


# ---------------------------------------------------------------------------
# Validación de la acusación
# ---------------------------------------------------------------------------
@app.post(
    "/api/engine/validate",
    response_model=AccusationResponse,
    tags=["engine"],
    summary="Valida la acusación final",
)
async def validate_accusation(
    payload: AccusationRequest,
    catalog: CatalogDep,
) -> AccusationResponse:
    started = time.perf_counter()
    level = await catalog.get_level(payload.level_id)

    known_card_ids = {card.id for card in level.cards}
    unknown = [
        card_id
        for card_id in payload.selected_card_ids
        if card_id not in known_card_ids
    ]
    if unknown:
        # Un id desconocido indica desincronización con el tablero, no una
        # acusación simplemente incorrecta.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "La acusación contiene tarjetas que no pertenecen al nivel: "
                + ", ".join(unknown[:10])
            ),
        )

    outcome = evaluate_accusation(level, payload.selected_card_ids)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    logger.info(
        "Validación: nivel=%s resuelto=%s elementos=%d tiempo=%dms",
        level.level_id,
        outcome.solved,
        len(payload.selected_card_ids),
        elapsed_ms,
    )

    return AccusationResponse(
        solved=outcome.solved,
        feedback_message=outcome.feedback_message,
        elapsed_ms=elapsed_ms,
    )
