"""Datos de demostración para arrancar el tablero sin publicar nada a mano.

``SEED_DEMO_DATA=false`` desactiva la carga. El seed es idempotente: si el
nivel ya existe no se modifica, de modo que varias réplicas del servicio
pueden arrancar a la vez sin pisarse.

El caso demo usa el **archivador**: solo los tres sospechosos están visibles
desde el principio. Las otras trece fichas (armas, lugares y pistas) están
ocultas y se recuperan tecleando alguno de sus alias, que aparecen en las
declaraciones de los sospechosos o en las descripciones ya reveladas.
"""

from __future__ import annotations

import logging

from .models import (
    CardCreate,
    CardType,
    Difficulty,
    LevelCreate,
)
from .repository import LevelAlreadyExistsError, LevelRepository

logger = logging.getLogger(__name__)

DEMO_LEVEL = LevelCreate(
    level_id="tutorial-001",
    title="El último brindis en Villa Aurora",
    description=(
        "Durante la cena de fin de año, el anfitrión brindó y cayó sin vida. "
        "El reloj de la biblioteca se detuvo a las 23:15. Interroga a los "
        "sospechosos, busca los términos que mencionan en el archivador y "
        "reconstruye la escena antes de acusar."
    ),
    difficulty=Difficulty.TUTORIAL,
    reveal_solution_composition=True,
    cards=[
        # ------------------- Tarjetas visibles (3) -------------------
        CardCreate(
            id="suspect-mayordomo",
            type=CardType.SUSPECT,
            title="Alfredo Vázquez",
            description=(
                "«Serví el champán a las 23:00 y dejé la copa de cristal junto "
                "a la biblioteca. Cuando volví, el anfitrión ya no respiraba. "
                "El despacho privado estaba cerrado con llave; nadie entró "
                "hasta que forzamos la puerta.»"
            ),
        ),
        CardCreate(
            id="suspect-sobrina",
            type=CardType.SUSPECT,
            title="Lucía Ferrer",
            description=(
                "«Discutimos por el testamento, sí. Pero esa tarde yo estaba "
                "en el invernadero, entre las macetas. La receta que "
                "encontraron es del Dr. Sáenz: su consultorio está en Calle "
                "Falsa 123, y el vial era para mi migraña.»"
            ),
        ),
        CardCreate(
            id="suspect-socio",
            type=CardType.SUSPECT,
            title="Mauricio Salas",
            description=(
                "«Llegué en taxi a las 22:50, no a las 23:30. Traía barro en "
                "los zapatos porque el taxi se quedó atascado junto al "
                "invernadero. El reloj de pared ya marcaba las 23:15 cuando "
                "entré; nadie lo oyó dar la hora.»"
            ),
        ),
        # ------------------- Armas ocultas (3) -------------------
        CardCreate(
            id="weapon-copa",
            type=CardType.WEAPON,
            title="Copa de cristal tallado",
            description=(
                "La copa del anfitrión, aún con restos de vino tinto y una "
                "leve película blanquecina en el fondo."
            ),
            hidden=True,
            aliases=["copa", "copa de cristal", "copa de champán", "champán"],
        ),
        CardCreate(
            id="weapon-pistola",
            type=CardType.WEAPON,
            title="Revólver de colección",
            description=(
                "Arma de salón expuesta en la vitrina, sin proyectiles y con "
                "la culata cubierta de polvo."
            ),
            hidden=True,
            aliases=["revólver", "pistola", "vitrina"],
        ),
        CardCreate(
            id="weapon-atizador",
            type=CardType.WEAPON,
            title="Atizador de latón",
            description=(
                "Pesado atizador de la chimenea, limpio y en su sitio. "
                "Demasiado limpio para la ceniza acumulada alrededor."
            ),
            hidden=True,
            aliases=["atizador", "chimenea", "latón"],
        ),
        # ------------------ Lugares ocultos (4) ------------------
        CardCreate(
            id="location-despacho",
            type=CardType.LOCATION,
            title="Despacho privado",
            description=(
                "Habitación cerrada con llave donde la víctima guardaba los "
                "documentos del testamento. La cerradura no presenta fuerza."
            ),
            hidden=True,
            aliases=["despacho", "despacho privado", "documentos", "testamento"],
        ),
        CardCreate(
            id="location-biblioteca",
            type=CardType.LOCATION,
            title="Biblioteca",
            description=(
                "Sala principal de la cena, presidida por un reloj de pared "
                "detenido a las 23:15."
            ),
            hidden=True,
            aliases=["biblioteca", "reloj de pared"],
        ),
        CardCreate(
            id="location-invernadero",
            type=CardType.LOCATION,
            title="Invernadero",
            description=(
                "Anexo acristalado, húmedo y lleno de macetas con tierra "
                "reciente. Una de ellas está volcada."
            ),
            hidden=True,
            aliases=["invernadero", "macetas", "turba"],
        ),
        CardCreate(
            id="location-casa-dentista",
            type=CardType.LOCATION,
            title="Casa del dentista",
            description=(
                "Chalet en el 123 de Calle Falsa. El Dr. Sáenz atiende allí "
                "fuera del horario de consulta y guarda muestras en el sótano."
            ),
            hidden=True,
            aliases=[
                "calle falsa 123",
                "casa del dentista",
                "consultorio",
                "dr saenz",
                "dentista",
            ],
        ),
        # ------------------- Pistas ocultas (6) -------------------
        CardCreate(
            id="clue-reloj",
            type=CardType.CLUE,
            title="Reloj detenido",
            description=(
                "Las agujas del reloj de pared marcan las 23:15, pero nadie "
                "recuerda haberlo oído dar la hora."
            ),
            hidden=True,
            aliases=["reloj", "reloj detenido", "23:15"],
        ),
        CardCreate(
            id="clue-copa",
            type=CardType.CLUE,
            title="Residuo en la copa",
            description=(
                "En el fondo de la copa del anfitrión quedó un polvo blanco "
                "que no es azúcar. El laboratorio detecta un sedante."
            ),
            hidden=True,
            aliases=["residuo", "polvo blanco", "veneno", "sedante"],
        ),
        CardCreate(
            id="clue-huella",
            type=CardType.CLUE,
            title="Huella de barro",
            description=(
                "Una huella de barro seco cruza el pasillo desde el "
                "invernadero hasta el despacho."
            ),
            hidden=True,
            aliases=["huella", "barro", "huella de barro"],
        ),
        CardCreate(
            id="clue-coartada",
            type=CardType.CLUE,
            title="Coartada quebrada",
            description=(
                "El socio afirma que llegó a las 23:30, pero el taxi lo dejó "
                "a las 22:50 según el registro de la verja."
            ),
            hidden=True,
            aliases=["coartada", "taxi", "22:50"],
        ),
        CardCreate(
            id="clue-receta",
            type=CardType.CLUE,
            title="Receta médica",
            description=(
                "Una receta firmada por el Dr. Sáenz, dentista, con consulta "
                "en Calle Falsa 123. El vial está medio vacío."
            ),
            hidden=True,
            aliases=["receta", "receta médica", "vial", "membrete"],
        ),
        CardCreate(
            id="clue-tierra",
            type=CardType.CLUE,
            title="Tierra de invernadero",
            description=(
                "La huella contiene turba y perlita, la misma mezcla de las "
                "macetas del invernadero."
            ),
            hidden=True,
            aliases=["tierra", "perlita", "tierra de invernadero"],
        ),
    ],
    # Combinación ganadora: el mayordomo sedó la copa y la llevó al despacho.
    # El reloj y la huella son pistas señuelo.
    solution_card_ids=[
        "suspect-mayordomo",
        "weapon-copa",
        "location-despacho",
        "clue-copa",
    ],
)


# Variante difícil del mismo expediente: oculta la composición en el dosier y
# activa el feedback estricto del motor. Sirve para probar #6 sin publicar un
# caso nuevo desde el editor.
HARD_DEMO_LEVEL = DEMO_LEVEL.model_copy(
    update={
        "level_id": "tutorial-002",
        "title": "El último brindis · edición difícil",
        "description": (
            "El mismo misterio de Villa Aurora, pero sin la composición de la "
            "solución en el dosier. El motor no revelará coincidencias "
            "parciales: tendrás que reconstruir la acusación completa."
        ),
        "difficulty": Difficulty.HARD,
        "reveal_solution_composition": False,
    }
)

DEMO_LEVELS = (DEMO_LEVEL, HARD_DEMO_LEVEL)


async def seed_demo_level(repository: LevelRepository) -> None:
    """Inserta los niveles de demostración si todavía no existen."""
    for level in DEMO_LEVELS:
        try:
            if await repository.get(level.level_id) is not None:
                continue
            await repository.create(level)
            logger.info("Nivel de demostración '%s' cargado", level.level_id)
        except LevelAlreadyExistsError:
            # Otra réplica ganó la carrera: el estado final es el mismo.
            logger.debug("El nivel de demostración ya había sido creado")
