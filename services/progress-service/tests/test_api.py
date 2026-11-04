"""Contratos HTTP del Progress Service con JWT y clave interna."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from app.main import app
from fastapi.testclient import TestClient
from jose import jwt

USER_ID = "usr_api"
LEVEL = "faro-002"


def _token(secret: str = "test-secret") -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": USER_ID,
            "username": "detective_api",
            "iss": "crime-board-user-service",
            "iat": now,
            "exp": now + timedelta(hours=1),
        },
        secret,
        algorithm="HS256",
    )


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_ISSUER", "crime-board-user-service")
    monkeypatch.setenv("INTERNAL_API_KEY", "internal-test")
    with TestClient(app) as test_client:
        yield test_client


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}"}


def _internal() -> dict[str, str]:
    return {"X-Internal-Api-Key": "internal-test"}


def test_public_endpoints_require_auth(client: TestClient) -> None:
    response = client.get("/api/progress/notes/general")
    assert response.status_code == 401

    bad = client.get(
        "/api/progress/notes/general",
        headers={"Authorization": "Bearer token-invalido"},
    )
    assert bad.status_code == 401


def test_notes_roundtrip_and_conflict(client: TestClient) -> None:
    response = client.get(
        "/api/progress/notes/general",
        headers=_auth(),
    )
    assert response.status_code == 200
    assert response.json() == {
        "scope": "general",
        "content": "",
        "version": 0,
        "updated_at": None,
    }

    created = client.put(
        "/api/progress/notes/faro-002",
        headers=_auth(),
        json={"content": "El farero miente.", "base_version": 0},
    )
    assert created.status_code == 200
    assert created.json()["version"] == 1

    stale = client.put(
        "/api/progress/notes/faro-002",
        headers=_auth(),
        json={"content": "Otra nota.", "base_version": 0},
    )
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["current"]["content"] == "El farero miente."
    assert detail["current"]["version"] == 1

    updated = client.put(
        "/api/progress/notes/faro-002",
        headers=_auth(),
        json={"content": "Nota corregida.", "base_version": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2


def test_workspace_roundtrip_and_conflict(client: TestClient) -> None:
    saved = client.put(
        f"/api/progress/cases/{LEVEL}/workspace",
        headers=_auth(),
        json={
            "crossed_out_ids": ["clue-botas", "clue-botas"],
            "accusation_ids": ["suspect-faro"],
            "base_version": 0,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["version"] == 1
    assert saved.json()["crossed_out_ids"] == ["clue-botas"]

    stale = client.put(
        f"/api/progress/cases/{LEVEL}/workspace",
        headers=_auth(),
        json={
            "crossed_out_ids": [],
            "accusation_ids": [],
            "base_version": 0,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current"]["version"] == 1


def test_sessions_attempts_and_stats_flow(client: TestClient) -> None:
    started = client.post(
        f"/api/progress/cases/{LEVEL}/sessions",
        headers=_auth(),
    )
    assert started.status_code == 200
    assert started.json()["is_replay"] is False

    missing_key = client.post(
        "/api/internal/progress/attempts",
        json={
            "user_id": USER_ID,
            "level_id": LEVEL,
            "client_attempt_id": "attempt-1",
            "solved": False,
            "selected_card_ids": ["suspect-faro"],
            "elapsed_ms": 120,
        },
    )
    assert missing_key.status_code == 401

    first = client.post(
        "/api/internal/progress/attempts",
        headers=_internal(),
        json={
            "user_id": USER_ID,
            "level_id": LEVEL,
            "session_key": "browser-1",
            "client_attempt_id": "attempt-1",
            "solved": False,
            "selected_card_ids": ["suspect-faro"],
            "elapsed_ms": 120,
        },
    )
    assert first.status_code == 201
    assert first.json()["first_solve"] is False
    assert first.json()["attempt_number"] == 1

    duplicate = client.post(
        "/api/internal/progress/attempts",
        headers=_internal(),
        json={
            "user_id": USER_ID,
            "level_id": LEVEL,
            "session_key": "browser-1",
            "client_attempt_id": "attempt-1",
            "solved": False,
            "selected_card_ids": ["suspect-faro"],
            "elapsed_ms": 120,
        },
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["recorded"] is False

    restarted = client.post(
        f"/api/progress/cases/{LEVEL}/restart",
        headers=_auth(),
        json={"session_id": "browser-1", "reason": "player_requested"},
    )
    assert restarted.status_code == 201
    assert restarted.json()["is_replay"] is True

    solved = client.post(
        "/api/internal/progress/attempts",
        headers=_internal(),
        json={
            "user_id": USER_ID,
            "level_id": LEVEL,
            "session_key": "browser-1",
            "client_attempt_id": "attempt-2",
            "solved": True,
            "selected_card_ids": ["suspect-faro", "weapon-cuerda"],
            "elapsed_ms": 220,
        },
    )
    assert solved.status_code == 201
    assert solved.json()["first_solve"] is True
    assert solved.json()["first_solve_attempts"] == 2

    state = client.get(
        f"/api/progress/cases/{LEVEL}/state",
        headers=_auth(),
    )
    assert state.status_code == 200
    body = state.json()
    assert body["stats"]["first_solve_attempts"] == 2
    assert body["stats"]["total_attempts"] == 2
    assert body["stats"]["replay_count"] == 0

    replay = client.post(
        f"/api/progress/cases/{LEVEL}/restart",
        headers=_auth(),
    )
    assert replay.status_code == 201
    assert replay.json()["is_replay"] is True

    state_after_replay = client.get(
        f"/api/progress/cases/{LEVEL}/state",
        headers=_auth(),
    )
    assert state_after_replay.json()["stats"]["replay_count"] == 1
    assert state_after_replay.json()["stats"]["first_solve_attempts"] == 2

    summary = client.get(
        "/api/progress/me/summary",
        headers=_auth(),
    )
    assert summary.status_code == 200
    assert summary.json()["cases_played"] == 1
    assert summary.json()["cases_solved"] == 1
    assert summary.json()["total_attempts"] == 2

    history = client.get(
        "/api/progress/me/cases",
        headers=_auth(),
        params={"status": "solved"},
    )
    assert history.status_code == 200
    assert history.json()["total"] == 1

    detail = client.get(
        f"/api/progress/me/cases/{LEVEL}",
        headers=_auth(),
    )
    assert detail.status_code == 200
    assert len(detail.json()["sessions"]) == 3
    assert len(detail.json()["attempts"]) == 2


def test_internal_purge_removes_everything(client: TestClient) -> None:
    client.put(
        "/api/progress/notes/general",
        headers=_auth(),
        json={"content": "Borrar", "base_version": 0},
    )
    client.post(
        "/api/internal/progress/attempts",
        headers=_internal(),
        json={
            "user_id": USER_ID,
            "level_id": LEVEL,
            "client_attempt_id": "attempt-1",
            "solved": False,
            "selected_card_ids": ["suspect-faro"],
            "elapsed_ms": 100,
        },
    )

    purged = client.delete(
        f"/api/internal/progress/users/{USER_ID}",
        headers=_internal(),
    )
    assert purged.status_code == 200
    deleted = purged.json()["deleted"]
    assert deleted["notes"] == 1
    assert deleted["attempts"] == 1
    assert deleted["sessions"] == 1
    assert deleted["stats"] == 1

    note = client.get(
        "/api/progress/notes/general",
        headers=_auth(),
    )
    assert note.status_code == 200
    assert note.json()["content"] == ""
    assert note.json()["version"] == 0


def test_invalid_identifiers_are_rejected(client: TestClient) -> None:
    bad_level = client.post(
        "/api/progress/cases/NO_VALIDO/sessions",
        headers=_auth(),
    )
    assert bad_level.status_code == 422

    bad_scope = client.get(
        "/api/progress/notes/NO_VALIDO",
        headers=_auth(),
    )
    assert bad_scope.status_code == 422
