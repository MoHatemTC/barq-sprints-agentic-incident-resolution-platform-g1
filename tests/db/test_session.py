"""Unit tests for lazy async database construction."""

from dataclasses import dataclass

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import build_database_url, create_database_engine, create_session_factory


@dataclass
class StubPostgreSQLSettings:
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: SecretStr | None


def test_build_database_url_uses_asyncpg_and_escapes_credentials() -> None:
    settings = StubPostgreSQLSettings(
        postgres_host="db.internal",
        postgres_port=5544,
        postgres_db="barq state",
        postgres_user="service@barq",
        postgres_password=SecretStr("not/a:url?secret"),
    )

    url = build_database_url(settings)

    assert url.drivername == "postgresql+asyncpg"
    assert url.host == "db.internal"
    assert url.port == 5544
    assert url.database == "barq state"
    assert url.username == "service@barq"
    assert url.password == "not/a:url?secret"
    assert "not/a:url?secret" not in str(url)


async def test_engine_and_session_factory_are_lazy() -> None:
    engine = create_database_engine("postgresql+asyncpg://user:pass@127.0.0.1:1/barq")
    try:
        assert isinstance(engine, AsyncEngine)
        factory = create_session_factory(engine)
        session = factory()
        try:
            assert session.bind is engine
        finally:
            await session.close()
    finally:
        await engine.dispose()
