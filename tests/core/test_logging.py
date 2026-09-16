import pytest
import structlog

from app.core.logging import REDACTED, configure_logging

SENTINEL_TOKEN = "AT-LIVE-TOKEN-9f3e2c1b4a5d"


@pytest.fixture(autouse=True)
def _reset_structlog():
    yield
    structlog.reset_defaults()


def test_exception_log_does_not_leak_token(capsys) -> None:
    configure_logging()
    logger = structlog.get_logger("test")

    def do_request():
        token = SENTINEL_TOKEN
        headers = {"Authorization": f"Bearer {token}"}  # noqa: F841
        raise ConnectionResetError("connection reset by peer")

    try:
        do_request()
    except ConnectionResetError:
        logger.exception("execution_log_write_failed", token=SENTINEL_TOKEN)

    output = capsys.readouterr().out
    assert SENTINEL_TOKEN not in output
    assert REDACTED in output
