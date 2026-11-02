"""Configuración del Level Management Service.

Toda la configuración se obtiene del entorno (Docker Compose inyecta las
variables) para que la imagen sea inmutable y el mismo artefacto sirva en
desarrollo, staging y producción.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(raw: str | None, *, default: bool) -> bool:
    """Interpreta variables de entorno booleanas de forma tolerante."""
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
    """Parámetros de ejecución del servicio."""

    environment: str
    log_level: str
    # Si está vacío se usa el repositorio en memoria (modo autónomo/dev).
    database_url: str | None
    # Clave compartida con Playground Engine para el endpoint de solución.
    # Si no se define, el endpoint interno queda abierto (solo desarrollo).
    internal_api_key: str | None
    # Clave y parámetros para verificar los JWT emitidos por User Service.
    # Si no hay clave, la publicación autenticada queda deshabilitada.
    jwt_secret_key: str | None
    jwt_algorithm: str
    jwt_issuer: str
    # Carga el nivel de demostración al arrancar si el catálogo está vacío.
    seed_demo_data: bool
    # Orígenes autorizados para el frontend (CORS).
    cors_allow_origins: tuple[str, ...]

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            environment=os.getenv("ENVIRONMENT", "development"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            database_url=os.getenv("DATABASE_URL") or None,
            internal_api_key=os.getenv("INTERNAL_API_KEY")
            or os.getenv("APP_SECRET_KEY")
            or None,
            jwt_secret_key=os.getenv("JWT_SECRET_KEY")
            or os.getenv("APP_SECRET_KEY")
            or None,
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256").upper(),
            jwt_issuer=os.getenv("JWT_ISSUER", "crime-board-user-service"),
            seed_demo_data=_as_bool(os.getenv("SEED_DEMO_DATA"), default=True),
            cors_allow_origins=_as_origins(os.getenv("CORS_ALLOW_ORIGINS")),
        )
