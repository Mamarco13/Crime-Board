"""Verificación de los JWT emitidos por el User Service.

Level Management no guarda sesiones: valida la firma con la clave compartida
(``JWT_SECRET_KEY`` o ``APP_SECRET_KEY``) y extrae la identidad del creador.
Así el catálogo no depende de una llamada síncrona al User Service en cada
publicación y puede escalar de forma independiente.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jose import JWTError, jwt


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Identidad mínima necesaria para atribuir un nivel."""

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
