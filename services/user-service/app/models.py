"""Modelos de persistencia y contratos HTTP del User Service.

La tabla ``users`` vive en la misma instancia de PostgreSQL que el catálogo de
niveles, pero con un nombre propio para no acoplar los esquemas de ambos
servicios. Cada servicio es dueño de sus tablas.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

USERNAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RESERVED_USERNAMES = {"anonimo", "anónimo", "invitado", "guest", "sistema"}


class Base(DeclarativeBase):
    """Base declarativa de SQLAlchemy 2.x."""


class UserModel(Base):
    """Cuenta de un detective creador de casos."""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        unique=True,
        index=True,
    )
    email: Mapped[str] = mapped_column(
        String(254),
        nullable=False,
        unique=True,
        index=True,
    )
    # Hash autosuficiente (algoritmo + iteraciones + sal + digest). Nunca se
    # expone por la API.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class UserRegister(BaseModel):
    """Payload de ``POST /api/users/register``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: str = Field(
        min_length=3,
        max_length=32,
        pattern=USERNAME_PATTERN,
        examples=["detective_miller"],
    )
    email: str = Field(min_length=5, max_length=254, examples=["miller@crimeboard.dev"])
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str) -> str:
        if value.lower() in RESERVED_USERNAMES:
            raise ValueError("Ese nombre de usuario está reservado")
        return value

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        if not EMAIL_PATTERN.match(value):
            raise ValueError("El correo electrónico no tiene un formato válido")
        return value.lower()

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("La contraseña no puede empezar ni terminar en espacios")
        return value


class UserLogin(BaseModel):
    """Credenciales de ``POST /api/users/login``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class UserPublic(BaseModel):
    """Vista pública de una cuenta (sin hash ni datos sensibles)."""

    model_config = ConfigDict(from_attributes=True)

    user_id: str
    username: str
    email: str
    created_at: datetime


class TokenResponse(BaseModel):
    """Sesión emitida tras un login correcto."""

    access_token: str
    token_type: str = "bearer"
    username: str
    user_id: str
    expires_in: int = Field(description="Vigencia del token en segundos.")
