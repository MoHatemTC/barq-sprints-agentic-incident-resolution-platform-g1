from app.core.config import Settings


def mock_settings(**overrides: object) -> Settings:
    defaults = {
        "servicenow_instance_url": "https://dev00000.service-now.com",
        "servicenow_client_id": "test-cid",
        "servicenow_client_secret": "test-secret",
        "servicenow_username": "svc_user",
        "servicenow_password": "svc_pass",
        "servicenow_timeout_seconds": 5,
        "servicenow_token_expiry_buffer_seconds": 30,
    }
    defaults.update(overrides)
    return Settings(**defaults)
