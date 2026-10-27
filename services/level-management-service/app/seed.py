"""Datos de demostración para arrancar el tablero sin publicar nada a mano.

``SEED_DEMO_DATA=false`` desactiva la carga. El seed es idempotente: si el
nivel ya existe no se modifica, de modo que varias réplicas del servicio
pueden arrancar a la vez sin pisarse.
"""

from __future__ import annotations

import logging

from .models import CardCreate, CardType, Difficulty, LevelCreate
from .repository import LevelAlreadyExistsError, LevelRepository

logger = logging.getLogger(__name__)

DEMO_LEVEL = LevelCreate(
    level_id="tutorial-001",
    title="El último brindis en Villa Aurora",
    description=(
        "Durante la cena de fin de año, el anfitrión brindó y cayó sin vida. "
        "El reloj de la biblioteca se detuvo a las 23:15. Reconstruye la escena "
        "con las tarjetas del tablón y formula una acusación."
    ),
    difficulty=Difficulty.TUTORIAL,
    cards=[
        # ---------------------------- Sospechosos ----------------------------
        CardCreate(
            id="suspect-mayordomo",
            type=CardType.SUSPECT,
            title="Alfredo Vázquez",
            description=(
                "Mayordomo de la familia desde hace treinta años. Conoce cada "
                "rincón de la casa y preparó las copas del brindis."
            ),
        ),
        CardCreate(
            id="suspect-sobrina",
            type=CardType.SUSPECT,
            title="Lucía Ferrer",
            description=(
                "Sobrina y única heredera. Discutió con la víctima por el "
                "testamento esa misma tarde."
            ),
        ),
        CardCreate(
            id="suspect-socio",
            type=CardType.SUSPECT,
            title="Mauricio Salas",
            description=(
                "Socio comercial. Llegó tarde a la cena con barro en los "
                "zapatos y una carpeta que nunca soltó."
            ),
        ),
        # ------------------------------- Armas -------------------------------
        CardCreate(
            id="weapon-copa",
            type=CardType.WEAPON,
            title="Copa de cristal tallado",
            description="La copa del anfitrión, aún con restos de vino tinto.",
        ),
        CardCreate(
            id="weapon-pistola",
            type=CardType.WEAPON,
            title="Revólver de colección",
            description="Arma de salón expuesta en la vitrina, sin proyectiles.",
        ),
        CardCreate(
            id="weapon-atizador",
            type=CardType.WEAPON,
            title="Atizador de latón",
            description="Pesado atizador de la chimenea, limpio y en su sitio.",
        ),
        # ------------------------------ Lugares ------------------------------
        CardCreate(
            id="location-despacho",
            type=CardType.LOCATION,
            title="Despacho privado",
            description=(
                "Habitación cerrada con llave donde la víctima guardaba los "
                "documentos del testamento."
            ),
        ),
        CardCreate(
            id="location-biblioteca",
            type=CardType.LOCATION,
            title="Biblioteca",
            description="Sala principal de la cena, con un reloj de pared.",
        ),
        CardCreate(
            id="location-invernadero",
            type=CardType.LOCATION,
            title="Invernadero",
            description="Anexo acristalado, húmedo y lleno de macetas con tierra reciente.",
        ),
        # ------------------------------- Pistas -------------------------------
        CardCreate(
            id="clue-reloj",
            type=CardType.CLUE,
            title="Reloj detenido",
            description=(
                "Las agujas del reloj de pared marcan las 23:15, pero nadie "
                "recuerda haberlo oído dar la hora."
            ),
        ),
        CardCreate(
            id="clue-copa",
            type=CardType.CLUE,
            title="Residuo en la copa",
            description=(
                "En el fondo de la copa del anfitrión quedó un polvo blanco "
                "que no es azúcar."
            ),
        ),
        CardCreate(
            id="clue-huella",
            type=CardType.CLUE,
            title="Huella de barro",
            description=(
                "Una huella de barro seco cruza el pasillo desde el "
                "invernadero hasta el despacho."
            ),
        ),
        CardCreate(
            id="clue-coartada",
            type=CardType.CLUE,
            title="Coartada quebrada",
            description=(
                "El socio afirma que llegó a las 23:30, pero el taxi lo dejó "
                "a las 22:50."
            ),
        ),
    ],
    # Combinación ganadora: el mayordomo envenenó la copa y la llevó al
    # despacho. El reloj y la huella son pistas señuelo.
    solution_card_ids=[
        "suspect-mayordomo",
        "weapon-copa",
        "location-despacho",
        "clue-copa",
    ],
)


async def seed_demo_level(repository: LevelRepository) -> None:
    """Inserta el nivel de demostración si todavía no existe."""
    try:
        if await repository.get(DEMO_LEVEL.level_id) is not None:
            return
        await repository.create(DEMO_LEVEL)
        logger.info("Nivel de demostración '%s' cargado", DEMO_LEVEL.level_id)
    except LevelAlreadyExistsError:
        # Otra réplica ganó la carrera: el estado final es el mismo.
        logger.debug("El nivel de demostración ya había sido creado")
