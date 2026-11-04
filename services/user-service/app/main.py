"""User Service — registro, login y eliminación de cuentas.

Flujo de autenticación:

1. ``POST /api/users/register`` crea una cuenta con contraseña hasheada.
2. ``POST /api/users/login`` devuelve un JWT firmado con la clave compartida
   (``JWT_SECRET_KEY``/``APP_SECRET_KEY``). Level Management verifica ese mismo
   token para autorizar la publicación de casos.
3. ``DELETE /api/users/{user_id}`` exige un token válido del propio usuario.

Los invitados no necesitan cuenta para jugar: Playground Engine acepta sus
partidas, pero Level Management rechaza la publicación de casos sin sesión.
"""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncEngine

from .config import Settings
from .database import init_database
from .models import TokenResponse, UserLogin, UserPublic, UserRegister
from .progress import purge_progress_data
from .repository import (
    InMemoryUserRepository,
    SqlUserRepository,
    UserAlreadyExistsError,
    UserRepository,
    generate_user_id,
)
from .security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    app.state.settings = settings
    engine: AsyncEngine | None = None

    try:
        if settings.database_url:
            engine, session_factory = await init_database(settings.database_url)
            repository: UserRepository = SqlUserRepository(session_factory)
            logger.info("Cuentas persistidas en PostgreSQL")
        else:
            repository = InMemoryUserRepository()
            logger.warning(
                "DATABASE_URL no definida: cuentas en memoria "
                "(se pierden al reiniciar)"
            )

        app.state.engine = engine
        app.state.repository = repository

        if settings.jwt_secret_key.startswith("dev_only"):
            logger.warning(
                "JWT_SECRET_KEY/APP_SECRET_KEY no definida: usando secreto de "
                "desarrollo. No uses este valor fuera de local."
            )

        yield
    finally:
        if engine is not None:
            await engine.dispose()


app = FastAPI(
    title="OpenCrime — User Service",
    description=(
        "Registro y autenticación de detectives. Emite los JWT que autorizan "
        "la publicación de casos en Level Management."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(Settings.from_env().cors_allow_origins),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Dependencias
# ---------------------------------------------------------------------------
def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_repository(request: Request) -> UserRepository:
    return request.app.state.repository


SettingsDep = Annotated[Settings, Depends(get_settings)]
RepositoryDep = Annotated[UserRepository, Depends(get_repository)]


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def get_current_user(
    settings: SettingsDep,
    repository: RepositoryDep,
    authorization: Annotated[
        str | None,
        Header(description="Token JWT con prefijo Bearer"),
    ] = None,
) -> UserPublic:
    """Valida el Bearer token y devuelve la cuenta autenticada."""
    token = _bearer_token(authorization)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Debes iniciar sesión para realizar esta acción",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(
        token,
        secret_key=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
    )
    user_id = payload.get("sub") if payload else None
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión no válida o caducada",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await repository.get(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La cuenta asociada al token ya no existe",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return UserPublic.model_validate(user)


CurrentUserDep = Annotated[UserPublic, Depends(get_current_user)]


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------
@app.get("/health", tags=["ops"], summary="Sonda de salud")
async def health() -> dict[str, str]:
    """Liveness/readiness usada por Docker, Compose y el CI."""
    return {"status": "ok", "service": "user-service"}


# ---------------------------------------------------------------------------
# Cuentas
# ---------------------------------------------------------------------------
@app.post(
    "/api/users/register",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    tags=["users"],
    summary="Registra una nueva cuenta",
)
async def register(
    payload: UserRegister,
    repository: RepositoryDep,
) -> UserPublic:
    """Crea la cuenta si el nombre de usuario y el correo están libres."""
    if await repository.get_by_username(payload.username) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ese nombre de usuario ya está registrado",
        )
    if await repository.get_by_email(payload.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ese correo electrónico ya está registrado",
        )

    try:
        user = await repository.create(
            user_id=generate_user_id(),
            username=payload.username,
            email=payload.email,
            password_hash=hash_password(payload.password),
        )
    except UserAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe una cuenta con ese {exc.field}",
        ) from exc

    logger.info("Cuenta creada: %s (%s)", user.username, user.user_id)
    return UserPublic.model_validate(user)


@app.post(
    "/api/users/login",
    response_model=TokenResponse,
    tags=["users"],
    summary="Autentica y devuelve un JWT",
)
async def login(
    payload: UserLogin,
    repository: RepositoryDep,
    settings: SettingsDep,
) -> TokenResponse:
    """Valida las credenciales y emite una sesión firmada."""
    user = await repository.get_by_username(payload.username)
    if user is None or not verify_password(payload.password, user.password_hash):
        # Mensaje deliberadamente genérico: no revela si el usuario existe.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(
        user_id=user.user_id,
        username=user.username,
        secret_key=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
        expires_minutes=settings.access_token_expire_minutes,
    )
    logger.info("Login correcto: %s", user.username)
    return TokenResponse(
        access_token=token,
        username=user.username,
        user_id=user.user_id,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@app.get(
    "/api/users/me",
    response_model=UserPublic,
    tags=["users"],
    summary="Devuelve la cuenta del token actual",
)
async def me(current_user: CurrentUserDep) -> UserPublic:
    """Permite a la SPA restaurar la sesión y comprobar su vigencia."""
    return current_user


@app.delete(
    "/api/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    tags=["users"],
    summary="Elimina la cuenta del usuario autenticado",
)
async def delete_user(
    user_id: str,
    current_user: CurrentUserDep,
    repository: RepositoryDep,
    settings: SettingsDep,
) -> None:
    """Un usuario solo puede borrar su propia cuenta."""
    if not secrets.compare_digest(current_user.user_id, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No puedes eliminar la cuenta de otro usuario",
        )

    if not await repository.delete(user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cuenta no encontrada",
        )
    logger.info("Cuenta eliminada: %s", user_id)
    # Best-effort: la cuenta ya no existe aunque Progress Service no responda.
    await purge_progress_data(settings, user_id)
