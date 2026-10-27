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
    # Carga el nivel de demostración al arrancar si el catálogo está vacío.
    seed_demo_data: bool

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            environment=os.getenv("ENVIRONMENT", "development"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            database_url=os.getenv("DATABASE_URL") or None,
            internal_api_key=os.getenv("INTERNAL_API_KEY")
            or os.getenv("APP_SECRET_KEY")
            or None,
            seed_demo_data=_as_bool(os.getenv("SEED_DEMO_DATA"), default=True),
        )
