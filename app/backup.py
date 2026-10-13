"""전체 ZIP 백업과 이전 매장 카테고리 ZIP 호환 복원."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text, select
from sqlalchemy.exc import IntegrityError
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


def make_store_backup_zip(db: Session) -> bytes:
    payload = export_data(db)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("data.json", json.dumps(payload, ensure_ascii=False, indent=2))
        archive.writestr(
            "README.txt",
            "Galaxy Consultant 직원 호출 시스템 매장 카테고리 백업 파일입니다. 번호표/호출기록은 포함되지 않습니다.\n",
        )
    return buffer.getvalue()


def make_backup_zip(db: Session, video_directory=None) -> bytes:
    from .console_backup import make_full_backup
    return make_full_backup(db, video_directory) if video_directory is not None else make_full_backup(db)


def backup_version(zip_bytes: bytes) -> int:
    from .console_backup import _load_archive
    archive, data = _load_archive(zip_bytes)
    archive.close()
    if not isinstance(data, dict): raise ValueError("백업 데이터 형식이 올바르지 않습니다")
    return data.get("version", 0)


def restore_from_zip_bytes(db: Session, zip_bytes: bytes, video_directory=None) -> dict[str, Any]:
    from .console_backup import restore_full_backup
    if backup_version(zip_bytes) == 3:
        return {"kind": "full", "tables": restore_full_backup(db, zip_bytes, video_directory) if video_directory is not None else restore_full_backup(db, zip_bytes)}
    return restore_store_zip(db, zip_bytes)


def restore_store_zip(db: Session, zip_bytes: bytes) -> dict[str, Any]:
    from .console_backup import _load_archive
    archive, data = _load_archive(zip_bytes)
    archive.close()
    if not isinstance(data, dict) or data.get("version") not in SUPPORTED_BACKUP_VERSIONS:
        raise ValueError("지원하지 않는 백업 버전입니다")
    if not isinstance(data.get("stores"), list):
        raise ValueError("백업 데이터에 stores 목록이 없습니다")
    prepared = []
    ids, codes = set(), set()
    now = _now_naive_utc()
    for item in data["stores"]:
        try:
            store_id = int(item["id"])
            name = str(item["name"]).strip()
            code = str(item["code"]).strip().upper() if item.get("code") is not None else None
            code = code or None
            created = _parse_dt(item.get("created_at"))
            updated = _parse_dt(item.get("updated_at"))
            if created and created.tzinfo: created = created.astimezone(UTC).replace(tzinfo=None)
            if updated and updated.tzinfo: updated = updated.astimezone(UTC).replace(tzinfo=None)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("매장 백업의 ID, 이름, 점코드 또는 날짜가 올바르지 않습니다") from exc
        if store_id < 1 or store_id in ids:
            raise ValueError("매장 ID가 중복되거나 올바르지 않습니다")
        if not name or len(name)>120 or (code and len(code)>40):
            raise ValueError("매장 이름 또는 점코드의 길이가 올바르지 않습니다")
        if code and code in codes: raise ValueError("중복된 점코드가 포함되어 있습니다")
        if not isinstance(item.get("is_active",True),bool): raise ValueError("매장 활성 상태가 올바르지 않습니다")
        ids.add(store_id)
        if code: codes.add(code)
        prepared.append((store_id,name,code,item.get("is_active",True),created,updated))
    try:
        existing = {store.id:store for store in db.scalars(select(Store))}
        if any(store.id not in ids and store.code in codes for store in existing.values()):
            raise ValueError("현재 다른 매장에서 사용하는 점코드가 있습니다. 중복을 확인한 뒤 복원하세요")
        # 두 점코드가 서로 교환된 백업도 UNIQUE 제약을 위반하지 않게 처리한다.
        for sid in ids:
            if sid in existing: existing[sid].code=None
        db.flush()
        for sid,name,code,active,created,updated in prepared:
            store=existing.get(sid)
            if store is None:
                store=Store(id=sid,name=name);db.add(store)
            store.name=name;store.code=code;store.is_active=active
            store.created_at=created or store.created_at or now
            store.updated_at=updated or now
        deactivated=0
        for sid,store in existing.items():
            if sid not in ids and store.is_active:
                store.is_active=False;store.updated_at=now;deactivated+=1
        db.flush()
        if db.bind is not None and db.bind.dialect.name=='postgresql':
            db.execute(text("SELECT setval(pg_get_serial_sequence('stores','id'), COALESCE((SELECT MAX(id) FROM stores), 1), (SELECT COUNT(*)>0 FROM stores))"))
        db.commit()
    except (IntegrityError, ValueError) as exc:
        db.rollback()
        raise ValueError(str(exc) if isinstance(exc,ValueError) else "점코드 또는 매장 ID가 충돌하여 복원하지 않았습니다") from exc
    return {"stores":len(ids),"deactivated_missing_stores":deactivated}

