"""Pruebas de los endpoints del archivador y del interrogatorio."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from app.main import app
from app.schemas import CardType, LevelSnapshot
from app.store import InMemoryJsonStore
from fastapi.testclient import TestClient


def _archive_level() -> LevelSnapshot:
    return LevelSnapshot(
        level_id="archive-test",
        title="Prueba del archivador",
        cards=[
            {
                "id": "suspect-a",
                "type": "SUSPECT",
                "title": "Sospechoso",
                "description": (
                    "Dice que dejó la copa en la biblioteca y que la receta "
                    "procede de Calle Falsa 123."
                ),
                "aliases": ["mayordomo"],
            },
            {
                "id": "location-casa",
                "type": "LOCATION",
                "title": "Casa del dentista",
                "description": "Chalet en el 123.",
                "hidden": True,
                "aliases": ["calle falsa 123", "casa del dentista"],
            },
            {
                "id": "weapon-copa",
                "type": "WEAPON",
                "title": "Copa de cristal",
                "description": "La copa del brindis.",
                "hidden": True,
                "aliases": ["copa", "champán"],
            },
        ],
        solution_card_ids=["suspect-a", "weapon-copa"],
    )


def _legacy_level() -> LevelSnapshot:
    return LevelSnapshot(
        level_id="legacy-test",
        title="Nivel legado",
        cards=[
            {
                "id": "suspect-a",
                "type": "SUSPECT",
                "title": "Sospechoso",
                "description": "Dice que estaba en casa.",
            },
            {
                "id": "clue-receta",
                "type": "CLUE",
                "title": "Receta",
                "description": "Consulta en Calle Falsa 123.",
            },
        ],
        solution_card_ids=["suspect-a", "clue-receta"],
    )


class FakeCatalog:
    def __init__(self, level: LevelSnapshot) -> None:
        self.level = level

    async def get_level(self, level_id: str) -> LevelSnapshot:
        if level_id != self.level.level_id:
            from app.catalog import LevelNotFoundError

            raise LevelNotFoundError(level_id)
        return self.level


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        app.state.catalog = FakeCatalog(_archive_level())
        app.state.store = InMemoryJsonStore()
        yield test_client


def _archive(
    client: TestClient,
    query: str,
    *,
    level_id: str = "archive-test",
    session_id: str = "session-1",
) -> dict[str, Any]:
    response = client.post(
        "/api/engine/archive",
        json={"level_id": level_id, "query": query, "session_id": session_id},
    )
    return {"status": response.status_code, **response.json()}


def test_archive_recovers_hidden_card(client: TestClient) -> None:
    body = _archive(client, "Calle Falsa, 123.")

    assert body["status"] == 200
    assert body["found"] is True
    assert body["card"]["id"] == "location-casa"
    assert body["card"]["type"] == CardType.LOCATION.value
    assert body["already_unlocked"] is False
    assert body["score"] == 1.0
    assert "hidden" not in body["card"]
    assert "aliases" not in body["card"]


def test_archive_is_idempotent_per_session(client: TestClient) -> None:
    first = _archive(client, "copa")
    second = _archive(client, "copa")

    assert first["already_unlocked"] is False
    assert second["found"] is True
    assert second["already_unlocked"] is True
    assert "ya está sobre el tablero" in second["message"]


def test_archive_accepts_typos(client: TestClient) -> None:
    body = _archive(client, "cas del dentista")

    assert body["found"] is True
    assert body["card"]["id"] == "location-casa"
    assert body["score"] >= 0.82


def test_archive_reports_visible_card_as_already_on_board(
    client: TestClient,
) -> None:
    body = _archive(client, "mayordomo")

    assert body["found"] is True
    assert body["card"]["id"] == "suspect-a"
    assert body["already_unlocked"] is True
    assert "ya está sobre el tablero" in body["message"]


def test_archive_returns_not_found_message(client: TestClient) -> None:
    body = _archive(client, "la cocina")

    assert body["status"] == 200
    assert body["found"] is False
    assert body["card"] is None
    assert body["score"] is None
    assert "Registro no encontrado" in body["message"]


def test_archive_rejects_unknown_level(client: TestClient) -> None:
    body = _archive(client, "copa", level_id="otro-nivel")
    assert body["status"] == 404


def test_interrogate_with_archive_does_not_unlock_cards(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/engine/interrogate",
        json={
            "level_id": "archive-test",
            "suspect_card_id": "suspect-a",
            "session_id": "archive-session",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["new_card_unlocked"] is None
    assert "archivador" in body["unlocked_clue_text"]
    assert "Calle Falsa 123" in body["unlocked_clue_text"]


def test_interrogate_keeps_legacy_flow_without_hidden_cards(
    client: TestClient,
) -> None:
    app.state.catalog = FakeCatalog(_legacy_level())

    response = client.post(
        "/api/engine/interrogate",
        json={
            "level_id": "legacy-test",
            "suspect_card_id": "suspect-a",
            "session_id": "legacy-session",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["new_card_unlocked"]["id"] == "clue-receta"
    assert body["new_card_unlocked"]["type"] == CardType.CLUE.value
