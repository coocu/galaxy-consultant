import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Store
from app.services import (
    SERVICE_PURCHASE,
    SERVICE_SIMPLE,
    call_next,
    direct_call,
    get_store_state,
    issue_ticket,
    recall_last,
    reset_service,
)


@pytest.fixture()
def app_and_store():
    app = create_app({
        "TESTING": True,
        "DATABASE_URL": "sqlite:///:memory:",
        "SEED_DEFAULT_STORES": False,
    })
    with TestClient(app):
        db = app.state.SessionLocal()
        store = Store(name="이천점")
        db.add(store)
        db.commit()
        db.refresh(store)
        yield app, store.id
        db.close()


def test_issue_ticket_starts_at_one_and_isolated_by_service(app_and_store):
    app, store_id = app_and_store
    db = app.state.SessionLocal()

    first_simple = issue_ticket(db, store_id, SERVICE_SIMPLE)
    first_purchase = issue_ticket(db, store_id, SERVICE_PURCHASE)
    second_simple = issue_ticket(db, store_id, SERVICE_SIMPLE)

    assert first_simple.ticket_number == 1
    assert first_purchase.ticket_number == 1
    assert second_simple.ticket_number == 2

    state = get_store_state(db, store_id)
    assert state["services"][SERVICE_SIMPLE]["waiting_count"] == 2
    assert state["services"][SERVICE_PURCHASE]["waiting_count"] == 1
    db.close()


def test_call_recall_direct_and_reset_change_state(app_and_store):
    app, store_id = app_and_store
    db = app.state.SessionLocal()
    issue_ticket(db, store_id, SERVICE_SIMPLE)
    issue_ticket(db, store_id, SERVICE_SIMPLE)

    first_call = call_next(db, store_id, SERVICE_SIMPLE)
    assert first_call.ticket_number == 1

    recalled = recall_last(db, store_id, SERVICE_SIMPLE)
    assert recalled.ticket_number == 1
    assert recalled.call_type == "recall"

    direct = direct_call(db, store_id, SERVICE_SIMPLE, 9)
    assert direct.ticket_number == 9
    assert direct.call_type == "direct"

    state = get_store_state(db, store_id)
    assert state["services"][SERVICE_SIMPLE]["current_number"] == 9
    assert state["services"][SERVICE_SIMPLE]["waiting_count"] == 1

    reset_service(db, store_id, SERVICE_SIMPLE)
    state_after_reset = get_store_state(db, store_id)
    assert state_after_reset["services"][SERVICE_SIMPLE]["current_number"] is None
    assert state_after_reset["services"][SERVICE_SIMPLE]["waiting_count"] == 0

    new_ticket = issue_ticket(db, store_id, SERVICE_SIMPLE)
    assert new_ticket.ticket_number == 1
    db.close()


def test_call_next_returns_error_when_no_waiting_ticket(app_and_store):
    app, store_id = app_and_store
    db = app.state.SessionLocal()

    with pytest.raises(ValueError, match="대기 고객이 없습니다"):
        call_next(db, store_id, SERVICE_SIMPLE)
    db.close()


def test_deleted_store_state_keeps_inactive_flag(app_and_store):
    app, store_id = app_and_store
    db = app.state.SessionLocal()
    store = db.get(Store, store_id)
    store.is_active = False
    db.commit()

    state = get_store_state(db, store_id)
    assert state["store"]["is_active"] is False
    db.close()
