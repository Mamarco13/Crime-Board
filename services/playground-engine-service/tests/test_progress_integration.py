"""Integración del motor con Progress Service (registro fail-open)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app.config import Settings
from app.main import validate_accusation
from app.progress import ProgressClient
from app.schemas import (
    AccusationRequest,
    AttemptProgress,
    LevelSnapshot,
)
from jose import jwt


class FakeCatalog:
    def __init__(self, level: LevelSnapshot) -> None:
        self.level = level

    async def get_level(self, level_id: str) -> LevelSnapshot:
        return self.level


class FakeProgress:
    def __init__(
        self,
        result: AttemptProgress | None = None,
    ) -> None:
        self.result = result or AttemptProgress(
            recorded=True,
            attempt_number=3,
            first_solve=False,
            is_replay=True,
        )
        self.calls: list[dict] = []

    async def record_attempt(self, **kwargs) -> AttemptProgress:
        self.calls.append(kwargs)
        return self.result


def _level() -> LevelSnapshot:
    return LevelSnapshot(
        level_id="progress-test",
        title="Prueba de progreso",
        cards=[
            {
                "id": "suspect-a",
                "type": "SUSPECT",
                "title": "Sospechoso",
                "description": "Visible",
            },
            {
                "id": "clue-a",
                "type": "CLUE",
                "title": "Pista",
                "description": "Visible",
            },
        ],
        solution_card_ids=["suspect-a", "clue-a"],
    )


def _settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_ISSUER", "crime-board-user-service")
    monkeypatch.setenv("PROGRESS_SERVICE_URL", "http://progress:8000")
    monkeypatch.setenv("PROGRESS_ENABLED", "true")
    return Settings.from_env()


def _token(secret: str = "test-secret") -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "usr_engine",
            "username": "detective_engine",
            "iss": "crime-board-user-service",
            "iat": now,
            "exp": now + timedelta(hours=1),
        },
        secret,
        algorithm="HS256",
    )


def _payload(**overrides) -> AccusationRequest:
    data = {
        "level_id": "progress-test",
        "selected_card_ids": ["suspect-a", "clue-a"],
        "session_id": "session-1",
        "client_attempt_id": "attempt-1",
    }
    data.update(overrides)
    return AccusationRequest(**data)


def _run(payload, settings, progress, authorization):
    return asyncio.run(
        validate_accusation(
            payload,
            FakeCatalog(_level()),
            settings,
            progress,
            authorization=authorization,
        )
    )


def test_authenticated_accusation_records_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    progress = FakeProgress()

    response = _run(
        _payload(),
        settings,
        progress,
        f"Bearer {_token()}",
    )

    assert response.solved is True
    assert response.progress is not None
    assert response.progress.recorded is True
    assert response.progress.attempt_number == 3
    assert len(progress.calls) == 1
    call = progress.calls[0]
    assert call["user_id"] == "usr_engine"
    assert call["level_id"] == "progress-test"
    assert call["session_key"] == "session-1"
    assert call["client_attempt_id"] == "attempt-1"
    assert call["solved"] is True
    assert call["selected_card_ids"] == ["suspect-a", "clue-a"]


def test_guest_does_not_record_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    progress = FakeProgress()

    response = _run(_payload(guest=True), settings, progress, None)

    assert response.solved is True
    assert response.progress is None
    assert progress.calls == []


def test_invalid_token_is_treated_as_guest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    progress = FakeProgress()

    response = _run(
        _payload(),
        settings,
        progress,
        "Bearer token-invalido",
    )

    assert response.progress is None
    assert progress.calls == []


def test_progress_failure_keeps_the_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    progress = FakeProgress(AttemptProgress(recorded=False))

    response = _run(
        _payload(),
        settings,
        progress,
        f"Bearer {_token()}",
    )

    assert response.solved is True
    assert response.progress is not None
    assert response.progress.recorded is False
    assert response.progress.attempt_number is None


def test_progress_client_swallows_network_errors() -> None:
    import httpx

    client = ProgressClient(
        base_url="http://progress",
        internal_api_key="secret",
        timeout_seconds=0.2,
    )

    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red")

    client._client = httpx.AsyncClient(
        base_url="http://progress",
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(
        client.record_attempt(
            user_id="usr_engine",
            level_id="progress-test",
            session_key="session-1",
            client_attempt_id="attempt-1",
            solved=False,
            selected_card_ids=["suspect-a"],
            elapsed_ms=10,
        )
    )

    assert result.recorded is False
    asyncio.run(client.close())
