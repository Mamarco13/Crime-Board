"""Reglas de negocio puras del tablero.

Mantener aquí la lógica (sin FastAPI ni Redis) permite probarla de forma
determinista y reutilizarla si mañana se añade otro transporte (CLI, worker).

Diseño del MVP
--------------
* **Archivador**: la declaración del sospechoso se muestra tal cual y las
  fichas ocultas se recuperan escribiendo códigos, direcciones o palabras en el
  archivador del motor. El interrogatorio no entrega tarjetas por sí mismo.
* **Compatibilidad legada**: los niveles sin tarjetas ocultas conservan el
  reparto round-robin entre sospechosos y pistas.
* La acusación se compara como conjunto: el orden de las tarjetas no importa,
  pero deben coincidir exactamente con ``solution_card_ids``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .schemas import Card, CardType, LevelSnapshot


class MissingClueError(Exception):
    """El nivel no tiene tarjetas de pista que entregar."""

    def __init__(self, level_id: str) -> None:
        super().__init__(f"El nivel '{level_id}' no contiene pistas")
        self.level_id = level_id


@dataclass(frozen=True, slots=True)
class AccusationOutcome:
    """Resultado interno de una validación."""

    solved: bool
    feedback_message: str


def has_archive(level: LevelSnapshot) -> bool:
    """Indica si el nivel usa el archivador (tiene alguna ficha oculta)."""
    return any(card.hidden for card in level.cards)


def _cards_of_type(level: LevelSnapshot, card_type: CardType) -> list[Card]:
    """Tarjetas filtradas y ordenadas por id para un reparto estable."""
    return sorted(
        (card for card in level.cards if card.type is card_type),
        key=lambda card: card.id,
    )


def build_interrogation(
    level: LevelSnapshot,
    suspect: Card,
) -> tuple[str, Card | None]:
    """Genera la declaración del sospechoso y la pista que desbloquea.

    Devuelve ``(texto_para_el_jugador, tarjeta_de_pista)``. La función asume
    que ``suspect`` ya fue validado como tarjeta del nivel y de tipo SUSPECT.

    Con el archivador, la declaración es el texto que el jugador debe leer y
    ``tarjeta_de_pista`` es ``None``: las fichas se recuperan escribiendo en el
    archivador, no al interrogar. Los niveles antiguos sin fichas ocultas
    conservan el reparto automático como compatibilidad.
    """
    statement = suspect.description.strip() or suspect.title

    if has_archive(level):
        text = (
            f"{suspect.title} respira hondo antes de responder:\n"
            f"«{statement}»\n"
            "Anota lo que te haya llamado la atención: un código, una "
            "dirección o una palabra pueden abrir una ficha en el archivador."
        )
        return text, None

    clues = _cards_of_type(level, CardType.CLUE)
    if not clues:
        raise MissingClueError(level.level_id)

    suspects = _cards_of_type(level, CardType.SUSPECT)
    suspect_index = next(
        (index for index, item in enumerate(suspects) if item.id == suspect.id),
        0,
    )
    clue = clues[suspect_index % len(clues)]

    # Las pistas pueden no tener descripción; en ese caso se usa el título para
    # que el interrogatorio nunca devuelva un texto vacío.
    text = (
        f"{suspect.title} respira hondo antes de responder:\n"
        f"«{clue.description.strip() or clue.title}»\n"
        "Anota la declaración en el tablón: puede confirmar o descartar "
        "una hipótesis."
    )
    return text, clue


def evaluate_accusation(
    level: LevelSnapshot,
    selected_card_ids: list[str],
) -> AccusationOutcome:
    """Compara la acusación con la solución y redacta retroalimentación."""
    solution = set(level.solution_card_ids)
    selected = set(selected_card_ids)

    if selected == solution:
        return AccusationOutcome(
            solved=True,
            feedback_message=(
                "¡Caso resuelto! La acusación encaja con todas las pruebas. "
                "El tablón queda cerrado."
            ),
        )

    matched = len(selected & solution)
    if matched == 0:
        feedback = (
            "Ninguno de los elementos seleccionados encaja en la solución. "
        )
    else:
        feedback = (
            f"Solo {matched} de tus {len(selected)} elementos encajan en la "
            "solución. "
        )

    if len(selected) != len(solution):
        feedback += (
            "La acusación no reúne el número exacto de tarjetas que sostienen "
            "el caso. "
        )

    feedback += (
        "Vuelve a interrogar a los sospechosos y contrasta las pistas del "
        "tablón antes de acusar de nuevo."
    )
    return AccusationOutcome(solved=False, feedback_message=feedback)
