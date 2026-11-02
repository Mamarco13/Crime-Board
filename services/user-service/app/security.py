"""Hash de contraseñas y emisión/verificación de JWT.

Se usa PBKDF2-HMAC-SHA256 de la biblioteca estándar (``hashlib``) en lugar de
passlib/bcrypt: no añade dependencias nativas al build, es determinista y
permite fijar el número de iteraciones en el propio hash. El formato guardado
es ``pbkdf2_sha256$<iteraciones>$<sal_b64>$<digest_b64>``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt

PBKDF2_ALGORITHM = "sha256"
PBKDF2_ITERATIONS = 210_000
SALT_BYTES = 16


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str) -> str:
    """Devuelve un hash autosuficiente con sal aleatoria."""
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        PBKDF2_ALGORITHM,
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return (
        f"pbkdf2_{PBKDF2_ALGORITHM}${PBKDF2_ITERATIONS}"
        f"${_b64encode(salt)}${_b64encode(digest)}"
    )


def verify_password(password: str, encoded: str) -> bool:
    """Compara en tiempo constante; devuelve ``False`` ante un hash corrupto."""
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = encoded.split("$", 3)
        if algorithm != f"pbkdf2_{PBKDF2_ALGORITHM}":
            return False
        iterations = int(iterations_raw)
        if iterations <= 0:
            return False
        salt = _b64decode(salt_raw)
        expected = _b64decode(digest_raw)
    except (ValueError, TypeError):
        return False

    candidate = hashlib.pbkdf2_hmac(
        PBKDF2_ALGORITHM,
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(candidate, expected)


def create_access_token(
    *,
    user_id: str,
    username: str,
    secret_key: str,
    algorithm: str,
    issuer: str,
    expires_minutes: int,
) -> str:
    """Firma un JWT con la identidad mínima que necesita el resto del sistema."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": user_id,
        "username": username,
        "iss": issuer,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, secret_key, algorithm=algorithm)


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
