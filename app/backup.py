"""매장 카테고리 전용 ZIP 백업/복원 기능."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import Store

BACKUP_VERSION = 2
SUPPORTED_BACKUP_VERSIONS = {1, BACKUP_VERSION}


def _now_naive_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def export_data(db: Session) -> dict[str, Any]:
    """매장 카테고리만 내보낸다.

    번호표, 호출기록, 업무별 카운터는 백업하지 않는다.
    카테고리마다 고유 id를 유지해야 이천/동해처럼 선택한 매장별 데이터가 섞이지 않는다.
    """
    stores = db.query(Store).order_by(Store.id.asc()).all()
    return {
        "version": BACKUP_VERSION,
        "exported_at": _now_naive_utc().isoformat(),
        "stores": [
            {
                "id": store.id,
                "name": store.name,
                "code": store.code,
                "is_active": store.is_active,
                "created_at": _dt(store.created_at),
                "updated_at": _dt(store.updated_at),
            }
            for store in stores
        ],
    }


def make_backup_zip(db: Session) -> bytes:
    payload = export_data(db)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("data.json", json.dumps(payload, ensure_ascii=False, indent=2))
        archive.writestr(
            "README.txt",
            "CodeNote 직원 호출 시스템 매장 카테고리 백업 파일입니다. 번호표/호출기록은 포함되지 않습니다.\n",
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

    if data.get("version") not in SUPPORTED_BACKUP_VERSIONS:
        raise ValueError("지원하지 않는 백업 버전입니다")
    if not isinstance(data.get("stores"), list):
        raise ValueError("백업 데이터에 stores 목록이 없습니다")

    restored_ids: set[int] = set()
    restored_codes: set[str] = set()
    now = _now_naive_utc()

    for item in data["stores"]:
        try:
            store_id = int(item["id"])
            name = str(item["name"]).strip()
            raw_code = item.get("code")
            code = str(raw_code).strip().upper() if raw_code is not None else None
            if code == "":
                code = None
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("매장 카테고리 백업 데이터 형식이 올바르지 않습니다") from exc

        if store_id < 1:
            raise ValueError("매장 카테고리 ID는 1 이상이어야 합니다")
        if not name:
            raise ValueError("매장명이 비어 있는 백업 파일은 복원할 수 없습니다")
        if code is not None and code in restored_codes:
            raise ValueError("중복된 점코드가 포함된 백업 파일은 복원할 수 없습니다")
        if code is not None:
            restored_codes.add(code)

        restored_ids.add(store_id)
        store = db.get(Store, store_id)
        if store is None:
            store = Store(id=store_id, name=name, code=code)
            db.add(store)
        else:
            store.name = name
            store.code = code

        store.is_active = bool(item.get("is_active", True))
        store.created_at = _parse_dt(item.get("created_at")) or store.created_at or now
        store.updated_at = _parse_dt(item.get("updated_at")) or now

    # 백업에 없는 매장은 삭제하지 않고 비활성화한다.
    # 이렇게 해야 해당 매장에 이미 쌓인 번호표/호출기록이 사라지지 않는다.
    deactivated_count = 0
    existing_stores = db.query(Store).all()
    for store in existing_stores:
        if store.id not in restored_ids and store.is_active:
            store.is_active = False
            store.updated_at = now
            deactivated_count += 1

    db.commit()
    _reset_postgres_store_sequence(db)

    return {
        "stores": len(restored_ids),
        "deactivated_missing_stores": deactivated_count,
    }


def _reset_postgres_store_sequence(db: Session) -> None:
    """PostgreSQL에서 수동 ID 복원 후 stores 다음 자동 ID가 충돌하지 않도록 보정한다."""
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    db.execute(text("SELECT setval(pg_get_serial_sequence('stores','id'), COALESCE((SELECT MAX(id) FROM stores), 1), true)"))
    db.commit()
