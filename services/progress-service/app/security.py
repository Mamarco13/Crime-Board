"""Verificación de JWT y de la clave interna del Progress Service.

Los endpoints públicos exigen un JWT emitido por el User Service; la identidad
sale siempre del ``sub`` del token, nunca del cuerpo de la petición. Los
endpoints internos usan la misma clave compartida que el resto de servicios.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from jose import JWTError, jwt


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Identidad mínima necesaria para aislar el progreso de un usuario."""

    user_id: str
    username: str


def decode_access_token(
    token: str,
    *,
    secret_key: str,
    algorithm: str,
    issuer: str,
) -> dict[str, Any] | None:
    """Valida firma, caducidad e issuer. Devuelve ``None`` si no es válido."""
    try:
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=[algorithm],
            issuer=issuer,
        )
    except JWTError:
        return None
    return payload if isinstance(payload, dict) else None


def user_from_payload(payload: dict[str, Any]) -> AuthenticatedUser | None:
    """Extrae ``sub`` y ``username`` del token, exigiendo ambos."""
    user_id = payload.get("sub")
    username = payload.get("username")
    if not isinstance(user_id, str) or not user_id.strip():
        return None
    if not isinstance(username, str) or not username.strip():
        return None
    return AuthenticatedUser(
        user_id=user_id.strip(),
        username=username.strip()[:64],
    )


def internal_key_matches(provided: str | None, expected: str | None) -> bool:
    """Compara la clave interna en tiempo constante.

    Si el servicio no tiene clave configurada se permite el acceso para no
    romper el entorno local; en Compose siempre se inyecta una.
    """
    if expected is None:
        return True
    if provided is None:
        return False
    return secrets.compare_digest(
        provided.encode("utf-8"),
        expected.encode("utf-8"),
    )
