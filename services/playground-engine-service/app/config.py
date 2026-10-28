"""Configuración del Playground Engine Service.

Se lee del entorno para que el contenedor sea el mismo en todos los entornos.
Todas las variables tienen valores por defecto aptos para desarrollo local.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _as_int(raw: str | None, *, default: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _as_float(raw: str | None, *, default: float) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        return max(0.1, float(raw))
    except ValueError:
        return default


def _as_origins(raw: str | None) -> tuple[str, ...]:
    """Convierte ``CORS_ALLOW_ORIGINS`` en una tupla de orígenes.

    Sin valor se permite cualquier origen (útil para abrir el tablero desde
    ``file://`` o un servidor estático local). En producción conviene fijar la
    lista separada por comas con los orígenes reales del frontend.
    """
    if raw is None or not raw.strip():
        return ("*",)
    origins = tuple(item.strip() for item in raw.split(",") if item.strip())
    return origins or ("*",)


@dataclass(frozen=True, slots=True)
class Settings:
    """Parámetros de ejecución del motor de juego."""

    environment: str
    log_level: str
    level_management_url: str
    # Debe coincidir con INTERNAL_API_KEY/APP_SECRET_KEY del catálogo.
    internal_api_key: str | None
    # Sin REDIS_URL el estado efímero y la caché viven en el proceso.
    redis_url: str | None
    level_cache_ttl_seconds: int
    interrogation_ttl_seconds: int
    level_service_timeout_seconds: float
    # Orígenes autorizados para el frontend (CORS).
    cors_allow_origins: tuple[str, ...]

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            environment=os.getenv("ENVIRONMENT", "development"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            level_management_url=os.getenv(
                "LEVEL_MANAGEMENT_SERVICE_URL",
                "http://127.0.0.1:8000",
            ).rstrip("/"),
            internal_api_key=os.getenv("INTERNAL_API_KEY")
            or os.getenv("APP_SECRET_KEY")
            or None,
            redis_url=os.getenv("REDIS_URL") or None,
            level_cache_ttl_seconds=_as_int(
                os.getenv("LEVEL_CACHE_TTL_SECONDS"),
                default=300,
            ),
            interrogation_ttl_seconds=_as_int(
                os.getenv("INTERROGATION_TTL_SECONDS"),
                default=86_400,
            ),
            level_service_timeout_seconds=_as_float(
                os.getenv("LEVEL_SERVICE_TIMEOUT_SECONDS"),
                default=3.0,
            ),
            cors_allow_origins=_as_origins(os.getenv("CORS_ALLOW_ORIGINS")),
        )
