"""Playground Engine Service — API del motor de juego.

Flujo de una partida:

1. ``POST /api/engine/interrogate``: el jugador interroga a un sospechoso y
   desbloquea una pista. El estado se guarda por ``session_id`` en Redis (o en
   memoria si Redis no está disponible).
2. ``POST /api/engine/validate``: el jugador envía su acusación. El motor pide
   la solución al catálogo interno, la compara y devuelve veredicto + feedback.

La solución nunca se expone en las respuestas de este servicio.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

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
        {"text": text, "clue_card_id": clue.id},
        settings.interrogation_ttl_seconds,
    )
    logger.info(
        "Interrogatorio: nivel=%s sesión=%s sospechoso=%s pista=%s",
        level.level_id,
        payload.session_id,
        suspect.id,
        clue.id,
    )

    return InterrogationResponse(
        suspect_card_id=suspect.id,
        unlocked_clue_text=text,
        new_card_unlocked=clue,
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
