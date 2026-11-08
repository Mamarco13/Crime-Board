"""Cliente del catálogo interno (Level Management Service).

Playground Engine nunca conoce la solución de antemano: la pide al catálogo por
el endpoint interno protegido con ``X-Internal-Api-Key`` y la mantiene en una
caché de corta duración. El cliente final jamás recibe este objeto.
"""

from __future__ import annotations

import logging
from http import HTTPStatus

import httpx

from .schemas import LevelSnapshot
from .store import CACHE_PREFIX, JsonStore

logger = logging.getLogger(__name__)


class LevelNotFoundError(Exception):
    """El nivel solicitado no existe en el catálogo."""

    def __init__(self, level_id: str) -> None:
        super().__init__(f"El nivel '{level_id}' no existe")
        self.level_id = level_id


class CatalogUnavailableError(RuntimeError):
    """El catálogo no responde o devuelve datos no utilizables."""


class LevelCatalog:
    """Lectura del catálogo con caché read-through."""

    def __init__(
        self,
        *,
        base_url: str,
        internal_api_key: str | None,
        store: JsonStore,
        cache_ttl_seconds: int,
        timeout_seconds: float,
    ) -> None:
        headers = {
            "Accept": "application/json",
            "User-Agent": "open-crime-playground-engine/0.2",
        }
        if internal_api_key:
            headers["X-Internal-Api-Key"] = internal_api_key

        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            headers=headers,
        )
        self._store = store
        self._cache_ttl_seconds = cache_ttl_seconds

    async def get_level(self, level_id: str) -> LevelSnapshot:
        """Devuelve el nivel completo; traduce fallos a excepciones de dominio."""
        cache_key = f"{CACHE_PREFIX}level:{level_id}"
        cached = await self._store.get(cache_key)
        if cached is not None:
            try:
                return LevelSnapshot.model_validate(cached)
            except ValueError:
                # Caché de una versión anterior o corrupta: se ignora y se
                # vuelve a pedir al catálogo.
                logger.warning("Caché inválida para el nivel '%s'", level_id)

        try:
            response = await self._client.get(f"/api/levels/{level_id}/internal")
        except httpx.HTTPError as exc:
            raise CatalogUnavailableError(
                "No se pudo contactar con el catálogo de niveles"
            ) from exc

        if response.status_code == HTTPStatus.NOT_FOUND:
            raise LevelNotFoundError(level_id)
        if response.status_code in (
            HTTPStatus.UNAUTHORIZED,
            HTTPStatus.FORBIDDEN,
        ):
            raise CatalogUnavailableError(
                "El catálogo rechazó la clave interna; revisa "
                "INTERNAL_API_KEY/APP_SECRET_KEY en ambos servicios"
            )
        if response.status_code >= HTTPStatus.BAD_REQUEST:
            raise CatalogUnavailableError(
                f"El catálogo respondió con HTTP {response.status_code}"
            )

        try:
            snapshot = LevelSnapshot.model_validate(response.json())
        except ValueError as exc:
            raise CatalogUnavailableError(
                "El catálogo devolvió un nivel con formato inválido"
            ) from exc

        await self._store.set(
            cache_key,
            snapshot.model_dump(mode="json"),
            self._cache_ttl_seconds,
        )
        return snapshot

    async def close(self) -> None:
        await self._client.aclose()
