"""ZIP 백업/복원 기능."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import CallLog, ServiceCounter, Store, Ticket

BACKUP_VERSION = 1


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def export_data(db: Session) -> dict[str, Any]:
    stores = db.query(Store).order_by(Store.id.asc()).all()
    counters = db.query(ServiceCounter).order_by(ServiceCounter.id.asc()).all()
    tickets = db.query(Ticket).order_by(Ticket.id.asc()).all()
    call_logs = db.query(CallLog).order_by(CallLog.id.asc()).all()

    return {
        "version": BACKUP_VERSION,
        "exported_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "stores": [
            {
                "id": store.id,
                "name": store.name,
                "is_active": store.is_active,
                "created_at": _dt(store.created_at),
                "updated_at": _dt(store.updated_at),
            }
            for store in stores
        ],
        "service_counters": [
            {
                "id": counter.id,
                "store_id": counter.store_id,
                "service_type": counter.service_type,
                "next_number": counter.next_number,
                "current_number": counter.current_number,
                "round_no": counter.round_no,
                "updated_at": _dt(counter.updated_at),
            }
            for counter in counters
        ],
        "tickets": [
            {
                "id": ticket.id,
                "store_id": ticket.store_id,
                "service_type": ticket.service_type,
                "round_no": ticket.round_no,
                "ticket_number": ticket.ticket_number,
                "status": ticket.status,
                "created_at": _dt(ticket.created_at),
                "called_at": _dt(ticket.called_at),
            }
            for ticket in tickets
        ],
        "call_logs": [
            {
                "id": call.id,
                "store_id": call.store_id,
                "service_type": call.service_type,
                "round_no": call.round_no,
                "ticket_number": call.ticket_number,
                "call_type": call.call_type,
                "created_at": _dt(call.created_at),
            }
            for call in call_logs
        ],
    }


def make_backup_zip(db: Session) -> bytes:
    payload = export_data(db)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("data.json", json.dumps(payload, ensure_ascii=False, indent=2))
        archive.writestr(
            "README.txt",
            "CodeNote 직원 호출 시스템 백업 파일입니다. 관리자 화면의 복원 기능으로 업로드하세요.\n",
        )
    return buffer.getvalue()


def restore_from_zip_bytes(db: Session, zip_bytes: bytes) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            if "data.json" not in archive.namelist():
                raise ValueError("백업 ZIP 안에 data.json 파일이 없습니다")
            data = json.loads(archive.read("data.json").decode("utf-8"))
    except zipfile.BadZipFile as exc:
        raise ValueError("올바른 ZIP 백업 파일이 아닙니다") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("data.json 형식이 올바르지 않습니다") from exc

    if data.get("version") != BACKUP_VERSION:
        raise ValueError("지원하지 않는 백업 버전입니다")

    for required_key in ("stores", "service_counters", "tickets", "call_logs"):
        if not isinstance(data.get(required_key), list):
            raise ValueError(f"백업 데이터에 {required_key} 목록이 없습니다")

    db.query(CallLog).delete()
    db.query(Ticket).delete()
    db.query(ServiceCounter).delete()
    db.query(Store).delete()
    db.flush()

    for item in data["stores"]:
        db.add(
            Store(
                id=int(item["id"]),
                name=str(item["name"]),
                is_active=bool(item.get("is_active", True)),
                created_at=_parse_dt(item.get("created_at")) or datetime.now(UTC).replace(tzinfo=None),
                updated_at=_parse_dt(item.get("updated_at")) or datetime.now(UTC).replace(tzinfo=None),
            )
        )
    db.flush()

    for item in data["service_counters"]:
        db.add(
            ServiceCounter(
                id=int(item["id"]),
                store_id=int(item["store_id"]),
                service_type=str(item["service_type"]),
                next_number=int(item.get("next_number", 1)),
                current_number=item.get("current_number"),
                round_no=int(item.get("round_no", 1)),
                updated_at=_parse_dt(item.get("updated_at")) or datetime.now(UTC).replace(tzinfo=None),
            )
        )
    db.flush()

    for item in data["tickets"]:
        db.add(
            Ticket(
                id=int(item["id"]),
                store_id=int(item["store_id"]),
                service_type=str(item["service_type"]),
                round_no=int(item.get("round_no", 1)),
                ticket_number=int(item["ticket_number"]),
                status=str(item.get("status", "waiting")),
                created_at=_parse_dt(item.get("created_at")) or datetime.now(UTC).replace(tzinfo=None),
                called_at=_parse_dt(item.get("called_at")),
            )
        )
    db.flush()

    for item in data["call_logs"]:
        db.add(
            CallLog(
                id=int(item["id"]),
                store_id=int(item["store_id"]),
                service_type=str(item["service_type"]),
                round_no=int(item.get("round_no", 1)),
                ticket_number=item.get("ticket_number"),
                call_type=str(item["call_type"]),
                created_at=_parse_dt(item.get("created_at")) or datetime.now(UTC).replace(tzinfo=None),
            )
        )

    db.commit()
    _reset_postgres_sequences(db)

    return {
        "stores": len(data["stores"]),
        "service_counters": len(data["service_counters"]),
        "tickets": len(data["tickets"]),
        "call_logs": len(data["call_logs"]),
    }


def _reset_postgres_sequences(db: Session) -> None:
    """PostgreSQL에서 수동 ID 복원 후 다음 자동 ID가 충돌하지 않도록 보정한다."""
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    statements = [
        "SELECT setval(pg_get_serial_sequence('stores','id'), COALESCE((SELECT MAX(id) FROM stores), 1), true)",
        "SELECT setval(pg_get_serial_sequence('service_counters','id'), COALESCE((SELECT MAX(id) FROM service_counters), 1), true)",
        "SELECT setval(pg_get_serial_sequence('tickets','id'), COALESCE((SELECT MAX(id) FROM tickets), 1), true)",
        "SELECT setval(pg_get_serial_sequence('call_logs','id'), COALESCE((SELECT MAX(id) FROM call_logs), 1), true)",
    ]
    for statement in statements:
        db.execute(text(statement))
    db.commit()
