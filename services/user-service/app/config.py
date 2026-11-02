"""Configuración del User Service.

La imagen es inmutable: todos los parámetros llegan por variables de entorno
inyectadas por Docker Compose (o por el orquestador de turno).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Secreto de desarrollo. En cualquier entorno compartido debe sustituirse por
# una clave aleatoria de al menos 32 bytes (ver .env.example).
DEFAULT_DEV_SECRET = "dev_only_change_me_user_service_secret"


def _as_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(raw: str | None, *, default: int) -> int:
    try:
        value = int(raw or "")
    except ValueError:
        return default
    return value if value > 0 else default


def _as_origins(raw: str | None) -> tuple[str, ...]:
    """Convierte CORS_ALLOW_ORIGINS en una tupla de orígenes."""
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
    # Clave compartida con Level Management y Playground Engine para firmar y
    # verificar los JWT. JWT_SECRET_KEY tiene prioridad sobre APP_SECRET_KEY.
    jwt_secret_key: str
    jwt_algorithm: str
    jwt_issuer: str
    access_token_expire_minutes: int
    cors_allow_origins: tuple[str, ...]

    @classmethod
    def from_env(cls) -> Settings:
        secret = (
            os.getenv("JWT_SECRET_KEY")
            or os.getenv("APP_SECRET_KEY")
            or DEFAULT_DEV_SECRET
        )
        return cls(
            environment=os.getenv("ENVIRONMENT", "development"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            database_url=os.getenv("DATABASE_URL") or None,
            jwt_secret_key=secret,
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256").upper(),
            jwt_issuer=os.getenv("JWT_ISSUER", "crime-board-user-service"),
            access_token_expire_minutes=_as_int(
                os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES"),
                default=720,
            ),
            cors_allow_origins=_as_origins(os.getenv("CORS_ALLOW_ORIGINS")),
        )
