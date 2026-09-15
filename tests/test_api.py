import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Store
from app.services import SERVICE_SIMPLE


@pytest.fixture()
def client():
    app = create_app({
        "TESTING": True,
        "DATABASE_URL": "sqlite:///:memory:",
        "ADMIN_KEY": "test-key",
        "SEED_DEFAULT_STORES": False,
    })
    with TestClient(app) as test_client:
        db = app.state.SessionLocal()
        db.add(Store(name="간단서비스 코너"))
        db.add(Store(name="구매상담 코너"))
        db.commit()
        db.close()
        yield test_client


def auth_headers():
    return {"X-Admin-Key": "test-key"}


def test_store_search_create_update_delete(client):
    search_response = client.get("/api/stores?search=간단")
    assert search_response.status_code == 200
    assert [store["name"] for store in search_response.json()["stores"]] == ["간단서비스 코너"]

    create_response = client.post("/api/admin/stores", json={"name": "동해점"}, headers=auth_headers())
    assert create_response.status_code == 201
    created = create_response.json()["store"]
    assert created["name"] == "동해점"

    update_response = client.put(f"/api/admin/stores/{created['id']}", json={"name": "동해 센터"}, headers=auth_headers())
    assert update_response.status_code == 200
    assert update_response.json()["store"]["name"] == "동해 센터"

    delete_response = client.delete(f"/api/admin/stores/{created['id']}", headers=auth_headers())
    assert delete_response.status_code == 200

    customer_search = client.get("/api/stores?search=동해")
    assert customer_search.json()["stores"] == []


def test_ticket_call_and_state_api(client):
    store_id = client.get("/api/stores").json()["stores"][0]["id"]

    ticket_response = client.post("/api/tickets", json={"store_id": store_id, "service_type": SERVICE_SIMPLE})
    assert ticket_response.status_code == 201
    assert ticket_response.json()["ticket"]["ticket_number"] == 1

    call_response = client.post(
        "/api/admin/call",
        json={"store_id": store_id, "service_type": SERVICE_SIMPLE, "call_type": "normal"},
        headers=auth_headers(),
    )
    assert call_response.status_code == 200
    assert call_response.json()["call"]["ticket_number"] == 1

    state_response = client.get(f"/api/state/{store_id}")
    state = state_response.json()["state"]
    assert state["services"][SERVICE_SIMPLE]["current_number"] == 1
    assert state["services"][SERVICE_SIMPLE]["waiting_count"] == 0


def test_backup_zip_and_restore_roundtrip(client):
    create_response = client.post("/api/admin/stores", json={"name": "백업매장"}, headers=auth_headers())
    assert create_response.status_code == 201

    backup_response = client.get("/api/admin/backup", headers=auth_headers())
    assert backup_response.status_code == 200
    assert backup_response.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(backup_response.content)) as archive:
        assert "data.json" in archive.namelist()

    created_id = create_response.json()["store"]["id"]
    client.delete(f"/api/admin/stores/{created_id}", headers=auth_headers())
    assert client.get("/api/stores?search=백업매장").json()["stores"] == []

    restore_response = client.post(
        "/api/admin/restore",
        files={"file": ("backup.zip", backup_response.content, "application/zip")},
        headers=auth_headers(),
    )
    assert restore_response.status_code == 200
    assert client.get("/api/stores?search=백업매장").json()["stores"][0]["name"] == "백업매장"


def test_backup_contains_only_store_categories(client):
    store_id = client.get('/api/stores').json()['stores'][0]['id']
    ticket_response = client.post('/api/tickets', json={'store_id': store_id, 'service_type': SERVICE_SIMPLE})
    assert ticket_response.status_code == 201

    backup_response = client.get('/api/admin/backup', headers=auth_headers())
    assert backup_response.status_code == 200

    with zipfile.ZipFile(io.BytesIO(backup_response.content)) as archive:
        data = __import__('json').loads(archive.read('data.json').decode('utf-8'))

    assert list(data.keys()) == ['version', 'exported_at', 'stores']
    assert data['stores']


def test_ticket_numbers_and_calls_are_isolated_by_store(client):
    stores = client.get('/api/stores').json()['stores']
    first_store_id = stores[0]['id']
    second_store_id = stores[1]['id']

    first_ticket = client.post('/api/tickets', json={'store_id': first_store_id, 'service_type': SERVICE_SIMPLE})
    second_ticket = client.post('/api/tickets', json={'store_id': second_store_id, 'service_type': SERVICE_SIMPLE})
    assert first_ticket.json()['ticket']['ticket_number'] == 1
    assert second_ticket.json()['ticket']['ticket_number'] == 1

    call_response = client.post(
        '/api/admin/call',
        json={'store_id': first_store_id, 'service_type': SERVICE_SIMPLE, 'call_type': 'normal'},
        headers=auth_headers(),
    )
    assert call_response.status_code == 200

    first_state = client.get(f'/api/state/{first_store_id}').json()['state']
    second_state = client.get(f'/api/state/{second_store_id}').json()['state']
    assert first_state['services'][SERVICE_SIMPLE]['current_number'] == 1
    assert first_state['services'][SERVICE_SIMPLE]['waiting_count'] == 0
    assert second_state['services'][SERVICE_SIMPLE]['current_number'] is None
    assert second_state['services'][SERVICE_SIMPLE]['waiting_count'] == 1
