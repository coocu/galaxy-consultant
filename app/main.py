"""CodeNote 직원 호출 시스템 FastAPI 엔트리포인트."""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, time as datetime_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import inspect, or_, select, text
from sqlalchemy.orm import Session

from .backup import make_backup_zip, restore_from_zip_bytes
from .config import Settings, load_settings
from .database import Base, build_engine, build_session_factory, get_db
from .models import CallLog, Store
from .push import (
    disable_push_subscription,
    push_config_payload,
    save_push_subscription,
    send_ticket_push_notifications,
)
from .services import (
    CALL_DAILY_RESET,
    CALL_DIRECT,
    CALL_NORMAL,
    CALL_RECALL,
    SERVICE_META,
    SERVICE_PURCHASE,
    SERVICE_SIMPLE,
    call_next,
    call_to_dict,
    direct_call,
    get_store_state,
    issue_ticket,
    list_calls_after,
    recall_last,
    reset_service,
    store_to_dict,
    ticket_to_dict,
)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
ADMIN_SESSION_COOKIE = "codenote_staff_call_admin_session"
ADMIN_SESSION_SECONDS = 24 * 60 * 60
MANAGE_SESSION_COOKIE = "codenote_staff_call_manage_session"
MANAGE_SESSION_SECONDS = 10 * 60


class StoreEventBroker:
    """매장별 SSE 구독자에게 변경 이벤트를 전달하는 단일 프로세스 브로커."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: dict[int, set[asyncio.Queue[dict[str, object]]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, store_id: int) -> asyncio.Queue[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=100)
        with self._lock:
            self._subscribers.setdefault(int(store_id), set()).add(queue)
        return queue

    def unsubscribe(self, store_id: int, queue: asyncio.Queue[dict[str, object]]) -> None:
        with self._lock:
            queues = self._subscribers.get(int(store_id))
            if not queues:
                return
            queues.discard(queue)
            if not queues:
                self._subscribers.pop(int(store_id), None)

    def publish(self, store_id: int, payload: dict[str, object]) -> None:
        with self._lock:
            queues = list(self._subscribers.get(int(store_id), set()))
        loop = self._loop
        if not loop or not loop.is_running() or not queues:
            return
        for queue in queues:
            loop.call_soon_threadsafe(self._offer, queue, payload)

    @staticmethod
    def _offer(queue: asyncio.Queue[dict[str, object]], payload: dict[str, object]) -> None:
        if queue.full():
            with suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        queue.put_nowait(payload)


def _format_sse(event_name: str, payload: dict[str, object]) -> str:
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event_name}\ndata: {body}\n\n"


def _publish_store_event(app: FastAPI, store_id: int, payload: dict[str, object]) -> None:
    broker = getattr(app.state, "store_event_broker", None)
    if isinstance(broker, StoreEventBroker):
        broker.publish(store_id, payload)


KST = ZoneInfo("Asia/Seoul")


def run_daily_reset_if_needed(app: FastAPI, now_kst: datetime | None = None) -> int:
    """한국시간 기준 오늘 자동 초기화가 아직 안 된 매장/업무만 초기화한다."""
    current_kst = now_kst or datetime.now(KST)
    if current_kst.tzinfo is None:
        current_kst = current_kst.replace(tzinfo=KST)
    else:
        current_kst = current_kst.astimezone(KST)

    kst_midnight = datetime.combine(current_kst.date(), datetime_time.min, tzinfo=KST)
    utc_midnight_naive = kst_midnight.astimezone(UTC).replace(tzinfo=None)

    db: Session = app.state.SessionLocal()
    reset_count = 0
    try:
        store_ids = list(db.execute(select(Store.id)).scalars())
        for store_id in store_ids:
            reset_services: list[str] = []
            for service_type in (SERVICE_SIMPLE, SERVICE_PURCHASE):
                already_reset = db.execute(
                    select(CallLog.id)
                    .where(
                        CallLog.store_id == store_id,
                        CallLog.service_type == service_type,
                        CallLog.call_type == CALL_DAILY_RESET,
                        CallLog.created_at >= utc_midnight_naive,
                    )
                    .limit(1)
                ).scalar_one_or_none()
                if already_reset is not None:
                    continue
                reset_service(db, store_id, service_type, call_type=CALL_DAILY_RESET)
                reset_count += 1
                reset_services.append(service_type)

            if reset_services:
                state_payload = get_store_state(db, store_id)
                for service_type in reset_services:
                    _publish_store_event(app, store_id, {
                        "type": "service_reset",
                        "service_type": service_type,
                        "state": state_payload,
                    })
    finally:
        db.close()
    return reset_count


async def _daily_reset_loop(app: FastAPI) -> None:
    while True:
        await asyncio.to_thread(run_daily_reset_if_needed, app)
        now_kst = datetime.now(KST)
        next_midnight = datetime.combine(
            now_kst.date() + timedelta(days=1),
            datetime_time.min,
            tzinfo=KST,
        )
        seconds_until_midnight = max(1.0, (next_midnight - now_kst).total_seconds() + 1.0)
        await asyncio.sleep(seconds_until_midnight)


class LoginBody(BaseModel):
    key: str = Field(min_length=1)


class StoreCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=40)


class StoreUpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=40)
    is_active: bool | None = None


class TicketCreateBody(BaseModel):
    store_id: int = Field(gt=0)
    service_type: str


class CallBody(BaseModel):
    store_id: int = Field(gt=0)
    service_type: str
    call_type: str = Field(pattern="^(normal|recall|direct)$")
    ticket_number: int | None = Field(default=None, ge=1)


class ResetBody(BaseModel):
    store_id: int = Field(gt=0)
    service_type: str


class PushKeysBody(BaseModel):
    p256dh: str = Field(min_length=1)
    auth: str = Field(min_length=1)


class PushSubscriptionBody(BaseModel):
    endpoint: str = Field(min_length=1)
    keys: PushKeysBody


class PushSubscribeBody(BaseModel):
    store_id: int = Field(gt=0)
    subscription: PushSubscriptionBody


class PushUnsubscribeBody(BaseModel):
    endpoint: str = Field(min_length=1)


def _cleanup_sessions(session_store: dict[str, float]) -> None:
    now = time.time()
    expired = [token for token, expires_at in session_store.items() if expires_at <= now]
    for token in expired:
        session_store.pop(token, None)


def _issue_admin_session(app: FastAPI) -> str:
    with app.state.admin_session_lock:
        _cleanup_sessions(app.state.admin_sessions)
        token = secrets.token_urlsafe(32)
        app.state.admin_sessions[token] = time.time() + ADMIN_SESSION_SECONDS
        return token


def _issue_manage_session(app: FastAPI) -> str:
    with app.state.manage_session_lock:
        _cleanup_sessions(app.state.manage_sessions)
        token = secrets.token_urlsafe(32)
        app.state.manage_sessions[token] = time.time() + MANAGE_SESSION_SECONDS
        return token


def _has_admin_session(request: Request) -> bool:
    token = request.cookies.get(ADMIN_SESSION_COOKIE, "")
    with request.app.state.admin_session_lock:
        _cleanup_sessions(request.app.state.admin_sessions)
        return bool(token and token in request.app.state.admin_sessions)


def _has_manage_session(request: Request) -> bool:
    token = request.cookies.get(MANAGE_SESSION_COOKIE, "")
    with request.app.state.manage_session_lock:
        _cleanup_sessions(request.app.state.manage_sessions)
        return bool(token and token in request.app.state.manage_sessions)


def require_admin(request: Request) -> None:
    if not _has_admin_session(request):
        raise HTTPException(status_code=401, detail="관리자 인증이 필요합니다")


def require_store_manager(request: Request) -> None:
    if not _has_manage_session(request):
        raise HTTPException(status_code=401, detail="매장 관리 인증이 필요합니다")


def require_admin_or_store_manager(request: Request) -> None:
    if not (_has_admin_session(request) or _has_manage_session(request)):
        raise HTTPException(status_code=401, detail="관리자 또는 매장 관리 인증이 필요합니다")


def api_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def normalize_store_code(code: str | None) -> str | None:
    normalized = (code or "").strip().upper()
    if not normalized:
        return None
    return normalized


def ensure_store_code_unique(db: Session, code: str, exclude_store_id: int | None = None) -> None:
    query = select(Store).where(Store.code == code)
    if exclude_store_id is not None:
        query = query.where(Store.id != exclude_store_id)
    existing = db.execute(query.limit(1)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=400, detail="이미 등록된 점코드입니다")


def ensure_store_code_schema(engine) -> None:
    inspector = inspect(engine)
    if "stores" not in inspector.get_table_names():
        return
    column_names = {column["name"] for column in inspector.get_columns("stores")}
    with engine.begin() as connection:
        if "code" not in column_names:
            connection.execute(text("ALTER TABLE stores ADD COLUMN code VARCHAR(40)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_stores_code ON stores (code)"))


def seed_default_stores(db: Session, settings: Settings) -> None:
    if not settings.seed_default_stores:
        return
    exists = db.execute(select(Store.id).limit(1)).scalar_one_or_none()
    if exists is not None:
        return
    db.add(Store(name="기본 매장", code=None))
    db.commit()


def _check_poket_auth(auth_check_url: str, code: str) -> dict[str, object]:
    payload = json.dumps({"code": code}).encode("utf-8")
    req = urllib_request.Request(
        auth_check_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=10) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        if body:
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = {}
            detail = data.get("detail") or data.get("message") or "인증 서버에서 거절되었습니다"
            raise HTTPException(status_code=401, detail=detail) from exc
        raise HTTPException(status_code=502, detail="인증 서버 오류입니다") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=502, detail="인증 서버에 연결할 수 없습니다") from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="인증 서버 응답 형식이 올바르지 않습니다") from exc
    return data if isinstance(data, dict) else {}


def _verify_admin_key(app: FastAPI, code: str) -> None:
    auth_key = (code or "").strip()
    if not auth_key:
        raise HTTPException(status_code=401, detail="인증키를 입력하세요")

    auth_checker = getattr(app.state, "auth_checker", None)
    if callable(auth_checker):
        result = auth_checker(auth_key)
    else:
        result = _check_poket_auth(app.state.settings.poket_auth_check_url, auth_key)

    if not isinstance(result, dict) or result.get("status") != "approved" or not result.get("token"):
        raise HTTPException(status_code=401, detail="인증키가 올바르지 않습니다")


def _verify_manage_key(app: FastAPI, code: str) -> None:
    auth_key = (code or "").strip()
    if "kiosk" not in auth_key.lower():
        raise HTTPException(status_code=401, detail="매장 관리전용 인증키를 입력해 주세요")
    _verify_admin_key(app, auth_key)


def _set_admin_cookie(response: JSONResponse, request: Request, token: str) -> None:
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        token,
        max_age=ADMIN_SESSION_SECONDS,
        httponly=True,
        secure=(not request.app.state.settings.testing and forwarded_proto == "https"),
        samesite="lax",
        path="/",
    )


def _set_manage_cookie(response: JSONResponse, request: Request, token: str) -> None:
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(
        MANAGE_SESSION_COOKIE,
        token,
        max_age=MANAGE_SESSION_SECONDS,
        httponly=True,
        secure=(not request.app.state.settings.testing and forwarded_proto == "https"),
        samesite="lax",
        path="/",
    )


def create_app(test_config: dict | None = None) -> FastAPI:
    test_config = test_config or {}
    settings = load_settings(test_config)
    engine = build_engine(settings.database_url)
    session_local = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _app.state.store_event_broker.set_loop(asyncio.get_running_loop())
        Base.metadata.create_all(bind=engine)
        ensure_store_code_schema(engine)
        db = session_local()
        try:
            seed_default_stores(db, settings)
        finally:
            db.close()

        daily_reset_task = None
        if not settings.testing:
            daily_reset_task = asyncio.create_task(_daily_reset_loop(_app))
        try:
            yield
        finally:
            if daily_reset_task is not None:
                daily_reset_task.cancel()
                with suppress(asyncio.CancelledError):
                    await daily_reset_task

    app = FastAPI(title="CodeNote 직원 호출 시스템", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.SessionLocal = session_local
    app.state.admin_sessions = {}
    app.state.admin_session_lock = threading.RLock()
    app.state.manage_sessions = {}
    app.state.manage_session_lock = threading.RLock()
    app.state.auth_checker = test_config.get("AUTH_CHECKER")
    app.state.push_sender = test_config.get("PUSH_SENDER")
    app.state.store_event_broker = StoreEventBroker()

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.get("/sw.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        return FileResponse(
            BASE_DIR / "static" / "sw.js",
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-cache",
                "Service-Worker-Allowed": "/",
            },
        )

    @app.get("/", response_class=HTMLResponse)
    @app.get("/customer", response_class=HTMLResponse)
    @app.get("/admin", response_class=HTMLResponse)
    @app.get("/display", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "service_meta": SERVICE_META,
            },
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/meta")
    def meta() -> dict[str, object]:
        return {"services": SERVICE_META}

    @app.get("/api/stores")
    def list_customer_stores(search: str = "", db: Session = Depends(get_db)) -> dict[str, object]:
        query = select(Store).where(Store.is_active.is_(True))
        if search.strip():
            keyword = f"%{search.strip()}%"
            query = query.where(or_(Store.name.ilike(keyword), Store.code.ilike(keyword)))
        stores = db.execute(query.order_by(Store.name.asc())).scalars().all()
        return {"stores": [store_to_dict(store) for store in stores]}

    @app.get("/api/stores/by-code/{store_code}")
    def get_store_by_code(store_code: str, db: Session = Depends(get_db)) -> dict[str, object]:
        code = normalize_store_code(store_code)
        if code is None:
            raise HTTPException(status_code=400, detail="점코드를 입력하세요")
        store = db.execute(
            select(Store).where(Store.code == code, Store.is_active.is_(True))
        ).scalar_one_or_none()
        if store is None:
            raise HTTPException(status_code=404, detail="등록되지 않은 점코드입니다")
        return {"store": store_to_dict(store)}

    @app.get("/api/admin/status")
    def admin_status(request: Request) -> dict[str, object]:
        return {"authenticated": _has_admin_session(request)}

    @app.post("/api/admin/login")
    def admin_login(body: LoginBody, request: Request) -> JSONResponse:
        _verify_admin_key(request.app, body.key)
        token = _issue_admin_session(request.app)
        response = JSONResponse({"ok": True, "message": "인증되었습니다"})
        _set_admin_cookie(response, request, token)
        return response

    @app.post("/api/admin/logout")
    def admin_logout(request: Request) -> JSONResponse:
        token = request.cookies.get(ADMIN_SESSION_COOKIE, "")
        with request.app.state.admin_session_lock:
            request.app.state.admin_sessions.pop(token, None)
        manage_token = request.cookies.get(MANAGE_SESSION_COOKIE, "")
        with request.app.state.manage_session_lock:
            request.app.state.manage_sessions.pop(manage_token, None)
        response = JSONResponse({"ok": True})
        response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
        response.delete_cookie(MANAGE_SESSION_COOKIE, path="/")
        return response

    @app.post("/api/admin/manage/login")
    def manage_login(body: LoginBody, request: Request) -> JSONResponse:
        _verify_manage_key(request.app, body.key)
        token = _issue_manage_session(request.app)
        response = JSONResponse({"ok": True, "message": "매장 관리 인증되었습니다"})
        _set_manage_cookie(response, request, token)
        return response

    @app.get("/api/admin/stores", dependencies=[Depends(require_admin_or_store_manager)])
    def list_admin_stores(search: str = "", db: Session = Depends(get_db)) -> dict[str, object]:
        query = select(Store)
        if search.strip():
            keyword = f"%{search.strip()}%"
            query = query.where(or_(Store.name.ilike(keyword), Store.code.ilike(keyword)))
        stores = db.execute(query.order_by(Store.is_active.desc(), Store.name.asc())).scalars().all()
        return {"stores": [store_to_dict(store) for store in stores]}

    @app.post("/api/admin/stores", status_code=201, dependencies=[Depends(require_store_manager)])
    def create_store(body: StoreCreateBody, db: Session = Depends(get_db)) -> dict[str, object]:
        name = body.name.strip()
        code = normalize_store_code(body.code)
        if not name:
            raise HTTPException(status_code=400, detail="매장명을 입력하세요")
        if code is None:
            raise HTTPException(status_code=400, detail="점코드를 입력하세요")
        ensure_store_code_unique(db, code)
        store = Store(name=name, code=code, is_active=True)
        db.add(store)
        db.commit()
        db.refresh(store)
        return {"store": store_to_dict(store)}

    @app.put("/api/admin/stores/{store_id}", dependencies=[Depends(require_store_manager)])
    def update_store(store_id: int, body: StoreUpdateBody, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
        store = db.get(Store, store_id)
        if store is None:
            raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다")
        if body.name is not None:
            name = body.name.strip()
            if not name:
                raise HTTPException(status_code=400, detail="매장명을 입력하세요")
            store.name = name
        if body.code is not None:
            code = normalize_store_code(body.code)
            if code is None:
                raise HTTPException(status_code=400, detail="점코드를 입력하세요")
            ensure_store_code_unique(db, code, exclude_store_id=store_id)
            store.code = code
        if body.is_active is not None:
            store.is_active = body.is_active
        db.commit()
        db.refresh(store)
        store_payload = store_to_dict(store)
        try:
            state_payload = get_store_state(db, store_id)
        except ValueError:
            state_payload = None
        _publish_store_event(request.app, store_id, {
            "type": "store_updated",
            "store": store_payload,
            "state": state_payload,
        })
        return {"store": store_payload}

    @app.delete("/api/admin/stores/{store_id}", dependencies=[Depends(require_store_manager)])
    def delete_store(store_id: int, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
        store = db.get(Store, store_id)
        if store is None:
            raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다")
        deleted = store_to_dict(store)
        db.delete(store)
        db.commit()
        _publish_store_event(request.app, store_id, {"type": "store_deleted", "store": deleted})
        return {"store": deleted, "message": "매장 카테고리가 삭제되었습니다"}

    @app.post("/api/tickets", status_code=201)
    def create_ticket(body: TicketCreateBody, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
        try:
            ticket = issue_ticket(db, body.store_id, body.service_type)
            ticket_payload = ticket_to_dict(ticket)
            state_payload = get_store_state(db, body.store_id)
        except ValueError as exc:
            raise api_error(exc) from exc
        push_result = send_ticket_push_notifications(request.app, db, ticket)
        _publish_store_event(request.app, body.store_id, {
            "type": "ticket_created",
            "ticket": ticket_payload,
            "state": state_payload,
            "push": push_result,
        })
        return {"ticket": ticket_payload, "state": state_payload, "push": push_result}

    @app.get("/api/state/{store_id}")
    def state(store_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
        try:
            payload = get_store_state(db, store_id)
        except ValueError as exc:
            raise api_error(exc) from exc
        return {"state": payload}

    @app.get("/api/events/{store_id}")
    async def store_events(store_id: int, request: Request) -> StreamingResponse:
        db = request.app.state.SessionLocal()
        try:
            initial_state = get_store_state(db, store_id)
        except ValueError as exc:
            raise api_error(exc) from exc
        finally:
            db.close()

        queue = request.app.state.store_event_broker.subscribe(store_id)

        async def event_stream():
            try:
                yield _format_sse("state", {"type": "state", "state": initial_state})
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event_payload = await asyncio.wait_for(queue.get(), timeout=25)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    event_name = str(event_payload.get("type") or "message")
                    yield _format_sse(event_name, event_payload)
            finally:
                request.app.state.store_event_broker.unsubscribe(store_id, queue)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/admin/call", dependencies=[Depends(require_admin)])
    def call_customer(body: CallBody, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
        try:
            if body.call_type == CALL_NORMAL:
                call = call_next(db, body.store_id, body.service_type)
            elif body.call_type == CALL_RECALL:
                call = recall_last(db, body.store_id, body.service_type)
            elif body.call_type == CALL_DIRECT:
                if body.ticket_number is None:
                    raise ValueError("지정호출 번호를 입력하세요")
                call = direct_call(db, body.store_id, body.service_type, body.ticket_number)
            else:
                raise ValueError("지원하지 않는 호출 방식입니다")
            call_payload = call_to_dict(call)
            state_payload = get_store_state(db, body.store_id)
        except ValueError as exc:
            raise api_error(exc) from exc
        _publish_store_event(request.app, body.store_id, {
            "type": "call_created",
            "call": call_payload,
            "state": state_payload,
        })
        return {"call": call_payload, "state": state_payload}

    @app.post("/api/admin/reset", dependencies=[Depends(require_admin)])
    def reset_queue(body: ResetBody, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
        try:
            reset_service(db, body.store_id, body.service_type)
            payload = get_store_state(db, body.store_id)
        except ValueError as exc:
            raise api_error(exc) from exc
        _publish_store_event(request.app, body.store_id, {
            "type": "service_reset",
            "service_type": body.service_type,
            "state": payload,
        })
        return {"state": payload, "message": "초기화되었습니다"}

    @app.get("/api/calls")
    def calls(store_id: int, after_id: int = 0, db: Session = Depends(get_db)) -> dict[str, object]:
        try:
            get_store_state(db, store_id)
        except ValueError as exc:
            raise api_error(exc) from exc
        return {"calls": list_calls_after(db, store_id, after_id)}

    @app.get("/api/push/vapid-public-key", dependencies=[Depends(require_admin)])
    def push_public_key(request: Request) -> dict[str, object]:
        return push_config_payload(request.app.state.settings)

    @app.post("/api/admin/push/subscribe", dependencies=[Depends(require_admin)])
    def subscribe_push(body: PushSubscribeBody, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
        try:
            subscription = save_push_subscription(
                db,
                store_id=body.store_id,
                subscription=body.subscription.model_dump(),
                user_agent=request.headers.get("user-agent", ""),
            )
        except ValueError as exc:
            raise api_error(exc) from exc
        return {
            "ok": True,
            "enabled": True,
            "store_id": subscription.store_id,
            "subscription_id": subscription.id,
        }

    @app.post("/api/admin/push/unsubscribe", dependencies=[Depends(require_admin)])
    def unsubscribe_push(body: PushUnsubscribeBody, db: Session = Depends(get_db)) -> dict[str, object]:
        try:
            disabled = disable_push_subscription(db, body.endpoint)
        except ValueError as exc:
            raise api_error(exc) from exc
        return {"ok": True, "disabled": disabled}

    @app.get("/api/admin/backup", dependencies=[Depends(require_store_manager)])
    def backup(db: Session = Depends(get_db)) -> Response:
        content = make_backup_zip(db)
        filename = f"codenote_staff_call_backup_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.zip"
        return Response(
            content=content,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/admin/restore", dependencies=[Depends(require_store_manager)])
    async def restore(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict[str, object]:
        if not file.filename.lower().endswith(".zip"):
            raise HTTPException(status_code=400, detail="ZIP 백업 파일만 업로드할 수 있습니다")
        try:
            result = restore_from_zip_bytes(db, await file.read())
        except ValueError as exc:
            raise api_error(exc) from exc
        return {"message": "복원이 완료되었습니다", "result": result}

    return app


app = create_app()
