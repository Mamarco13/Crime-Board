"""Almacén JSON efímero (Redis) con degradación a memoria.

Se usa para dos cosas:

1. **Caché de niveles**: evita consultar el catálogo en cada interacción.
2. **Estado de la partida**: recuerda qué pistas ya se desbloquearon por
   sesión, para no entregar dos veces la misma tarjeta.

Si Redis no está disponible al arrancar, el servicio sigue funcionando con un
almacén en memoria. Es una decisión deliberada para el MVP: el tablero no deja
de jugar si el caché cae, aunque el estado no sobrevive a un reinicio.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

# Prefijos para que caché y estado compartan instancia sin colisionar.
CACHE_PREFIX = "open-crime:cache:"
STATE_PREFIX = "open-crime:state:"


class JsonStore(Protocol):
    """Contrato mínimo que consumen catálogo y motor."""

    async def get(self, key: str) -> dict[str, Any] | None: ...

    async def set(
        self,
        key: str,
        value: dict[str, Any],
        ttl_seconds: int,
    ) -> None: ...

    async def close(self) -> None: ...


class InMemoryJsonStore:
    """Almacén con expiración perezosa para desarrollo y tests."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[float, dict[str, Any]]] = {}

    async def get(self, key: str) -> dict[str, Any] | None:
        entry = self._items.get(key)
        if entry is None:
            return None

        expires_at, value = entry
        if expires_at <= time.monotonic():
            self._items.pop(key, None)
            return None
        return value

    async def set(
        self,
        key: str,
        value: dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        self._items[key] = (
            time.monotonic() + max(1, ttl_seconds),
            value,
        )

    async def close(self) -> None:
        self._items.clear()


class RedisJsonStore:
    """Almacén JSON sobre Redis con fallo abierto en cada operación."""

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def get(self, key: str) -> dict[str, Any] | None:
        try:
            raw = await self._client.get(key)
        except RedisError as exc:
            logger.warning("Redis no disponible al leer '%s': %s", key, exc)
            return None

        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            logger.warning("Valor no JSON en Redis para '%s'; se ignora", key)
            return None

        return value if isinstance(value, dict) else None

    async def set(
        self,
        key: str,
        value: dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        try:
            payload = json.dumps(value, ensure_ascii=False)
            await self._client.set(key, payload, ex=max(1, ttl_seconds))
        except (TypeError, ValueError, RedisError) as exc:
            logger.warning("No se pudo escribir '%s' en Redis: %s", key, exc)

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except RedisError as exc:
            logger.warning("Error cerrando la conexión Redis: %s", exc)


async def build_json_store(redis_url: str | None) -> JsonStore:
    """Devuelve Redis si responde; en caso contrario, memoria."""
    if not redis_url:
        logger.warning(
            "REDIS_URL no definida: estado efímero y caché en memoria "
            "(no se comparten entre réplicas)"
        )
        return InMemoryJsonStore()

    client: Redis | None = None
    try:
        client = Redis.from_url(redis_url, decode_responses=True)
        await client.ping()
    except (RedisError, OSError, ValueError) as exc:
        logger.warning(
            "No se pudo conectar a Redis (%s): se usa memoria como fallback",
            exc,
        )
        if client is not None:
            try:
                await client.aclose()
            except RedisError:
                pass
        return InMemoryJsonStore()

    logger.info("Estado efímero y caché de niveles en Redis")
    return RedisJsonStore(client)
