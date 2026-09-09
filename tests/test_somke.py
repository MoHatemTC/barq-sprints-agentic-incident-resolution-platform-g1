from app.main import app


def test_app_is_created() -> None:
    assert app is not None
