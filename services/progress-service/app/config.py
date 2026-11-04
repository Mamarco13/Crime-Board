"""Configuración del Progress Service.

Toda la configuración llega por variables de entorno inyectadas por Docker
Compose (o por el orquestador de turno) para que la imagen sea inmutable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _as_origins(raw: str | None) -> tuple[str, ...]:
    """Convierte ``CORS_ALLOW_ORIGINS`` en una tupla de orígenes."""
    if raw is None or not raw.strip():
        return ("*",)
    origins = tuple(item.strip() for item in raw.split(",") if item.strip())
    return origins or ("*",)


@dataclass(frozen=True, slots=True)
class Settings:
    """Parámetros de ejecución del servicio."""

    environment: str
    log_level: str
    # Sin DATABASE_URL el servicio usa un repositorio en memoria (tests/dev).
    database_url: str | None
    # Clave y parámetros para verificar los JWT emitidos por User Service.
    jwt_secret_key: str | None
    jwt_algorithm: str
    jwt_issuer: str
    # Clave compartida con Playground Engine y User Service para los endpoints
    # internos. Sin valor quedan abiertos solo en desarrollo.
    internal_api_key: str | None
    cors_allow_origins: tuple[str, ...]

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            environment=os.getenv("ENVIRONMENT", "development"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            database_url=os.getenv("DATABASE_URL") or None,
            jwt_secret_key=os.getenv("JWT_SECRET_KEY")
            or os.getenv("APP_SECRET_KEY")
            or None,
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256").upper(),
            jwt_issuer=os.getenv("JWT_ISSUER", "crime-board-user-service"),
            internal_api_key=os.getenv("INTERNAL_API_KEY")
            or os.getenv("APP_SECRET_KEY")
            or None,
            cors_allow_origins=_as_origins(os.getenv("CORS_ALLOW_ORIGINS")),
        )
