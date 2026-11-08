# Archivador — término libre → ficha oculta → tablero

Mecánica de OpenCrime que sustituye el desbloqueo automático de pistas. El
diseñador marca una tarjeta como oculta y declara sus **alias** (códigos,
direcciones o palabras). El jugador lee una declaración, abre el archivador,
teclea un término y, si coincide con una ficha oculta, esta aparece en el
tablero. La comprobación es determinista: **no hay IA generativa ni llamadas a
modelos externos**.

## 1. Flujo de juego

1. El jugador pulsa **Interrogar** en un sospechoso y lee su declaración.
2. La declaración puede contener un código, una dirección, un nombre o un
   objeto: ese es el término que abre una ficha.
3. El jugador pulsa **Archivador** (o el CTA del modal de declaración), escribe
   el término y lo envía.
4. El motor compara la consulta con los alias de las fichas ocultas del nivel.
   - Acierto: devuelve la ficha, se inserta en el tablero y queda registrada en
     el archivo y en el historial de búsquedas.
   - Fallo: responde **«Registro no encontrado»**; no hay penalización y el
     jugador puede seguir buscando.
5. Si la ficha ya estaba sobre el tablero, responde **«Esa ficha ya está sobre
   el tablero.»** y no la duplica.

Las fichas recuperadas también se pueden leer desde el tablero con **Leer**.

## 2. Configuración de un nivel

Cada tarjeta de `POST /api/levels/` admite `hidden` y `aliases`:

```json
{
  "level_id": "tutorial-001",
  "title": "El caso del mayordomo",
  "description": "…",
  "difficulty": "easy",
  "cards": [
    {
      "id": "suspect-mayordomo",
      "type": "SUSPECT",
      "title": "Alfredo Vázquez",
      "description": "Dejé la copa de cristal junto a la biblioteca…"
    },
    {
      "id": "weapon-copa",
      "type": "WEAPON",
      "title": "Copa de cristal tallado",
      "description": "Aún conserva restos de vino y un polvo blanquecino.",
      "hidden": true,
      "aliases": ["copa", "copa de cristal", "champán"]
    },
    {
      "id": "clue-reloj",
      "type": "CLUE",
      "title": "Reloj detenido",
      "description": "Las agujas marcan las 23:15.",
      "hidden": true,
      "aliases": ["reloj", "23:15"]
    }
  ],
  "solution_card_ids": ["suspect-mayordomo", "weapon-copa"]
}
```

Reglas de validación al publicar:

- `aliases` se normaliza (minúsculas, sin acentos, sin puntuación) y no puede
  contener duplicados dentro de una tarjeta.
- Un alias no puede repetirse entre tarjetas del mismo nivel: dos fichas no
  deben abrirse con el mismo término.
- Una tarjeta `hidden: true` necesita al menos un alias.
- El nivel necesita al menos una tarjeta visible para abrir el tablón.
- `solution_card_ids` puede referenciar tarjetas visibles u ocultas; todas deben
  existir.

## 3. Contratos HTTP

### Catálogo público

`GET /api/levels/{level_id}` devuelve solo las tarjetas visibles y el total del
expediente:

```json
{
  "level_id": "tutorial-001",
  "cards": [
    {
      "id": "suspect-mayordomo",
      "type": "SUSPECT",
      "title": "Alfredo Vázquez",
      "description": "…"
    }
  ],
  "card_count": 16
}
```

Nunca incluye `hidden`, `aliases` ni `solution_card_ids`.

### Canal interno

`GET /api/levels/{level_id}/internal` (cabecera `X-Internal-Api-Key`) devuelve
`LevelAdmin`, con todas las tarjetas, `hidden`, `aliases` y la solución. Solo lo
consume el Playground Engine.

### Archivador

`POST /api/engine/archive`

```json
{
  "level_id": "tutorial-001",
  "query": "Calle Falsa, 123",
  "session_id": "abc123"
}
```

Respuesta en acierto:

```json
{
  "found": true,
  "card": {
    "id": "location-casa-dentista",
    "type": "LOCATION",
    "title": "Casa del dentista",
    "description": "…"
  },
  "already_unlocked": false,
  "message": "Registro recuperado: Casa del dentista.",
  "score": 1.0
}
```

Respuesta sin coincidencia:

```json
{
  "found": false,
  "card": null,
  "already_unlocked": false,
  "message": "Registro no encontrado. Revisa el código, la dirección o la palabra y vuelve a intentarlo.",
  "score": null
}
```

Errores:

| Código | Motivo |
| --- | --- |
| `404` | El nivel no existe en el catálogo. |
| `422` | Payload inválido (consulta vacía o ids con formato incorrecto). |

### Interrogatorio

`POST /api/engine/interrogate` mantiene el contrato anterior. Si el nivel tiene
fichas ocultas, devuelve la declaración del sospechoso y
`new_card_unlocked: null`. Si no las tiene, conserva el reparto round-robin como
compatibilidad con los niveles antiguos.

## 4. Emparejamiento determinista

Implementación Python: `services/playground-engine-service/app/archive.py`.
Implementación JS: `matchLocalArchive` en `frontend/index.html`.

Ambas aplican las mismas reglas, de mayor a menor confianza:

1. **Normalización**: minúsculas, sin acentos, sin puntuación y con espacios
   colapsados.
2. **Igualdad exacta** normalizada → `1.0`.
3. **La consulta contiene el alias** como frase completa → `0.95`. Así se acepta
   *«creo que fue en la casa del dentista»*.
4. **El alias contiene la consulta** como frase completa → `0.85`. Así
   *«dentista»* abre la ficha *«casa del dentista»*.
5. **Similitud de secuencia** para tolerar erratas. Python usa
   `difflib.SequenceMatcher.ratio()`; JS replica el mismo ratio
   (`2 × caracteres comunes / total`) con una subsecuencia común más larga. El
   umbral es `0.82`.

Las consultas o alias de menos de 4 caracteres nunca usan similitud: `123` no
debe abrir fichas por parecido accidental con `1234`.

Desempate: mayor puntuación, después alias más largo y, por último, el orden en
que las tarjetas se declararon en el nivel.

## 5. Estado efímero

Claves en Redis (o memoria si Redis no está disponible), con el TTL de
`INTERROGATION_TTL_SECONDS`:

- `open-crime:state:archive:{level}:{session}:{card}`: ficha ya recuperada en
  esa partida.

No se guarda la consulta libre en el backend. El frontend conserva el historial
local de búsquedas (con su resultado) en `localStorage`.

## 6. Modo simulación y modo real

- **Simulación**: el frontend valida con el matcher JS usando las fichas ocultas
  y sus alias, que sí están en el nivel mock.
- **Real**: el frontend llama a `/api/engine/archive`. El nivel público no
  incluye fichas ocultas ni alias, así que, si el motor no está disponible,
  **no se puede validar la búsqueda en local**; la UI muestra un error en lugar
  de simular un resultado.
- **Fallback local**: solo se usa cuando el nivel cargado tiene definiciones
  locales con alias (por ejemplo, tras caer el backend y recuperar el mock).
- **Compatibilidad legada**: los niveles sin fichas ocultas siguen usando el
  flujo de interrogatorio clásico con reparto round-robin.

## 7. Pruebas

```powershell
# Matcher, endpoints y corpus compartido (Python)
cd services/playground-engine-service
python -m pytest -q

# Contrato del catálogo
cd services/level-management-service
python -m pytest -q

# Lint
python -m ruff check services
```

El corpus `services/playground-engine-service/tests/fixtures/archive_corpus.json`
es la especificación compartida: los mismos casos deben pasar en Python y en el
matcher JS. Incluye aciertos exactos, frases largas, erratas, términos cortos y
falsos positivos.

## 8. Base de datos existente

`Base.metadata.create_all` crea la tabla `cards` con las columnas nuevas, pero no
ejecuta `ALTER TABLE` sobre tablas ya existentes. Un volumen de PostgreSQL creado
antes de este cambio **no tendrá** `hidden` ni `aliases`, y el seed es
idempotente: si `tutorial-001` ya existe, no se actualiza.

Para refrescar solo el nivel de demostración en local, usa las credenciales de
tu `.env`:

```powershell
docker compose exec postgres psql -U <POSTGRES_USER> -d <POSTGRES_DB> -c "DELETE FROM levels WHERE level_id = 'tutorial-001';"
docker compose restart level-management-service
```

La relación en cascada elimina también sus tarjetas. Como alternativa
destructiva en un entorno local:

```powershell
docker compose down -v
docker compose up --build -d
```

En un entorno con datos reales, la evolución del esquema debe pasar a migraciones
(Alembic) antes de añadir más columnas a tablas existentes.

## 9. Alcance y siguientes fases

Incluido en esta iteración:

- Columnas `hidden` y `aliases` en `cards`, con validación al publicar.
- Catálogo público saneado y canal interno completo.
- Endpoint `/api/engine/archive`, matcher determinista y estado por sesión.
- Flujo jugable en simulación y en modo real, con persistencia local.
- Editor básico de fichas ocultas y alias al crear un caso.
- Corpus de pruebas compartido Python/JS.

Pendiente:

- Editor visual avanzado (Fase 3): pestaña dedicada con chips de alias,
  previsualización de coincidencias y probador local del archivador.
- Pistas escalonadas o sugerencias tras varios fallos, solo si el playtesting lo
  justifica.
