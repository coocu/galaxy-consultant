"""CodeNote 직원 호출 시스템 FastAPI 엔트리포인트."""

from __future__ import annotations

import json
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .backup import make_backup_zip, restore_from_zip_bytes
from .config import Settings, load_settings
from .database import Base, build_engine, build_session_factory, get_db
from .models import Store
from .services import (
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
ADMIN_SESSION_SECONDS = 30 * 60


class LoginBody(BaseModel):
    key: str = Field(min_length=1)


class StoreCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class StoreUpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
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


def _cleanup_admin_sessions(app: FastAPI) -> None:
    now = time.time()
    expired = [token for token, expires_at in app.state.admin_sessions.items() if expires_at <= now]
    for token in expired:
        app.state.admin_sessions.pop(token, None)


def _issue_admin_session(app: FastAPI) -> str:
    with app.state.admin_session_lock:
        _cleanup_admin_sessions(app)
        token = secrets.token_urlsafe(32)
        app.state.admin_sessions[token] = time.time() + ADMIN_SESSION_SECONDS
        return token


def _has_admin_session(request: Request) -> bool:
    token = request.cookies.get(ADMIN_SESSION_COOKIE, "")
    with request.app.state.admin_session_lock:
        _cleanup_admin_sessions(request.app)
        return bool(token and token in request.app.state.admin_sessions)


def require_admin(request: Request) -> None:
    if not _has_admin_session(request):
        raise HTTPException(status_code=401, detail="관리자 인증이 필요합니다")


def api_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def seed_default_stores(db: Session, settings: Settings) -> None:
    if not settings.seed_default_stores:
        return
    exists = db.execute(select(Store.id).limit(1)).scalar_one_or_none()
    if exists is not None:
        return
    db.add(Store(name="기본 매장"))
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


def create_app(test_config: dict | None = None) -> FastAPI:
    test_config = test_config or {}
    settings = load_settings(test_config)
    engine = build_engine(settings.database_url)
    session_local = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        Base.metadata.create_all(bind=engine)
        db = session_local()
        try:
            seed_default_stores(db, settings)
        finally:
            db.close()
        yield

    app = FastAPI(title="CodeNote 직원 호출 시스템", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.SessionLocal = session_local
    app.state.admin_sessions = {}
    app.state.admin_session_lock = threading.RLock()
    app.state.auth_checker = test_config.get("AUTH_CHECKER")

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

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
            query = query.where(Store.name.ilike(f"%{search.strip()}%"))
        stores = db.execute(query.order_by(Store.name.asc())).scalars().all()
        return {"stores": [store_to_dict(store) for store in stores]}

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
        response = JSONResponse({"ok": True})
        response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
        return response

    @app.get("/api/admin/stores", dependencies=[Depends(require_admin)])
    def list_admin_stores(search: str = "", db: Session = Depends(get_db)) -> dict[str, object]:
        query = select(Store)
        if search.strip():
            query = query.where(Store.name.ilike(f"%{search.strip()}%"))
        stores = db.execute(query.order_by(Store.is_active.desc(), Store.name.asc())).scalars().all()
        return {"stores": [store_to_dict(store) for store in stores]}

    @app.post("/api/admin/stores", status_code=201, dependencies=[Depends(require_admin)])
    def create_store(body: StoreCreateBody, db: Session = Depends(get_db)) -> dict[str, object]:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="매장명을 입력하세요")
        store = Store(name=name, is_active=True)
        db.add(store)
        db.commit()
        db.refresh(store)
        return {"store": store_to_dict(store)}

    @app.put("/api/admin/stores/{store_id}", dependencies=[Depends(require_admin)])
    def update_store(store_id: int, body: StoreUpdateBody, db: Session = Depends(get_db)) -> dict[str, object]:
        store = db.get(Store, store_id)
        if store is None:
            raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다")
        if body.name is not None:
            name = body.name.strip()
            if not name:
                raise HTTPException(status_code=400, detail="매장명을 입력하세요")
            store.name = name
        if body.is_active is not None:
            store.is_active = body.is_active
        db.commit()
        db.refresh(store)
        return {"store": store_to_dict(store)}

    @app.delete("/api/admin/stores/{store_id}", dependencies=[Depends(require_admin)])
    def delete_store(store_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
        store = db.get(Store, store_id)
        if store is None:
            raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다")
        store.is_active = False
        db.commit()
        db.refresh(store)
        return {"store": store_to_dict(store), "message": "매장이 비활성화되었습니다"}

    @app.post("/api/tickets", status_code=201)
    def create_ticket(body: TicketCreateBody, db: Session = Depends(get_db)) -> dict[str, object]:
        try:
            ticket = issue_ticket(db, body.store_id, body.service_type)
        except ValueError as exc:
            raise api_error(exc) from exc
        return {"ticket": ticket_to_dict(ticket)}

    @app.get("/api/state/{store_id}")
    def state(store_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
        try:
            payload = get_store_state(db, store_id)
        except ValueError as exc:
            raise api_error(exc) from exc
        return {"state": payload}

    @app.post("/api/admin/call", dependencies=[Depends(require_admin)])
    def call_customer(body: CallBody, db: Session = Depends(get_db)) -> dict[str, object]:
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
        except ValueError as exc:
            raise api_error(exc) from exc
        return {"call": call_to_dict(call), "state": get_store_state(db, body.store_id)}

    @app.post("/api/admin/reset", dependencies=[Depends(require_admin)])
    def reset_queue(body: ResetBody, db: Session = Depends(get_db)) -> dict[str, object]:
        try:
            reset_service(db, body.store_id, body.service_type)
            payload = get_store_state(db, body.store_id)
        except ValueError as exc:
            raise api_error(exc) from exc
        return {"state": payload, "message": "초기화되었습니다"}

    @app.get("/api/calls")
    def calls(store_id: int, after_id: int = 0, db: Session = Depends(get_db)) -> dict[str, object]:
        try:
            get_store_state(db, store_id)
        except ValueError as exc:
            raise api_error(exc) from exc
        return {"calls": list_calls_after(db, store_id, after_id)}

    @app.get("/api/admin/backup", dependencies=[Depends(require_admin)])
    def backup(db: Session = Depends(get_db)) -> Response:
        content = make_backup_zip(db)
        filename = f"codenote_staff_call_backup_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.zip"
        return Response(
            content=content,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/admin/restore", dependencies=[Depends(require_admin)])
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
