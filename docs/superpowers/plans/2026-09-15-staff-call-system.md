# Staff Call System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render에 바로 배포 가능한 직원 호출 시스템을 만들고, 고객/관리자/호출화면/매장관리/ZIP 백업복원 기능을 제공한다.

**Architecture:** FastAPI 서버가 HTML/CSS/JS 단일 앱과 JSON API를 제공한다. SQLAlchemy 모델은 매장, 업무별 번호 상태, 번호표, 호출 기록을 분리하며 PostgreSQL `DATABASE_URL`이 있으면 사용하고 없으면 SQLite를 사용한다. 실시간성은 1초 polling으로 구현해 Render 단일 웹서비스에서 안정적으로 동작하게 한다.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, SQLAlchemy, pytest, vanilla HTML/CSS/JavaScript.

**Spec:** Chat conversation requirements from 2026-09-15.

## Global Constraints

- 메인 UI는 고객/관리자 버튼 2개로 시작한다.
- 고객은 매장 검색/선택 후 간단서비스 또는 구매문의 번호표를 발급한다.
- 관리자는 인증키 입력 후 매장 검색/선택, 갤럭시 컨설턴트/구매상담 호출을 관리한다.
- 톱니바퀴는 인증키 재입력 후 매장 생성/수정/삭제/검색과 ZIP 백업/복원을 제공한다.
- 간단서비스는 파란색, 구매문의는 빨간색 계열로 표시한다.
- 호출, 재호출, 지정호출, 업무별 초기화를 제공한다.
- 초기화는 선택 업무만 0으로 돌리고 다음 번호표를 1번부터 발급한다.
- 호출 시 중앙 팝업과 브라우저 음성 안내를 제공한다.
- 번호표 출력 연동 위치는 실제 구현하지 않고 주석으로 남긴다.
- 모바일과 PC 웹 모두 반응형으로 깨지지 않게 구성한다.

---

### Task 1: FastAPI application factory and database models

**Files:**
- Create: `app/__init__.py`
- Create: `app/main.py`
- Create: `app/database.py`
- Create: `app/config.py`
- Create: `app/models.py`
- Test: `tests/test_queue_logic.py`

**Interfaces:**
- Produces: `create_app(test_config: dict | None = None) -> FastAPI`
- Produces: `Store`, `ServiceCounter`, `Ticket`, `CallLog`

- [ ] Write failing tests for app creation, default store seed, and isolated service counters.
- [ ] Run `pytest tests/test_queue_logic.py -v` and verify import failure.
- [ ] Implement app factory, config, and models.
- [ ] Run tests and verify they pass.

### Task 2: Queue service behavior

**Files:**
- Create: `app/services.py`
- Modify: `app/__init__.py`
- Test: `tests/test_queue_logic.py`

**Interfaces:**
- Produces: `issue_ticket(store_id: int, service_type: str) -> Ticket`
- Produces: `call_next(store_id: int, service_type: str) -> CallLog`
- Produces: `recall_last(store_id: int, service_type: str) -> CallLog`
- Produces: `direct_call(store_id: int, service_type: str, ticket_number: int) -> CallLog`
- Produces: `reset_service(store_id: int, service_type: str) -> ServiceCounter`
- Produces: `get_store_state(store_id: int) -> dict`

- [ ] Write failing tests for issue/call/recall/direct/reset.
- [ ] Run `pytest tests/test_queue_logic.py -v` and verify missing service functions.
- [ ] Implement service functions using counters and round numbers.
- [ ] Run tests and verify they pass.

### Task 3: JSON API and backup/restore

**Files:**
- Create: `app/routes.py`
- Create: `app/backup.py`
- Modify: `app/__init__.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `/api/stores`, `/api/admin/login`, `/api/admin/stores`, `/api/tickets`, `/api/state/<store_id>`, `/api/admin/call`, `/api/admin/reset`, `/api/admin/backup`, `/api/admin/restore`, `/api/calls`

- [ ] Write failing API tests for store CRUD/search, ticket/call state, backup/restore.
- [ ] Run `pytest tests/test_api.py -v` and verify API routes missing.
- [ ] Implement API routes and ZIP backup/restore.
- [ ] Run tests and verify they pass.

### Task 4: Responsive UI

**Files:**
- Create: `app/templates/index.html`
- Create: `app/static/css/style.css`
- Create: `app/static/js/app.js`
- Modify: `app/routes.py`

**Interfaces:**
- Produces: `/`, `/customer`, `/admin`, `/display`

- [ ] Implement responsive single-page UI using vanilla JS.
- [ ] Add store management modal with backup/restore controls.
- [ ] Add display polling, call popup, and speech synthesis.
- [ ] Add print integration placeholder comments.

### Task 5: Deployment package

**Files:**
- Create: `requirements.txt`
- Create: `render.yaml`
- Create: `README.md`
- Create: `runtime.txt`

**Interfaces:**
- Produces: Uvicorn command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

- [ ] Add dependency and Render configuration files.
- [ ] Run `pytest -q`.
- [ ] Create ZIP excluding cache files.
