"""Purga best-effort del progreso desde User Service."""

from __future__ import annotations

import asyncio
from typing import ClassVar, Self

import httpx
import pytest
from app.config import Settings
from app.progress import purge_progress_data

USER_ID = "usr_purge"


def _settings(monkeypatch: pytest.MonkeyPatch, *, url: str | None) -> Settings:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("INTERNAL_API_KEY", "internal-test")
    if url is None:
        monkeypatch.delenv("PROGRESS_SERVICE_URL", raising=False)
    else:
        monkeypatch.setenv("PROGRESS_SERVICE_URL", url)
    return Settings.from_env()


class FakeResponse:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def raise_for_status(self) -> None:
        if self._error is not None:
            raise self._error


class FakeClient:
    instances: ClassVar[list[FakeClient]] = []

    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.response = FakeResponse()
        FakeClient.instances.append(self)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def delete(self, url: str, headers: dict[str, str]) -> FakeResponse:
        self.calls.append((url, headers))
        return self.response


def test_purge_is_skipped_without_progress_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, url=None)
    FakeClient.instances.clear()
    monkeypatch.setattr("app.progress.httpx.AsyncClient", FakeClient)

    assert asyncio.run(purge_progress_data(settings, USER_ID)) is False
    assert FakeClient.instances == []


def test_purge_calls_the_internal_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, url="http://progress:8000")
    FakeClient.instances.clear()
    monkeypatch.setattr("app.progress.httpx.AsyncClient", FakeClient)

    assert asyncio.run(purge_progress_data(settings, USER_ID)) is True

    client = FakeClient.instances[0]
    url, headers = client.calls[0]
    assert url == f"http://progress:8000/api/internal/progress/users/{USER_ID}"
    assert headers["X-Internal-Api-Key"] == "internal-test"
    assert client.timeout > 0


def test_purge_failure_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, url="http://progress:8000")
    FakeClient.instances.clear()
    monkeypatch.setattr("app.progress.httpx.AsyncClient", FakeClient)

    original_init = FakeClient.__init__

    def failing_init(self, *, timeout: float) -> None:
        original_init(self, timeout=timeout)
        self.response = FakeResponse(httpx.ConnectError("sin red"))

    monkeypatch.setattr(FakeClient, "__init__", failing_init)

    assert asyncio.run(purge_progress_data(settings, USER_ID)) is False
