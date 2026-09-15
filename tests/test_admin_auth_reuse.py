from fastapi.testclient import TestClient

from app.main import create_app


def test_approved_codenote_auth_key_can_login_more_than_once():
    calls = []

    def auth_checker(code: str):
        calls.append(code)
        return {"status": "approved", "token": "server-token"}

    app = create_app({
        "TESTING": True,
        "DATABASE_URL": "sqlite:///:memory:",
        "SEED_DEFAULT_STORES": False,
        "AUTH_CHECKER": auth_checker,
    })

    with TestClient(app) as client:
        first = client.post("/api/admin/login", json={"key": "active-server-key"})
        second = client.post("/api/admin/login", json={"key": "active-server-key"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == ["active-server-key", "active-server-key"]
