"""매니저 앱 전용 FCM. 기존 번호 발급/호출 및 브라우저 Web Push와 분리.

새 Ticket과 전송 대기 레코드는 같은 DB 트랜잭션에 저장한다. FCM 통신은
별도 worker가 수행하므로 Firebase 장애가 번호표 API를 지연시키지 않는다.
서비스 계정/단말 토큰/인증 비밀값은 로그나 공개 API에 반환하지 않는다.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Literal

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, event, inspect, or_, select, text, update
from sqlalchemy.orm import Mapped, Session, backref, mapped_column, relationship

from .database import Base, get_db
from .models import Store, Ticket, utc_now
from .services import SERVICE_META

log = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class MobilePushSettings:
    enabled: bool = False
    project_id: str = ""
    service_account_json: str = ""
    credentials_file: str = ""
    registration_days: int = 30
    ttl_seconds: int = 120


def load_mobile_settings(overrides: dict | None = None) -> MobilePushSettings:
    overrides = overrides or {}

    def value(name: str, default: str = ""):
        return overrides[name] if name in overrides else os.getenv(name, default)

    def integer(name: str, default: int, lower: int, upper: int) -> int:
        try:
            result = int(value(name, str(default)))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{name}은 정수여야 합니다") from exc
        if not lower <= result <= upper:
            raise ValueError(f"{name}은 {lower}~{upper} 범위여야 합니다")
        return result

    return MobilePushSettings(
        enabled=str(value("FCM_ENABLED", "false")).lower() in {"1", "true", "yes", "on"},
        project_id=str(value("FCM_PROJECT_ID")).strip(),
        service_account_json=str(value("FIREBASE_SERVICE_ACCOUNT_JSON")).strip(),
        credentials_file=str(value("GOOGLE_APPLICATION_CREDENTIALS")).strip(),
        registration_days=integer("FCM_REGISTRATION_DAYS", 30, 1, 365),
        ttl_seconds=integer("FCM_TTL_SECONDS", 120, 30, 3600),
    )


class MobileDevice(Base):
    __tablename__ = "manager_mobile_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    installation_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    # NULL is deliberately unsubscribed for registrations created by the older, both-services app.
    service_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    token: Mapped[str] = mapped_column(Text, nullable=False)
    binding_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)
    store: Mapped[Store] = relationship(Store, backref=backref("manager_mobile_devices", cascade="all, delete-orphan"))


class MobilePushDelivery(Base):
    __tablename__ = "manager_mobile_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("manager_mobile_devices.id", ondelete="CASCADE"), index=True)
    binding_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)
    lease_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    last_error: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    device: Mapped[MobileDevice] = relationship(MobileDevice, backref=backref("deliveries", cascade="all, delete-orphan"))


VALID_MOBILE_SERVICES = {"simple_service", "purchase_consult"}
NOTIFICATION_SCOPE = "selected_service"


def ensure_mobile_schema(engine) -> None:
    """기존 DB를 유지하며 업무 열만 추가한다. 과거 '두 업무' 등록은 재선택 전까지 해지.

    새 DB는 create_all에서 열이 만들어진다. SQLite / PostgreSQL 기존 DB에도
    적용되며 재시작 시 반복 실행해도 선택된 업무와 발급 데이터는 변경하지 않는다.
    """
    with engine.begin() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("manager_mobile_devices")}
        if "service_type" not in columns:
            optional = "IF NOT EXISTS " if engine.dialect.name == "postgresql" else ""
            connection.execute(text(
                f"ALTER TABLE manager_mobile_devices ADD COLUMN {optional}service_type VARCHAR(40)"
            ))
        connection.execute(text(
            "UPDATE manager_mobile_devices SET active = false "
            "WHERE service_type IS NULL OR service_type NOT IN ('simple_service', 'purchase_consult')"
        ))


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class FirebaseSender:
    """Lazy SDK 초기화. FCM_ENABLED=false이면 Firebase 키가 없어도 정상 운영."""

    def __init__(self, settings: MobilePushSettings):
        self.settings = settings
        self._app = None
        self._lock = threading.Lock()
        self.ready = False
        self.error = ""

    def prepare(self) -> None:
        if self._app is not None:
            return
        with self._lock:
            if self._app is not None:
                return
            try:
                import firebase_admin
                from firebase_admin import credentials

                if self.settings.service_account_json:
                    info = json.loads(self.settings.service_account_json)
                    if info.get("type") != "service_account":
                        raise ValueError("service-account-required")
                    credential = credentials.Certificate(info)
                elif self.settings.credentials_file:
                    credential = credentials.Certificate(self.settings.credentials_file)
                else:
                    # Google Cloud의 Workload Identity/ADC도 지원한다.
                    credential = credentials.ApplicationDefault()
                options = {"httpTimeout": 10}
                if self.settings.project_id:
                    options["projectId"] = self.settings.project_id
                app = firebase_admin.initialize_app(credential, options, name=f"manager-{uuid.uuid4().hex}")
                # 실제 자격 증명과 project_id를 기동 시 확인한다. 인증 토큰 발급은 send 때 한다.
                credential.get_credential()
                if not app.project_id:
                    firebase_admin.delete_app(app)
                    raise ValueError("project-id-required")
                self._app = app
                self.ready = True
                self.error = ""
            except Exception as exc:
                self.error = type(exc).__name__
                raise

    @property
    def project_id(self) -> str:
        return str(getattr(self._app, "project_id", None) or self.settings.project_id)

    def __call__(self, token: str, payload: dict[str, str], ttl: int) -> None:
        self.prepare()
        from firebase_admin import messaging

        # data-only: 전경/백그라운드 모두 Android의 동일한 채널/중복제거 경로로 처리.
        message = messaging.Message(
            token=token,
            data=payload,
            android=messaging.AndroidConfig(priority="high", ttl=timedelta(seconds=max(1, ttl))),
        )
        messaging.send(message, app=self._app)

    def close(self) -> None:
        if self._app is not None:
            import firebase_admin
            firebase_admin.delete_app(self._app)
            self._app = None
            self.ready = False


def _queue_for_devices(db: Session, settings: MobilePushSettings, store: Store,
                       data: dict[str, str], devices: list[MobileDevice] | None = None) -> int:
    now = utc_now()
    service = data.get("service_type")
    if service not in VALID_MOBILE_SERVICES or not store.is_active:
        return 0  # 업무를 확인할 수 없으면 전체 매장/전체 업무로 전송하지 않는다.
    if devices is None:
        devices = list(db.scalars(select(MobileDevice).where(
            MobileDevice.store_id == store.id,
            MobileDevice.service_type == service,
            MobileDevice.active.is_(True), MobileDevice.expires_at > now,
        )))
    # 테스트 전송처럼 대상이 명시됐어도 매장과 업무를 모두 재검사한다.
    devices = [device for device in devices
               if device.store_id == store.id and device.service_type == service
               and device.active and device.expires_at > now]
    event_id = uuid.uuid4().hex
    for device in devices:
        payload = {**data, "event_id": event_id, "store_id": str(store.id),
                   "store_code": str(store.code or ""), "binding_id": device.binding_id,
                   "issued_at": now.isoformat() + "Z"}
        db.add(MobilePushDelivery(
            device_id=device.id, binding_id=device.binding_id, event_id=event_id,
            payload=json.dumps(payload, ensure_ascii=False),
            expires_at=now + timedelta(seconds=settings.ttl_seconds), next_attempt=now,
        ))
    return len(devices)


def install_mobile_push(app, overrides: dict) -> None:
    settings = load_mobile_settings(overrides)
    app.state.mobile_settings = settings
    app.state.mobile_sender = overrides.get("FCM_SENDER") or FirebaseSender(settings)
    app.state.mobile_worker_error = ""

    def before_commit(db: Session) -> None:
        if not settings.enabled:
            return
        new_tickets = [item for item in db.new if isinstance(item, Ticket)]
        if not new_tickets:
            return
        db.flush()
        for ticket in new_tickets:
            store = db.get(Store, ticket.store_id)
            if store is None or not store.is_active:
                continue
            label = SERVICE_META[ticket.service_type]["customer_label"]
            _queue_for_devices(db, settings, store, {
                "type": "ticket_created", "service_type": ticket.service_type,
                "ticket_number": str(ticket.ticket_number), "ticket_id": str(ticket.id),
                "title": f"{store.name} · {label}",
                "body": f"{ticket.ticket_number}번 번호표가 발급되었습니다.",
            })

    # SessionLocal 전용 listener: 다른 앱/테스트 DB에 영향을 주지 않는다.
    event.listen(app.state.SessionLocal, "before_commit", before_commit)


def process_mobile_outbox(app, limit: int = 50) -> int:
    """DB lease로 중복 동시 전송 방지. 재시작하면 보류된 전송을 다시 읽는다.

    FCM은 exactly-once가 아니므로 lease 종료 직전의 재시작 등에서 재전송될 수 있다.
    Android가 event_id를 저장해 같은 알림을 다시 울리지 않도록 처리한다.
    """
    if not app.state.mobile_settings.enabled:
        return 0
    now = utc_now()
    with app.state.SessionLocal() as db:
        ids = list(db.scalars(select(MobilePushDelivery.id).where(
            MobilePushDelivery.status.in_(["pending", "sending"]),
            MobilePushDelivery.next_attempt <= now,
        ).order_by(MobilePushDelivery.id).limit(limit)))
    processed = 0
    for delivery_id in ids:
        lease = uuid.uuid4().hex
        with app.state.SessionLocal() as db:
            claimed = db.execute(update(MobilePushDelivery).where(
                MobilePushDelivery.id == delivery_id,
                MobilePushDelivery.status.in_(["pending", "sending"]),
                MobilePushDelivery.next_attempt <= utc_now(),
            ).values(status="sending", lease_id=lease,
                     next_attempt=utc_now() + timedelta(seconds=90),
                     attempts=MobilePushDelivery.attempts + 1)).rowcount
            db.commit()
            if not claimed:
                continue
            row = db.get(MobilePushDelivery, delivery_id)
            device = db.get(MobileDevice, row.device_id)
            store = db.get(Store, device.store_id) if device else None
            payload = json.loads(row.payload)
            # 등록 이후 업무/매장이 바뀐 보류 알림도 발송 직전에 다시 거른다.
            skip = (row.expires_at <= utc_now() or not device or not device.active or
                    device.binding_id != row.binding_id or device.expires_at <= utc_now() or
                    device.service_type not in VALID_MOBILE_SERVICES or
                    payload.get("store_id") != str(device.store_id) or
                    payload.get("service_type") != device.service_type or
                    payload.get("binding_id") != device.binding_id or
                    not store or not store.is_active)
            token = device.token if device else ""
            ttl = max(1, int((row.expires_at - utc_now()).total_seconds()))
            attempts = row.attempts

        status, error, invalid_token = ("skipped", "", False) if skip else ("sent", "", False)
        if not skip:
            try:
                app.state.mobile_sender(token, payload, ttl)
            except Exception as exc:
                # 에러 메시지 원문은 토큰/키를 포함할 수 있으므로 클래스명만 보관.
                error = type(exc).__name__[:100]
                invalid_token = error == "UnregisteredError"
                status = "failed" if invalid_token or attempts >= 5 else "pending"
                log.warning("Manager FCM send deferred: %s (attempt %s)", error, attempts)
        with app.state.SessionLocal() as db:
            result = db.execute(update(MobilePushDelivery).where(
                MobilePushDelivery.id == delivery_id, MobilePushDelivery.lease_id == lease,
            ).values(status=status, last_error=error, lease_id=None,
                     next_attempt=utc_now() + timedelta(seconds=min(60, 2 ** attempts))))
            if result.rowcount and invalid_token:
                # 새 토큰으로 이미 갱신됐다면 구독을 잘못 끄지 않는다.
                db.execute(update(MobileDevice).where(
                    MobileDevice.id == row.device_id, MobileDevice.token == token,
                    MobileDevice.binding_id == row.binding_id,
                ).values(active=False))
            db.commit()
        processed += 1
    return processed


async def mobile_push_loop(app) -> None:
    cleanup_at = utc_now()
    while True:
        try:
            await asyncio.to_thread(process_mobile_outbox, app)
            app.state.mobile_worker_error = ""
            if utc_now() >= cleanup_at:
                await asyncio.to_thread(_cleanup, app)
                cleanup_at = utc_now() + timedelta(hours=1)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            app.state.mobile_worker_error = type(exc).__name__
            log.warning("Manager FCM worker retry: %s", type(exc).__name__)
        await asyncio.sleep(1)


def _cleanup(app) -> None:
    with app.state.SessionLocal() as db:
        db.query(MobilePushDelivery).filter(
            MobilePushDelivery.created_at < utc_now() - timedelta(days=7)
        ).delete(synchronize_session=False)
        db.query(MobileDevice).filter(MobileDevice.expires_at <= utc_now()).update(
            {MobileDevice.active: False}, synchronize_session=False)
        db.commit()


class RegisterBody(BaseModel):
    installation_id: str = Field(min_length=16, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    token: str = Field(min_length=20, max_length=4096)
    store_id: int = Field(gt=0)
    service_type: Literal["simple_service", "purchase_consult"]
    firebase_project_id: str = Field(default="", max_length=160)


class RefreshBody(BaseModel):
    token: str = Field(min_length=20, max_length=4096)


class TestBody(BaseModel):
    binding_id: str = Field(min_length=20, max_length=64)


def _credential_device(request: Request, db: Session) -> MobileDevice:
    authorization = request.headers.get("authorization", "")
    parts = authorization.split(" ", 1)
    credential = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""
    binding_id, dot, secret = credential.partition(".")
    if not dot or len(binding_id) > 64 or len(secret) > 128:
        raise HTTPException(401, "단말 인증이 필요합니다")
    row = db.scalar(select(MobileDevice).where(MobileDevice.binding_id == binding_id))
    if (row is None or not row.active or row.expires_at <= utc_now() or
            row.service_type not in VALID_MOBILE_SERVICES or
            not secrets.compare_digest(row.secret_hash, _secret_hash(secret))):
        raise HTTPException(401, "앱에서 관리자 인증을 다시 진행하세요")
    store = db.get(Store, row.store_id)
    if store is None or not store.is_active:
        raise HTTPException(401, "현재 사용할 수 없는 매장입니다")
    return row


def register_mobile_routes(app, require_admin: Callable) -> None:
    def csrf_guard(request: Request) -> None:
        # 모바일 API만 강화. 기존 브라우저/발급 API의 동작은 변경하지 않는다.
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlsplit
            parsed = urlsplit(origin)
            if parsed.netloc.lower() != request.headers.get("host", "").lower():
                raise HTTPException(403, "다른 사이트에서 요청할 수 없습니다")

    admin_deps = [Depends(require_admin), Depends(csrf_guard)]

    @app.get("/api/admin/mobile/status", dependencies=admin_deps)
    def mobile_status(request: Request) -> dict:
        sender = request.app.state.mobile_sender
        settings = request.app.state.mobile_settings
        return {"enabled": settings.enabled,
                "configured": bool(getattr(sender, "ready", callable(sender))),
                "project_id": getattr(sender, "project_id", settings.project_id),
                "error": getattr(sender, "error", "") or request.app.state.mobile_worker_error,
                "registration_days": settings.registration_days,
                "notification_scope": NOTIFICATION_SCOPE}

    @app.post("/api/admin/mobile/register", dependencies=admin_deps)
    def mobile_register(body: RegisterBody, request: Request, db: Session = Depends(get_db)) -> dict:
        settings = request.app.state.mobile_settings
        if not settings.enabled:
            raise HTTPException(503, "서버 FCM_ENABLED 설정이 꺼져 있습니다")
        project_id = getattr(request.app.state.mobile_sender, "project_id", settings.project_id)
        if project_id and body.firebase_project_id and project_id != body.firebase_project_id:
            raise HTTPException(400, "앱 google-services.json과 서버 Firebase 프로젝트가 다릅니다")
        store = db.get(Store, body.store_id)
        if not store or not store.is_active:
            raise HTTPException(404, "현재 사용할 수 없는 매장입니다")
        row = db.scalar(select(MobileDevice).where(MobileDevice.installation_id == body.installation_id))
        if row is None:
            row = MobileDevice(installation_id=body.installation_id)
            db.add(row)
        # 동일 FCM 토큰이 다른 설치 식별자로 남아 있어도 한 번만 울리도록 한다.
        db.execute(update(MobileDevice).where(
            MobileDevice.token == body.token, MobileDevice.installation_id != body.installation_id,
        ).values(active=False))
        secret = secrets.token_urlsafe(32)
        row.binding_id = uuid.uuid4().hex
        row.secret_hash = _secret_hash(secret)
        row.store_id, row.token, row.active = store.id, body.token.strip(), True
        row.service_type = body.service_type
        row.expires_at = utc_now() + timedelta(days=settings.registration_days)
        db.commit()
        return {"ok": True, "binding_id": row.binding_id,
                "device_secret": secret, "store_id": store.id, "store_code": store.code or "",
                "service_type": row.service_type,
                "expires_at": row.expires_at.isoformat() + "Z", "notification_scope": NOTIFICATION_SCOPE}

    @app.post("/api/mobile/token")
    def mobile_refresh(body: RefreshBody, request: Request, db: Session = Depends(get_db)) -> dict:
        row = _credential_device(request, db)
        row.token = body.token.strip()
        db.execute(update(MobileDevice).where(
            MobileDevice.token == row.token, MobileDevice.id != row.id,
        ).values(active=False))
        # 백그라운드 토큰 갱신은 관리자 인증의 유효기간을 연장하지 않는다.
        db.commit()
        return {"ok": True}

    @app.post("/api/mobile/unregister")
    def mobile_unregister(request: Request, db: Session = Depends(get_db)) -> dict:
        row = _credential_device(request, db)
        row.active = False
        db.commit()
        return {"ok": True}

    @app.post("/api/admin/mobile/test", dependencies=admin_deps)
    def mobile_test(body: TestBody, request: Request, db: Session = Depends(get_db)) -> dict:
        settings = request.app.state.mobile_settings
        if not settings.enabled:
            raise HTTPException(503, "서버 FCM_ENABLED 설정이 꺼져 있습니다")
        row = db.scalar(select(MobileDevice).where(MobileDevice.binding_id == body.binding_id))
        if (not row or not row.active or row.expires_at <= utc_now()
                or row.service_type not in VALID_MOBILE_SERVICES):
            raise HTTPException(404, "이 단말의 알림 등록을 먼저 진행하세요")
        store = db.get(Store, row.store_id)
        if not store or not store.is_active:
            raise HTTPException(404, "매장을 찾을 수 없습니다")
        _queue_for_devices(db, settings, store, {
            "type": "test", "service_type": row.service_type, "ticket_number": "",
            "title": f"{store.name} · {SERVICE_META[row.service_type]['customer_label']} 알림 테스트",
            "body": "선택한 매장·업무의 Firebase 발급 알림이 연결되었습니다.",
        }, [row])
        db.commit()
        return {"ok": True, "queued": True}
