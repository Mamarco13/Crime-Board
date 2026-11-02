# Rating Service

Microservicio de valoraciones (1-5 estrellas) de los niveles de Crime Board.

- **Lectura pública** (incluidos invitados): media y número de votos de un
  nivel, individual o por lotes.
- **Escritura autenticada**: exige un JWT válido emitido por `user-service`.
  Un usuario solo puede tener un voto por nivel; volver a votar actualiza la
  puntuación anterior.
- **Persistencia**: PostgreSQL (`level_ratings`). Sin `DATABASE_URL` arranca con
  un repositorio en memoria para desarrollo local.

## API

| Método | Ruta | Auth | Descripción |
| --- | --- | --- | --- |
| `GET` | `/health` | No | Sonda de salud. |
| `POST` | `/api/ratings/` | Bearer JWT | Crea o actualiza el voto. Devuelve el resumen del nivel. |
| `GET` | `/api/ratings/levels/{level_id}` | No | Media redondeada a 1 decimal y total de votos (`0.0`, `0` si no hay). |
| `GET` | `/api/ratings/levels/batch?level_ids=a&level_ids=b` | No | Resumen de varios niveles en una petición. |
| `POST` | `/api/ratings/levels/batch` | No | Igual que el anterior, con cuerpo `{"level_ids": ["a", "b"]}`. |

### Ejemplo de voto

```json
POST /api/ratings/
Authorization: Bearer <jwt>
Content-Type: application/json

{
  "level_id": "faro-002",
  "user_id": "usr_abc123",
  "stars": 4
}
```

`user_id` debe coincidir con el `sub` del token; en caso contrario se responde
`403`. Los invitados reciben `401`.

## Variables de entorno

| Variable | Obligatoria | Descripción |
| --- | --- | --- |
| `DATABASE_URL` | No | URL SQLAlchemy/asyncpg. Sin ella, almacenamiento en memoria. |
| `JWT_SECRET_KEY` / `APP_SECRET_KEY` | Sí para votar | Clave compartida con `user-service`. |
| `JWT_ALGORITHM` | No | Por defecto `HS256`. |
| `JWT_ISSUER` | No | Por defecto `crime-board-user-service`. |
| `CORS_ALLOW_ORIGINS` | No | Lista separada por comas; por defecto `*`. |
| `LOG_LEVEL` | No | Por defecto `INFO`. |

## Desarrollo local

```bash
# Desde services/rating-service
python -m venv .venv
.venv/Scripts/activate        # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8003
```

Con Docker Compose, el servicio queda publicado en
`http://127.0.0.1:8003` (`RATING_SERVICE_HOST_PORT`):

```bash
docker compose up --build -d rating-service
curl http://127.0.0.1:8003/health
```

La imagen se construye en dos etapas, corre como `10001:10001` y declara un
`HEALTHCHECK` nativo contra `/health`.
