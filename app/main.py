"""CodeNote 직원 호출 시스템 FastAPI 엔트리포인트."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
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


def require_admin(
    request: Request,
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
) -> None:
    key = x_admin_key or request.query_params.get("admin_key")
    if key not in request.app.state.settings.admin_keys:
        raise HTTPException(status_code=401, detail="인증키가 올바르지 않습니다")


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


def create_app(test_config: dict | None = None) -> FastAPI:
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

    @app.post("/api/admin/login")
    def admin_login(body: LoginBody, request: Request) -> dict[str, object]:
        if body.key not in request.app.state.settings.admin_keys:
            raise HTTPException(status_code=401, detail="인증키가 올바르지 않습니다")
        return {"ok": True, "message": "인증되었습니다"}

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
