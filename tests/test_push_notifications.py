import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Store
from app.services import SERVICE_PURCHASE, SERVICE_SIMPLE


@pytest.fixture()
def client_and_sent_pushes():
    sent_pushes = []

    def auth_checker(code: str):
        return {"status": "approved", "token": "test-token"} if code == "test-key" else {"status": "denied"}

    def push_sender(subscription_info, payload, settings):
        sent_pushes.append({
            "subscription": subscription_info,
            "payload": payload,
            "subject": settings.vapid_subject,
        })
        return {"ok": True, "reason": "test_sender"}

    app = create_app({
        "TESTING": True,
        "DATABASE_URL": "sqlite:///:memory:",
        "SEED_DEFAULT_STORES": False,
        "AUTH_CHECKER": auth_checker,
        "PUSH_SENDER": push_sender,
        "VAPID_PUBLIC_KEY": "test-public-key",
        "VAPID_PRIVATE_KEY": "test-private-key",
        "VAPID_SUBJECT": "mailto:test@example.com",
    })

    with TestClient(app) as test_client:
        db = app.state.SessionLocal()
        db.add(Store(name="이천점"))
        db.add(Store(name="강남점"))
        db.commit()
        db.close()
        login_response = test_client.post("/api/admin/login", json={"key": "test-key"})
        assert login_response.status_code == 200
        yield test_client, sent_pushes


def sample_subscription(endpoint="https://example.push/sub-1"):
    return {
        "endpoint": endpoint,
        "keys": {
            "p256dh": "sample-p256dh-key",
            "auth": "sample-auth-key",
        },
    }


def test_push_subscribe_requires_admin():
    app = create_app({
        "TESTING": True,
        "DATABASE_URL": "sqlite:///:memory:",
        "SEED_DEFAULT_STORES": False,
    })
    with TestClient(app) as client:
        response = client.post(
            "/api/admin/push/subscribe",
            json={"store_id": 1, "subscription": sample_subscription()},
        )
    assert response.status_code == 401


def test_push_subscription_is_saved_for_selected_store(client_and_sent_pushes):
    client, _sent_pushes = client_and_sent_pushes
    stores = client.get("/api/stores").json()["stores"]
    icheon_id = next(store["id"] for store in stores if store["name"] == "이천점")

    response = client.post(
        "/api/admin/push/subscribe",
        json={"store_id": icheon_id, "subscription": sample_subscription()},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["store_id"] == icheon_id
    assert payload["enabled"] is True


def test_ticket_issue_sends_push_only_to_same_store(client_and_sent_pushes):
    client, sent_pushes = client_and_sent_pushes
    stores = client.get("/api/stores").json()["stores"]
    icheon_id = next(store["id"] for store in stores if store["name"] == "이천점")
    gangnam_id = next(store["id"] for store in stores if store["name"] == "강남점")

    client.post(
        "/api/admin/push/subscribe",
        json={"store_id": icheon_id, "subscription": sample_subscription("https://example.push/icheon")},
    )
    client.post(
        "/api/admin/push/subscribe",
        json={"store_id": gangnam_id, "subscription": sample_subscription("https://example.push/gangnam")},
    )

    ticket_response = client.post(
        "/api/tickets",
        json={"store_id": icheon_id, "service_type": SERVICE_SIMPLE},
    )

    assert ticket_response.status_code == 201
    assert len(sent_pushes) == 1
    assert sent_pushes[0]["subscription"]["endpoint"] == "https://example.push/icheon"
    assert sent_pushes[0]["payload"]["title"] == "새 대기번호 발급"
    assert "이천점" in sent_pushes[0]["payload"]["body"]
    assert "간단서비스" in sent_pushes[0]["payload"]["body"]
    assert "1번" in sent_pushes[0]["payload"]["body"]
    assert sent_pushes[0]["payload"]["data"]["store_id"] == icheon_id
    assert sent_pushes[0]["payload"]["data"]["service_type"] == SERVICE_SIMPLE


def test_push_subscription_can_move_to_new_selected_store(client_and_sent_pushes):
    client, sent_pushes = client_and_sent_pushes
    stores = client.get("/api/stores").json()["stores"]
    icheon_id = next(store["id"] for store in stores if store["name"] == "이천점")
    gangnam_id = next(store["id"] for store in stores if store["name"] == "강남점")
    subscription = sample_subscription("https://example.push/current-browser")

    first = client.post("/api/admin/push/subscribe", json={"store_id": icheon_id, "subscription": subscription})
    second = client.post("/api/admin/push/subscribe", json={"store_id": gangnam_id, "subscription": subscription})

    assert first.status_code == 200
    assert second.status_code == 200

    client.post("/api/tickets", json={"store_id": icheon_id, "service_type": SERVICE_PURCHASE})
    assert sent_pushes == []

    client.post("/api/tickets", json={"store_id": gangnam_id, "service_type": SERVICE_PURCHASE})
    assert len(sent_pushes) == 1
    assert sent_pushes[0]["subscription"]["endpoint"] == "https://example.push/current-browser"
    assert sent_pushes[0]["payload"]["data"]["store_id"] == gangnam_id


def test_unsubscribe_disables_push_endpoint(client_and_sent_pushes):
    client, sent_pushes = client_and_sent_pushes
    store_id = client.get("/api/stores").json()["stores"][0]["id"]
    endpoint = "https://example.push/off"

    client.post(
        "/api/admin/push/subscribe",
        json={"store_id": store_id, "subscription": sample_subscription(endpoint)},
    )
    response = client.post("/api/admin/push/unsubscribe", json={"endpoint": endpoint})

    assert response.status_code == 200
    assert response.json()["ok"] is True

    client.post("/api/tickets", json={"store_id": store_id, "service_type": SERVICE_SIMPLE})
    assert sent_pushes == []
