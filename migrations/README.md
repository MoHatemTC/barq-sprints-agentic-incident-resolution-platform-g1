# PostgreSQL migrations

Alembic imports the canonical metadata from `app.db.models`. It does not load
application-wide settings or require ServiceNow, Qdrant, Redis, or FastAPI.

Set one of these before running migrations:

1. `BARQ_DATABASE_URL` (preferred), using a `postgresql+asyncpg://` URL.
2. `DATABASE_URL`.
3. `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, and
   `POSTGRES_PASSWORD`.

Migration integration tests require `BARQ_TEST_DATABASE_URL`. For safety, its
database name must start with `barq_s2_2_test`; the tests reset that database's
`public` schema before and after the round trip.

```powershell
$env:BARQ_DATABASE_URL = "postgresql+asyncpg://USER:PASSWORD@localhost/barq_s2_2_test"
$env:BARQ_TEST_DATABASE_URL = $env:BARQ_DATABASE_URL
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head
uv run pytest tests/db/test_migrations_postgresql.py -q
```
