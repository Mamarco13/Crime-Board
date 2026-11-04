# Crime Board Cloud-Native

## 1. Descripción del Dominio y Casos de Uso
Plataforma interactiva para resolver misterios lógicos...

## 2. Justificación NIST SP 800-145
- **Autoservicio bajo demanda:** ...
- **Acceso amplio a la red:** ...
- **Asignación común de recursos:** ...
- **Rápida elasticidad:** ...
- **Servicio medido:** ...

## 3. Mapeo de Actores NIST SP 500-292
- **Consumidor:** ...
- **Proveedor:** ...
- **Intermediario:** ...
- **Auditor:** ...
- **Operador:** ...

## 4. Arquitectura de Microservicios
(Añadir diagrama de la arquitectura)

## 5. Especificación API REST

### Level Management Service
- `POST /api/levels/`
  - **Descripción:** Publica un nuevo nivel de la comunidad.
  - **Payload:** JSON con `title`, `creator_id`, `clues` y `correct_connections`.
- `GET /api/levels/{level_id}`
  - **Descripción:** Obtiene los datos de un misterio para renderizar el tablero en HTML/JS.

### Playground Engine Service
- `POST /api/engine/validate`
  - **Descripción:** Evalúa el estado actual del tablero del jugador.
  - **Payload:** JSON con `level_id`, `selected_card_ids`, `session_id` y `client_attempt_id` opcionales.
  - **Respuesta:** JSON con `{"solved": boolean, "feedback_message": string, "progress": {...} | null}`.
  - Si llega un JWT válido, el intento se registra en `progress-service` con timeout corto y **fail-open**: una avería del progreso nunca bloquea el veredicto.

### Progress Service
- `GET/PUT /api/progress/notes/{scope}`: libreta general (`general`) o por caso, con versión optimista y `409` en conflicto.
- `POST /api/progress/cases/{level_id}/sessions`: inicia o reanuda la partida activa.
- `POST /api/progress/cases/{level_id}/restart`: cierra la partida y abre una repetición.
- `GET/PUT /api/progress/cases/{level_id}/state` y `/workspace`: sesión, tachados y selección de acusación.
- `GET /api/progress/me/summary` y `/api/progress/me/cases`: KPIs e historial del perfil.
- Endpoints internos con `X-Internal-Api-Key`: registro idempotente de intentos y purga al borrar cuenta.

### Perfil y libreta en la SPA
- La SPA sigue siendo un único `frontend/index.html`, sin build ni framework.
- El perfil agrega datos con `Promise.allSettled`: progreso, valoraciones propias y casos diseñados.
- La libreta funciona en local sin sesión y se sincroniza por versión al iniciar sesión. Los conflictos muestran ambas versiones y nunca borran texto sin confirmación.
- El reinicio de un caso autenticado se coordina con `progress-service`; la primera resolución registrada es inmutable.

### Puesta en marcha local
```bash
cp .env.example .env
docker compose up --build -d
# Tablero: sirve frontend/ con un servidor estático, p. ej. python -m http.server 8123
# Progress Service: http://127.0.0.1:8004/docs
```
Sin `DATABASE_URL`, cada servicio arranca con repositorio en memoria para desarrollo y pruebas. En producción, `docker compose` inyecta PostgreSQL y los secretos compartidos.