"""Contrato de ``GET /api/ratings/me``: solo los votos del token."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from app.main import app
from fastapi.testclient import TestClient
from jose import jwt

USER_A = "usr_a"
USER_B = "usr_b"


def _token(user_id: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "username": f"detective_{user_id}",
            "iss": "crime-board-user-service",
            "iat": now,
            "exp": now + timedelta(hours=1),
        },
        "test-secret",
        algorithm="HS256",
    )


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id)}"}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_ISSUER", "crime-board-user-service")
    with TestClient(app) as test_client:
        yield test_client


def _rate(client: TestClient, user_id: str, level_id: str, stars: int) -> None:
    response = client.post(
        "/api/ratings/",
        headers=_auth(user_id),
        json={"level_id": level_id, "user_id": user_id, "stars": stars},
    )
    assert response.status_code == 200


def test_me_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/ratings/me")
    assert response.status_code == 401


def test_me_returns_only_the_authenticated_users_votes(
    client: TestClient,
) -> None:
    _rate(client, USER_A, "faro-002", 5)
    _rate(client, USER_A, "tutorial-001", 3)
    _rate(client, USER_B, "faro-002", 1)

    response = client.get("/api/ratings/me", headers=_auth(USER_A))

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert {row["level_id"] for row in body} == {"faro-002", "tutorial-001"}
    assert next(row for row in body if row["level_id"] == "faro-002")["stars"] == 5
    assert all("updated_at" in row for row in body)

    other = client.get("/api/ratings/me", headers=_auth(USER_B))
    assert [row["level_id"] for row in other.json()] == ["faro-002"]


def test_me_updates_the_existing_vote_instead_of_duplicating(
    client: TestClient,
) -> None:
    _rate(client, USER_A, "faro-002", 2)
    _rate(client, USER_A, "faro-002", 4)

    response = client.get("/api/ratings/me", headers=_auth(USER_A))

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["stars"] == 4


def test_me_supports_pagination(client: TestClient) -> None:
    for index in range(3):
        _rate(client, USER_A, f"level-{index}", 3)

    response = client.get(
        "/api/ratings/me",
        headers=_auth(USER_A),
        params={"limit": 1, "offset": 1},
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
