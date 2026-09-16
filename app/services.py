"""번호표와 호출 핵심 로직."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import CallLog, ServiceCounter, Store, Ticket

SERVICE_SIMPLE = "simple_service"
SERVICE_PURCHASE = "purchase_consult"
VALID_SERVICES = {SERVICE_SIMPLE, SERVICE_PURCHASE}

SERVICE_META: dict[str, dict[str, str]] = {
    SERVICE_SIMPLE: {
        "customer_label": "간단서비스",
        "admin_label": "갤럭시 컨설턴트",
        "voice_label": "간단서비스",
        "theme": "blue",
    },
    SERVICE_PURCHASE: {
        "customer_label": "구매문의",
        "admin_label": "구매상담",
        "voice_label": "구매문의",
        "theme": "red",
    },
}

CALL_NORMAL = "normal"
CALL_RECALL = "recall"
CALL_DIRECT = "direct"
CALL_RESET = "reset"


def validate_service_type(service_type: str) -> str:
    if service_type not in VALID_SERVICES:
        raise ValueError("지원하지 않는 업무입니다")
    return service_type


def get_store_or_raise(db: Session, store_id: int) -> Store:
    store = db.get(Store, store_id)
    if store is None:
        raise ValueError("매장을 찾을 수 없습니다")
    return store


def get_or_create_counter(db: Session, store_id: int, service_type: str) -> ServiceCounter:
    validate_service_type(service_type)
    get_store_or_raise(db, store_id)
    counter = db.execute(
        select(ServiceCounter).where(
            ServiceCounter.store_id == store_id,
            ServiceCounter.service_type == service_type,
        )
    ).scalar_one_or_none()
    if counter is None:
        counter = ServiceCounter(
            store_id=store_id,
            service_type=service_type,
            next_number=1,
            current_number=None,
            round_no=1,
        )
        db.add(counter)
        db.flush()
    return counter


def ticket_to_dict(ticket: Ticket) -> dict[str, Any]:
    return {
        "id": ticket.id,
        "store_id": ticket.store_id,
        "service_type": ticket.service_type,
        "service": SERVICE_META[ticket.service_type],
        "round_no": ticket.round_no,
        "ticket_number": ticket.ticket_number,
        "status": ticket.status,
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
        "called_at": ticket.called_at.isoformat() if ticket.called_at else None,
    }


def store_to_dict(store: Store) -> dict[str, Any]:
    return {
        "id": store.id,
        "name": store.name,
        "is_active": store.is_active,
        "created_at": store.created_at.isoformat() if store.created_at else None,
        "updated_at": store.updated_at.isoformat() if store.updated_at else None,
    }


def call_to_dict(call: CallLog) -> dict[str, Any]:
    return {
        "id": call.id,
        "store_id": call.store_id,
        "store_name": call.store.name if call.store else None,
        "service_type": call.service_type,
        "service": SERVICE_META[call.service_type],
        "round_no": call.round_no,
        "ticket_number": call.ticket_number,
        "call_type": call.call_type,
        "created_at": call.created_at.isoformat() if call.created_at else None,
        "speech_text": build_speech_text(call.service_type, call.ticket_number) if call.ticket_number else None,
    }


def build_speech_text(service_type: str, ticket_number: int | None) -> str:
    validate_service_type(service_type)
    if ticket_number is None:
        return ""
    return f"{ticket_number}번 고객님. {SERVICE_META[service_type]['voice_label']} 창구로 와주세요."


def issue_ticket(db: Session, store_id: int, service_type: str) -> Ticket:
    validate_service_type(service_type)
    store = get_store_or_raise(db, store_id)
    if not store.is_active:
        raise ValueError("현재 사용할 수 없는 매장입니다")

    counter = get_or_create_counter(db, store_id, service_type)
    ticket = Ticket(
        store_id=store_id,
        service_type=service_type,
        round_no=counter.round_no,
        ticket_number=counter.next_number,
        status="waiting",
    )
    counter.next_number += 1
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


def call_next(db: Session, store_id: int, service_type: str) -> CallLog:
    validate_service_type(service_type)
    counter = get_or_create_counter(db, store_id, service_type)
    ticket = db.execute(
        select(Ticket)
        .where(
            Ticket.store_id == store_id,
            Ticket.service_type == service_type,
            Ticket.round_no == counter.round_no,
            Ticket.status == "waiting",
        )
        .order_by(Ticket.ticket_number.asc())
        .limit(1)
    ).scalar_one_or_none()
    if ticket is None:
        raise ValueError("대기 고객이 없습니다")

    ticket.status = "called"
    ticket.called_at = datetime.now(UTC).replace(tzinfo=None)
    counter.current_number = ticket.ticket_number
    call = CallLog(
        store_id=store_id,
        service_type=service_type,
        round_no=counter.round_no,
        ticket_number=ticket.ticket_number,
        call_type=CALL_NORMAL,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def recall_last(db: Session, store_id: int, service_type: str) -> CallLog:
    validate_service_type(service_type)
    counter = get_or_create_counter(db, store_id, service_type)
    if counter.current_number is None:
        raise ValueError("재호출할 번호가 없습니다")

    call = CallLog(
        store_id=store_id,
        service_type=service_type,
        round_no=counter.round_no,
        ticket_number=counter.current_number,
        call_type=CALL_RECALL,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def direct_call(db: Session, store_id: int, service_type: str, ticket_number: int) -> CallLog:
    validate_service_type(service_type)
    if ticket_number < 1:
        raise ValueError("호출번호는 1 이상이어야 합니다")

    counter = get_or_create_counter(db, store_id, service_type)
    ticket = db.execute(
        select(Ticket).where(
            Ticket.store_id == store_id,
            Ticket.service_type == service_type,
            Ticket.round_no == counter.round_no,
            Ticket.ticket_number == ticket_number,
            Ticket.status == "waiting",
        )
    ).scalar_one_or_none()
    if ticket is not None:
        ticket.status = "called"
        ticket.called_at = datetime.now(UTC).replace(tzinfo=None)

    counter.current_number = ticket_number
    call = CallLog(
        store_id=store_id,
        service_type=service_type,
        round_no=counter.round_no,
        ticket_number=ticket_number,
        call_type=CALL_DIRECT,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def reset_service(db: Session, store_id: int, service_type: str) -> ServiceCounter:
    validate_service_type(service_type)
    counter = get_or_create_counter(db, store_id, service_type)
    old_round_no = counter.round_no
    db.query(Ticket).filter(
        Ticket.store_id == store_id,
        Ticket.service_type == service_type,
        Ticket.round_no == old_round_no,
        Ticket.status.in_(["waiting", "called"]),
    ).update({Ticket.status: "reset"}, synchronize_session=False)

    counter.next_number = 1
    counter.current_number = None
    counter.round_no = old_round_no + 1
    call = CallLog(
        store_id=store_id,
        service_type=service_type,
        round_no=counter.round_no,
        ticket_number=None,
        call_type=CALL_RESET,
    )
    db.add(call)
    db.commit()
    db.refresh(counter)
    return counter


def get_waiting_tickets(db: Session, store_id: int, service_type: str, round_no: int, limit: int = 20) -> list[Ticket]:
    return list(
        db.execute(
            select(Ticket)
            .where(
                Ticket.store_id == store_id,
                Ticket.service_type == service_type,
                Ticket.round_no == round_no,
                Ticket.status == "waiting",
            )
            .order_by(Ticket.ticket_number.asc())
            .limit(limit)
        ).scalars()
    )


def get_store_state(db: Session, store_id: int) -> dict[str, Any]:
    store = get_store_or_raise(db, store_id)
    services: dict[str, dict[str, Any]] = {}
    for service_type in (SERVICE_SIMPLE, SERVICE_PURCHASE):
        counter = get_or_create_counter(db, store_id, service_type)
        waiting_filter = (
            Ticket.store_id == store_id,
            Ticket.service_type == service_type,
            Ticket.round_no == counter.round_no,
            Ticket.status == "waiting",
        )
        waiting_count = db.execute(
            select(func.count(Ticket.id)).where(*waiting_filter)
        ).scalar_one()
        next_waiting_number = db.execute(
            select(func.min(Ticket.ticket_number)).where(*waiting_filter)
        ).scalar_one()
        waiting_tickets = get_waiting_tickets(db, store_id, service_type, counter.round_no)
        services[service_type] = {
            "service_type": service_type,
            "meta": SERVICE_META[service_type],
            "current_number": counter.current_number,
            "waiting_count": int(waiting_count),
            "next_number": counter.next_number,
            "next_waiting_number": next_waiting_number,
            "round_no": counter.round_no,
            "waiting_tickets": [ticket_to_dict(ticket) for ticket in waiting_tickets],
        }

    latest_call_id = db.execute(
        select(func.max(CallLog.id)).where(CallLog.store_id == store_id)
    ).scalar_one()

    db.commit()
    return {
        "store": store_to_dict(store),
        "services": services,
        "last_call_id": latest_call_id or 0,
    }


def list_calls_after(db: Session, store_id: int, after_id: int = 0, limit: int = 10) -> list[dict[str, Any]]:
    calls = list(
        db.execute(
            select(CallLog)
            .where(CallLog.store_id == store_id, CallLog.id > after_id)
            .order_by(CallLog.id.asc())
            .limit(limit)
        ).scalars()
    )
    return [call_to_dict(call) for call in calls]
