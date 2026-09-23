"""실제 Firebase 호출 없이 인증, 격리, 원자적 outbox, 재시도/해지를 검증한다."""
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, select, text

from app.main import create_app
from app.models import Store, Ticket, utc_now
from app.mobile_push import MobileDevice, MobilePushDelivery, process_mobile_outbox, load_mobile_settings, _queue_for_devices

TOKEN = "fake-fcm-token-for-unit-tests-only-0001"
INSTALL = "unit-test-installation-00001"


def config(db_url, sender, enabled=True):
    return {"TESTING": True, "DATABASE_URL": db_url, "SEED_DEFAULT_STORES": False,
            "FCM_ENABLED": enabled, "FCM_PROJECT_ID": "test-project", "FCM_SENDER": sender,
            "AUTH_CHECKER": lambda key: {"status": "approved", "token": "test"}
            if key in {"test-admin", "kiosk-test-admin"} else {"status": "denied"}}


@pytest.fixture
def setup(tmp_path):
    sent = []
    app = create_app(config(f"sqlite:///{tmp_path / 'test.db'}", lambda *args: sent.append(args)))
    with TestClient(app) as client:
        with app.state.SessionLocal() as db:
            db.add_all([Store(name="매장 A", code="A001"), Store(name="매장 B", code="B001")])
            db.commit()
        assert client.post("/api/admin/login", json={"key": "test-admin"}).status_code == 200
        yield app, client, sent


def register(client, store_id=1, token=TOKEN, installation=INSTALL, service="simple_service"):
    r = client.post("/api/admin/mobile/register", json={
        "installation_id": installation, "token": token, "store_id": store_id, "service_type": service,
        "firebase_project_id": "test-project",
    })
    assert r.status_code == 200, r.text
    return r.json()


def bearer(binding):
    return {"Authorization": f"Bearer {binding['binding_id']}.{binding['device_secret']}"}


def issue(client, store_id=1, service="simple_service"):
    r = client.post("/api/tickets", json={"store_id": store_id, "service_type": service})
    assert r.status_code == 201, r.text
    return r.json()


def rows(app, model):
    with app.state.SessionLocal() as db:
        return list(db.scalars(select(model).order_by(model.id)))


def test_manager_route_is_separate_and_has_no_credentials(setup):
    _, client, _ = setup
    page = client.get("/manager")
    assert page.status_code == 200
    assert "window.MANAGER_APP = true" in page.text
    assert "manager-web.js" in page.text
    assert "frame-src 'none'" in page.headers["content-security-policy"]
    assert "private_key" not in page.text
    assert "window.MANAGER_APP = false" in client.get("/admin?store_id=1").text
    assert "manager-web.js" not in client.get("/admin?store_id=1").text


def test_registration_requires_existing_admin_auth(setup):
    app, client, _ = setup
    client.post("/api/admin/logout")
    r = client.post("/api/admin/mobile/register", json={"installation_id": INSTALL, "token": TOKEN, "store_id": 1, "service_type": "simple_service"})
    assert r.status_code == 401
    assert not rows(app, MobileDevice)


def test_secret_hashed_and_not_returned_from_status(setup):
    app, client, _ = setup
    binding = register(client)
    assert rows(app, MobileDevice)[0].secret_hash != binding["device_secret"]
    status = client.get("/api/admin/mobile/status")
    assert status.status_code == 200
    assert TOKEN not in status.text and binding["device_secret"] not in status.text
    assert status.json()["notification_scope"] == "selected_service"


def test_register_rejects_cross_origin(setup):
    _, client, _ = setup
    r = client.post("/api/admin/mobile/register", headers={"Origin": "https://evil.invalid"},
                    json={"installation_id": INSTALL, "token": TOKEN, "store_id": 1, "service_type": "simple_service"})
    assert r.status_code == 403


def test_register_rejects_project_mismatch(setup):
    _, client, _ = setup
    r = client.post("/api/admin/mobile/register", json={"installation_id": INSTALL, "token": TOKEN,
                    "store_id": 1, "service_type": "simple_service", "firebase_project_id": "different-project"})
    assert r.status_code == 400


def test_register_rejects_missing_or_inactive_store(setup):
    app, client, _ = setup
    with app.state.SessionLocal() as db:
        db.get(Store, 1).is_active = False
        db.commit()
    for store_id in [1, 999]:
        r = client.post("/api/admin/mobile/register", json={"installation_id": INSTALL, "token": TOKEN, "store_id": store_id, "service_type": "simple_service"})
        assert r.status_code == 404


@pytest.mark.parametrize("selected_store", [1, 2])
@pytest.mark.parametrize("selected_service", ["simple_service", "purchase_consult"])
def test_only_matching_store_and_selected_service_receive(setup, selected_store, selected_service):
    app, client, sent = setup
    binding = register(client, store_id=selected_store, service=selected_service)
    for store_id in [1, 2]:
        for service in ["simple_service", "purchase_consult"]:
            result = issue(client, store_id=store_id, service=service)
            assert set(result) == {"ticket", "state", "push"}  # 기존 번호표 API 계약 보존
            assert result["ticket"]["ticket_number"] == (1 if service == "simple_service" else 101)
    assert len(rows(app, MobilePushDelivery)) == 1
    assert process_mobile_outbox(app) == 1
    assert len(sent) == 1
    assert sent[0][0] == TOKEN
    assert sent[0][1]["service_type"] == selected_service
    assert sent[0][1]["store_id"] == str(selected_store)
    assert sent[0][1]["binding_id"] == binding["binding_id"]
    assert 1 <= sent[0][2] <= 120


def test_no_device_no_outbox(setup):
    app, client, _ = setup
    issue(client)
    assert not rows(app, MobilePushDelivery)


def test_send_not_done_in_ticket_request(setup):
    app, client, sent = setup
    register(client)
    issue(client)
    assert not sent
    assert rows(app, MobilePushDelivery)[0].status == "pending"


def test_no_double_send_after_sent(setup):
    app, client, sent = setup
    register(client)
    issue(client)
    process_mobile_outbox(app)
    assert process_mobile_outbox(app) == 0
    assert len(sent) == 1


def test_transient_send_failure_retries_without_reissuing_ticket(setup):
    app, client, sent = setup
    register(client)
    def broken(*args):
        raise TimeoutError("sensitive-token-not-to-log")
    app.state.mobile_sender = broken
    issue(client)
    process_mobile_outbox(app)
    row = rows(app, MobilePushDelivery)[0]
    assert row.status == "pending" and row.last_error == "TimeoutError"
    with app.state.SessionLocal() as db:
        db.get(MobilePushDelivery, row.id).next_attempt = utc_now() - timedelta(seconds=1)
        db.commit()
    app.state.mobile_sender = lambda *args: sent.append(args)
    process_mobile_outbox(app)
    assert len(sent) == 1 and len(rows(app, Ticket)) == 1
    assert rows(app, MobilePushDelivery)[0].attempts == 2


def test_expired_delivery_skipped(setup):
    app, client, sent = setup
    register(client)
    issue(client)
    with app.state.SessionLocal() as db:
        db.get(MobilePushDelivery, 1).expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    process_mobile_outbox(app)
    assert not sent
    assert rows(app, MobilePushDelivery)[0].status == "skipped"


def test_expired_registration_cannot_refresh_or_receive(setup):
    app, client, _ = setup
    binding = register(client)
    with app.state.SessionLocal() as db:
        db.get(MobileDevice, 1).expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    assert client.post("/api/mobile/token", headers=bearer(binding), json={"token": TOKEN}).status_code == 401
    issue(client)
    assert not rows(app, MobilePushDelivery)


def test_wrong_secret_cannot_refresh_or_unregister(setup):
    _, client, _ = setup
    b = register(client)
    headers = {"Authorization": f"Bearer {b['binding_id']}.incorrect-secret"}
    assert client.post("/api/mobile/token", headers=headers, json={"token": TOKEN}).status_code == 401
    assert client.post("/api/mobile/unregister", headers=headers).status_code == 401


def test_refresh_updates_target_not_expiry(setup):
    app, client, sent = setup
    binding = register(client)
    expiry = rows(app, MobileDevice)[0].expires_at
    new_token = TOKEN + "-new"
    assert client.post("/api/mobile/token", headers=bearer(binding), json={"token": new_token}).status_code == 200
    assert rows(app, MobileDevice)[0].expires_at == expiry
    issue(client)
    process_mobile_outbox(app)
    assert sent[0][0] == new_token


def test_unregister_suppresses_pending_and_future(setup):
    app, client, sent = setup
    binding = register(client)
    issue(client)
    assert client.post("/api/mobile/unregister", headers=bearer(binding)).status_code == 200
    issue(client)
    process_mobile_outbox(app)
    assert not sent
    assert len(rows(app, MobilePushDelivery)) == 1


def test_switch_store_rotates_secret_and_suppresses_old_pending(setup):
    app, client, sent = setup
    old = register(client)
    issue(client)
    current = register(client, store_id=2)
    assert old["binding_id"] != current["binding_id"]
    assert client.post("/api/mobile/unregister", headers=bearer(old)).status_code == 401
    issue(client, store_id=1)
    issue(client, store_id=2)
    process_mobile_outbox(app)
    assert len(sent) == 1 and sent[0][1]["store_id"] == "2"


def test_same_token_new_installation_receives_once(setup):
    app, client, sent = setup
    register(client)
    register(client, installation=INSTALL + "-reinstalled")
    issue(client)
    process_mobile_outbox(app)
    assert len(sent) == 1


def test_fcm_unregistered_token_deactivates_device(setup):
    app, client, _ = setup
    class UnregisteredError(Exception): pass
    def sender(*args): raise UnregisteredError()
    app.state.mobile_sender = sender
    register(client)
    issue(client)
    process_mobile_outbox(app)
    assert rows(app, MobileDevice)[0].active is False
    assert rows(app, MobilePushDelivery)[0].status == "failed"


def test_store_deletion_cascades_mobile_records(setup):
    app, client, _ = setup
    register(client)
    issue(client)
    client.post("/api/admin/manage/login", json={"key": "kiosk-test-admin"})
    assert client.delete("/api/admin/stores/1").status_code == 200
    assert not rows(app, MobileDevice) and not rows(app, MobilePushDelivery)


def test_test_notification_does_not_issue_ticket(setup):
    app, client, sent = setup
    binding = register(client)
    response = client.post("/api/admin/mobile/test", json={"binding_id": binding["binding_id"]})
    assert response.json() == {"ok": True, "queued": True}
    assert not rows(app, Ticket)
    process_mobile_outbox(app)
    assert sent[0][1]["type"] == "test"


def test_disabled_fcm_keeps_web_and_ticket_functions(tmp_path):
    app = create_app(config(f"sqlite:///{tmp_path / 'off.db'}", lambda *args: None, enabled=False))
    with TestClient(app) as client:
        with app.state.SessionLocal() as db:
            db.add(Store(name="test", code="OFF")); db.commit()
        client.post("/api/admin/login", json={"key": "test-admin"})
        assert client.get("/manager").status_code == 200
        issue(client)
        assert not rows(app, MobilePushDelivery)
        assert client.post("/api/admin/mobile/register", json={"installation_id": INSTALL, "token": TOKEN, "store_id": 1, "service_type": "simple_service"}).status_code == 503


def test_pending_deliveries_survive_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'persistent.db'}"
    sent = []
    app = create_app(config(url, lambda *args: None))
    with TestClient(app) as client:
        with app.state.SessionLocal() as db:
            db.add(Store(name="test", code="TEST")); db.commit()
        client.post("/api/admin/login", json={"key": "test-admin"})
        register(client)
        issue(client)
    new_app = create_app(config(url, lambda *args: sent.append(args)))
    with TestClient(new_app):
        process_mobile_outbox(new_app)
        assert len(sent) == 1


def test_ticket_and_outbox_are_atomic(setup):
    app, client, _ = setup
    register(client)
    def abort(db):
        # mobile listener가 flush한 Ticket과 추가한 outbox 모두 rollback되어야 한다.
        raise RuntimeError("simulate transaction failure")
    event.listen(app.state.SessionLocal, "before_commit", abort)
    with app.state.SessionLocal() as db:
        db.add(Ticket(store_id=1, service_type="simple_service", round_no=1, ticket_number=42))
        with pytest.raises(RuntimeError): db.commit()
        db.rollback()
    event.remove(app.state.SessionLocal, "before_commit", abort)
    assert not rows(app, Ticket) and not rows(app, MobilePushDelivery)


def test_mobile_settings_validate_ranges_and_false():
    assert load_mobile_settings({"FCM_ENABLED": "false"}).enabled is False
    assert load_mobile_settings({"FCM_ENABLED": "true"}).enabled is True
    with pytest.raises(ValueError): load_mobile_settings({"FCM_TTL_SECONDS": 1})
    with pytest.raises(ValueError): load_mobile_settings({"FCM_REGISTRATION_DAYS": "no"})


@pytest.mark.parametrize("service", [None, "", "both_services", "integrated", "unknown"])
def test_unselected_or_invalid_service_rejected(setup, service):
    app, client, _ = setup
    body = {"installation_id": INSTALL, "token": TOKEN, "store_id": 1}
    if service is not None:
        body["service_type"] = service
    response = client.post("/api/admin/mobile/register", json=body)
    assert response.status_code == 422
    assert not rows(app, MobileDevice)


def test_four_devices_keep_all_store_service_combinations_separate(setup):
    app, client, sent = setup
    for store in [1, 2]:
        for service in ["simple_service", "purchase_consult"]:
            register(client, store_id=store, service=service,
                     token=f"{TOKEN}-{store}-{service}", installation=f"{INSTALL}-{store}-{service}")
    for store in [1, 2]:
        for service in ["simple_service", "purchase_consult"]:
            issue(client, store_id=store, service=service)
    assert len(rows(app, MobilePushDelivery)) == 4
    process_mobile_outbox(app)
    assert len(sent) == 4
    for token, data, _ in sent:
        assert token == f"{TOKEN}-{data['store_id']}-{data['service_type']}"


def test_switch_service_replaces_binding_and_skips_previous_pending(setup):
    app, client, sent = setup
    old = register(client, service="simple_service")
    issue(client, service="simple_service")
    current = register(client, service="purchase_consult")
    assert current["binding_id"] != old["binding_id"]
    assert current["service_type"] == "purchase_consult"
    assert client.post("/api/mobile/unregister", headers=bearer(old)).status_code == 401
    issue(client, service="simple_service")
    issue(client, service="purchase_consult")
    process_mobile_outbox(app)
    assert len(sent) == 1
    assert sent[0][1]["service_type"] == "purchase_consult"
    assert [d.status for d in rows(app, MobilePushDelivery)] == ["skipped", "sent"]


def test_switch_service_back_does_not_replay_old_simple_binding(setup):
    app, client, sent = setup
    original = register(client, service="simple_service")
    issue(client, service="simple_service")
    register(client, service="purchase_consult")
    issue(client, service="purchase_consult")
    latest = register(client, service="simple_service")
    issue(client, service="simple_service")
    process_mobile_outbox(app)
    assert latest["binding_id"] != original["binding_id"]
    assert len(sent) == 1 and sent[0][1]["ticket_number"] == "2"
    assert sent[0][1]["binding_id"] == latest["binding_id"]


@pytest.mark.parametrize("field,bad", [("store_id", "2"), ("service_type", "purchase_consult"),
                                      ("binding_id", "old-binding")])
def test_worker_rechecks_queued_payload_scope_before_send(setup, field, bad):
    app, client, sent = setup
    register(client)
    issue(client)
    with app.state.SessionLocal() as db:
        row = db.get(MobilePushDelivery, 1)
        payload = json.loads(row.payload)
        payload[field] = bad
        row.payload = json.dumps(payload)
        db.commit()
    process_mobile_outbox(app)
    assert not sent
    assert rows(app, MobilePushDelivery)[0].status == "skipped"


@pytest.mark.parametrize("selected", ["simple_service", "purchase_consult"])
def test_notification_test_uses_selected_service_not_hardcoded_simple(setup, selected):
    app, client, sent = setup
    binding = register(client, store_id=2, service=selected)
    response = client.post("/api/admin/mobile/test", json={"binding_id": binding["binding_id"]})
    assert response.status_code == 200
    process_mobile_outbox(app)
    assert sent[0][1]["service_type"] == selected
    assert sent[0][1]["store_id"] == "2"
    assert not rows(app, Ticket)


def test_explicit_device_list_cannot_bypass_store_or_service_filter(setup):
    app, client, _ = setup
    register(client)
    with app.state.SessionLocal() as db:
        device = db.get(MobileDevice, 1)
        for store, service in [(1, "purchase_consult"), (2, "simple_service"), (1, "both_services")]:
            assert _queue_for_devices(db, app.state.mobile_settings, db.get(Store, store),
                                      {"type": "test", "service_type": service}, [device]) == 0
        db.commit()
    assert not rows(app, MobilePushDelivery)


def test_legacy_database_migrates_without_losing_tickets(tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    app = create_app(config(url, lambda *args: None))
    with TestClient(app) as client:
        with app.state.SessionLocal() as db:
            db.add(Store(name="기존 매장", code="OLD")); db.commit()
        client.post("/api/admin/login", json={"key": "test-admin"})
        register(client)
        issue(client)
    # 직전 배포본과 같은 열 구성으로 돌린 다음 새 서버를 시작한다.
    with app.state.engine.begin() as connection:
        connection.execute(text("ALTER TABLE manager_mobile_devices DROP COLUMN service_type"))
    sent = []
    upgraded = create_app(config(url, lambda *args: sent.append(args)))
    with TestClient(upgraded) as client:
        assert "service_type" in {c["name"] for c in inspect(upgraded.state.engine).get_columns("manager_mobile_devices")}
        assert len(rows(upgraded, Ticket)) == 1
        assert rows(upgraded, MobileDevice)[0].active is False
        process_mobile_outbox(upgraded)
        assert not sent
        client.post("/api/admin/login", json={"key": "test-admin"})
        register(client, service="purchase_consult")
        issue(client, service="purchase_consult")
        process_mobile_outbox(upgraded)
        assert len(sent) == 1 and sent[0][1]["service_type"] == "purchase_consult"
    # 두 번째 기동은 새 정상 등록을 해지하지 않는다.
    again = create_app(config(url, lambda *args: sent.append(args)))
    with TestClient(again):
        assert rows(again, MobileDevice)[0].active is True
        assert rows(again, MobileDevice)[0].service_type == "purchase_consult"
        assert len(rows(again, Ticket)) == 2


def test_token_refresh_keeps_single_selected_service(setup):
    app, client, sent = setup
    binding = register(client, service="purchase_consult")
    fresh = TOKEN + "-refreshed"
    assert client.post("/api/mobile/token", json={"token": fresh}, headers=bearer(binding)).status_code == 200
    issue(client, service="simple_service")
    issue(client, service="purchase_consult")
    process_mobile_outbox(app)
    assert len(sent) == 1 and sent[0][0] == fresh
    assert sent[0][1]["service_type"] == "purchase_consult"
