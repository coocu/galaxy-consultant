"""CodeNote queue calling system.

Pure WSGI + SQLite so it can be deployed to Render with only Gunicorn.
Run locally:
    python app.py
Deploy start command:
    gunicorn app:application --bind 0.0.0.0:$PORT
"""
from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import sqlite3
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"
KST = ZoneInfo("Asia/Seoul")

QUEUE_TYPES = {"simple", "purchase"}
QUEUE_META = {
    "simple": {
        "customer_label": "간단서비스",
        "admin_label": "갤럭시 컨설턴트",
        "display_label": "간단서비스",
        "corner_label": "간단서비스 창구",
        "color": "blue",
    },
    "purchase": {
        "customer_label": "구매문의",
        "admin_label": "구매상담",
        "display_label": "구매상담",
        "corner_label": "구매상담 창구",
        "color": "red",
    },
}


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_db_path() -> Path:
    raw = os.environ.get("DB_PATH")
    if raw:
        return Path(raw)
    var_data = Path("/var/data")
    if var_data.exists():
        return var_data / "codenote_queue.db"
    return BASE_DIR / "data" / "codenote_queue.db"


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class Response:
    def __init__(self, status: str, headers: list[tuple[str, str]], body: bytes):
        self.status = status
        self.headers = headers
        self.body = body


def status_text(status_code: int) -> str:
    labels = {
        200: "OK",
        201: "Created",
        302: "Found",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        409: "Conflict",
        500: "Internal Server Error",
    }
    return f"{status_code} {labels.get(status_code, 'OK')}"


def json_response(payload: dict[str, Any], status: int = 200, extra_headers: list[tuple[str, str]] | None = None) -> Response:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")]
    if extra_headers:
        headers.extend(extra_headers)
    return Response(status_text(status), headers, body)


def html_response(html: str, status: int = 200, extra_headers: list[tuple[str, str]] | None = None) -> Response:
    headers = [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store")]
    if extra_headers:
        headers.extend(extra_headers)
    return Response(status_text(status), headers, html.encode("utf-8"))


def bytes_response(body: bytes, content_type: str, status: int = 200) -> Response:
    return Response(status_text(status), [("Content-Type", content_type), ("Cache-Control", "public, max-age=3600")], body)


def redirect_response(location: str, extra_headers: list[tuple[str, str]] | None = None) -> Response:
    headers = [("Location", location), ("Content-Type", "text/plain; charset=utf-8")]
    if extra_headers:
        headers.extend(extra_headers)
    return Response("302 Found", headers, b"")


def connect_db() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn




@contextmanager
def db_connection():
    conn = connect_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS stores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS queue_counters (
                store_id INTEGER NOT NULL,
                queue_type TEXT NOT NULL,
                last_number INTEGER NOT NULL DEFAULT 0,
                current_call_number INTEGER,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (store_id, queue_type),
                FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                queue_type TEXT NOT NULL,
                ticket_number INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'WAITING',
                issued_at TEXT NOT NULL,
                called_at TEXT,
                reset_at TEXT,
                FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_tickets_queue_status
                ON tickets(store_id, queue_type, status, id);

            CREATE TABLE IF NOT EXISTS call_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                queue_type TEXT NOT NULL,
                ticket_number INTEGER NOT NULL,
                call_kind TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_call_events_store_id
                ON call_events(store_id, id);
            """
        )
        count = conn.execute("SELECT COUNT(*) AS c FROM stores").fetchone()["c"]
        if count == 0:
            timestamp = now_iso()
            default_store = os.environ.get("DEFAULT_STORE_NAME", "CodeNote 테스트 매장")
            cursor = conn.execute(
                "INSERT INTO stores(name, is_active, sort_order, created_at, updated_at) VALUES (?, 1, 0, ?, ?)",
                (default_store, timestamp, timestamp),
            )
            store_id = cursor.lastrowid
            for queue_type in sorted(QUEUE_TYPES):
                conn.execute(
                    "INSERT INTO queue_counters(store_id, queue_type, last_number, current_call_number, updated_at) VALUES (?, ?, 0, NULL, ?)",
                    (store_id, queue_type, timestamp),
                )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def validate_queue_type(queue_type: str | None) -> str:
    if queue_type not in QUEUE_TYPES:
        raise ApiError("queue_type은 simple 또는 purchase만 가능합니다.", 400)
    return queue_type


def parse_int(value: Any, field_name: str, minimum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ApiError(f"{field_name} 값이 올바르지 않습니다.", 400)
    if minimum is not None and number < minimum:
        raise ApiError(f"{field_name} 값은 {minimum} 이상이어야 합니다.", 400)
    return number


def ensure_counter(conn: sqlite3.Connection, store_id: int, queue_type: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM queue_counters WHERE store_id = ? AND queue_type = ?",
        (store_id, queue_type),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO queue_counters(store_id, queue_type, last_number, current_call_number, updated_at) VALUES (?, ?, 0, NULL, ?)",
            (store_id, queue_type, now_iso()),
        )
        row = conn.execute(
            "SELECT * FROM queue_counters WHERE store_id = ? AND queue_type = ?",
            (store_id, queue_type),
        ).fetchone()
    return row


def get_store(conn: sqlite3.Connection, store_id: int, active_only: bool = False) -> sqlite3.Row:
    if active_only:
        row = conn.execute("SELECT * FROM stores WHERE id = ? AND is_active = 1", (store_id,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()
    if row is None:
        raise ApiError("매장을 찾을 수 없습니다.", 404)
    return row


def call_message(queue_type: str, ticket_number: int) -> str:
    corner = QUEUE_META[queue_type]["corner_label"]
    return f"{ticket_number}번 고객님. {corner}로 와주세요."


def create_call_event(conn: sqlite3.Connection, store_id: int, queue_type: str, ticket_number: int, call_kind: str) -> dict[str, Any]:
    message = call_message(queue_type, ticket_number)
    timestamp = now_iso()
    cursor = conn.execute(
        """
        INSERT INTO call_events(store_id, queue_type, ticket_number, call_kind, message, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (store_id, queue_type, ticket_number, call_kind, message, timestamp),
    )
    event_id = cursor.lastrowid
    return {
        "id": event_id,
        "store_id": store_id,
        "queue_type": queue_type,
        "ticket_number": ticket_number,
        "call_kind": call_kind,
        "message": message,
        "created_at": timestamp,
        "meta": QUEUE_META[queue_type],
    }


def get_request_body(environ: dict[str, Any]) -> dict[str, Any]:
    length = parse_int(environ.get("CONTENT_LENGTH") or 0, "CONTENT_LENGTH", 0)
    if length == 0:
        return {}
    raw = environ["wsgi.input"].read(length)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        raise ApiError("JSON 본문이 올바르지 않습니다.", 400)
    if not isinstance(parsed, dict):
        raise ApiError("JSON 본문은 객체여야 합니다.", 400)
    return parsed


def get_query(environ: dict[str, Any]) -> dict[str, list[str]]:
    return parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)


def query_first(query: dict[str, list[str]], key: str, default: str | None = None) -> str | None:
    values = query.get(key)
    if not values:
        return default
    return values[0]


def require_admin(environ: dict[str, Any]) -> None:
    admin_key = os.environ.get("ADMIN_KEY", "kyh")
    provided = environ.get("HTTP_X_ADMIN_KEY", "")
    if not hmac.compare_digest(provided, admin_key):
        raise ApiError("관리자 인증키가 필요합니다.", 401)


def cookie_dict(environ: dict[str, Any]) -> dict[str, str]:
    raw = environ.get("HTTP_COOKIE", "")
    parsed: dict[str, str] = {}
    for item in raw.split(";"):
        if "=" not in item:
            continue
        key, value = item.strip().split("=", 1)
        parsed[key] = value
    return parsed


def app_cookie_value() -> str:
    token = os.environ.get("APP_ACCESS_TOKEN", "codenote-app")
    secret = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    digest = hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"ok-{digest}"


def is_valid_app_access(environ: dict[str, Any], query: dict[str, list[str]]) -> bool:
    token = os.environ.get("APP_ACCESS_TOKEN", "codenote-app")
    from_query = query_first(query, "app_token")
    from_header = environ.get("HTTP_X_CODENOTE_APP", "")
    if from_query and hmac.compare_digest(from_query, token):
        return True
    if from_header and hmac.compare_digest(from_header, token):
        return True
    cookie_value = cookie_dict(environ).get("cn_app_ok")
    return bool(cookie_value and hmac.compare_digest(cookie_value, app_cookie_value()))


def app_only_guard(environ: dict[str, Any], path: str, query: dict[str, list[str]]) -> Response | None:
    if not env_bool("APP_ONLY_MODE", False):
        return None
    if path.startswith("/static/") or path == "/api/health":
        return None
    if is_valid_app_access(environ, query):
        if query_first(query, "app_token") and environ.get("REQUEST_METHOD") == "GET":
            clean_query = {k: v for k, v in query.items() if k != "app_token"}
            flat: list[tuple[str, str]] = []
            for key, values in clean_query.items():
                for value in values:
                    flat.append((key, value))
            clean_url = path + (("?" + urlencode(flat)) if flat else "")
            return redirect_response(
                clean_url,
                [("Set-Cookie", f"cn_app_ok={app_cookie_value()}; Path=/; HttpOnly; SameSite=Lax")],
            )
        return None
    locked = """
    <!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>CodeNote</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f3f5f8;font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#111827}.box{width:min(560px,calc(100% - 40px));background:#fff;border-radius:28px;padding:42px 30px;text-align:center;box-shadow:0 18px 50px rgba(15,23,42,.12)}h1{margin:0 0 14px;font-size:30px}p{margin:0;color:#6b7280;line-height:1.7}</style></head>
    <body><main class="box"><h1>앱 전용 화면입니다</h1><p>하이브리드 앱에서 발급된 접근 토큰으로 접속해야 사용할 수 있습니다.</p></main></body></html>
    """
    return html_response(locked, 403)


def api_health(environ: dict[str, Any]) -> Response:
    return json_response({"ok": True, "time": now_iso()})


def api_config(environ: dict[str, Any]) -> Response:
    return json_response(
        {
            "ok": True,
            "app_only_mode": env_bool("APP_ONLY_MODE", False),
            "queue_meta": QUEUE_META,
        }
    )


def api_stores(environ: dict[str, Any]) -> Response:
    query = get_query(environ)
    include_all = query_first(query, "all") == "1"
    if include_all:
        require_admin(environ)
    keyword = (query_first(query, "q", "") or "").strip()
    params: list[Any] = []
    where: list[str] = []
    if not include_all:
        where.append("is_active = 1")
    if keyword:
        where.append("name LIKE ?")
        params.append(f"%{keyword}%")
    sql = "SELECT * FROM stores"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY is_active DESC, sort_order ASC, id ASC"
    with db_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    stores = [row_to_dict(row) for row in rows]
    return json_response({"ok": True, "stores": stores})


def api_create_ticket(environ: dict[str, Any]) -> Response:
    payload = get_request_body(environ)
    store_id = parse_int(payload.get("store_id"), "store_id", 1)
    queue_type = validate_queue_type(payload.get("queue_type"))
    requested_ticket_number = payload.get("ticket_number")
    timestamp = now_iso()
    with db_connection() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            store = get_store(conn, store_id, active_only=True)
            counter = ensure_counter(conn, store_id, queue_type)

            if requested_ticket_number is None:
                ticket_number = int(counter["last_number"]) + 1
            else:
                ticket_number = parse_int(requested_ticket_number, "ticket_number", 1)

            next_counter_number = max(int(counter["last_number"]), ticket_number)
            conn.execute(
                "UPDATE queue_counters SET last_number = ?, updated_at = ? WHERE store_id = ? AND queue_type = ?",
                (next_counter_number, timestamp, store_id, queue_type),
            )
            cursor = conn.execute(
                """
                INSERT INTO tickets(store_id, queue_type, ticket_number, status, issued_at)
                VALUES (?, ?, ?, 'WAITING', ?)
                """,
                (store_id, queue_type, ticket_number, timestamp),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    ticket = {
        "id": cursor.lastrowid,
        "store_id": store_id,
        "store_name": store["name"],
        "queue_type": queue_type,
        "ticket_number": ticket_number,
        "status": "WAITING",
        "issued_at": timestamp,
        "meta": QUEUE_META[queue_type],
    }
    return json_response({"ok": True, "ticket": ticket})


def build_queue_state(conn: sqlite3.Connection, store_id: int, queue_type: str) -> dict[str, Any]:
    counter = ensure_counter(conn, store_id, queue_type)
    waiting_rows = conn.execute(
        """
        SELECT id, ticket_number, issued_at
        FROM tickets
        WHERE store_id = ? AND queue_type = ? AND status = 'WAITING'
        ORDER BY id ASC
        LIMIT 50
        """,
        (store_id, queue_type),
    ).fetchall()
    waiting_count = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM tickets
        WHERE store_id = ? AND queue_type = ? AND status = 'WAITING'
        """,
        (store_id, queue_type),
    ).fetchone()["c"]
    return {
        "queue_type": queue_type,
        "meta": QUEUE_META[queue_type],
        "last_issued_number": counter["last_number"],
        "current_call_number": counter["current_call_number"],
        "waiting_count": waiting_count,
        "waiting_list": [row_to_dict(row) for row in waiting_rows],
        "updated_at": counter["updated_at"],
    }


def api_state(environ: dict[str, Any]) -> Response:
    query = get_query(environ)
    raw_store_id = query_first(query, "store_id")
    with db_connection() as conn:
        if raw_store_id is None:
            row = conn.execute("SELECT id FROM stores WHERE is_active = 1 ORDER BY sort_order ASC, id ASC LIMIT 1").fetchone()
            if row is None:
                raise ApiError("등록된 매장이 없습니다.", 404)
            store_id = row["id"]
        else:
            store_id = parse_int(raw_store_id, "store_id", 1)
        store = get_store(conn, store_id, active_only=False)
        queues = {queue_type: build_queue_state(conn, store_id, queue_type) for queue_type in ("simple", "purchase")}
        last_event = conn.execute(
            "SELECT MAX(id) AS id FROM call_events WHERE store_id = ?",
            (store_id,),
        ).fetchone()["id"]
    return json_response(
        {
            "ok": True,
            "store": row_to_dict(store),
            "queues": queues,
            "last_event_id": last_event or 0,
            "server_time": now_iso(),
        }
    )


def api_call_next(environ: dict[str, Any]) -> Response:
    require_admin(environ)
    payload = get_request_body(environ)
    store_id = parse_int(payload.get("store_id"), "store_id", 1)
    queue_type = validate_queue_type(payload.get("queue_type"))
    timestamp = now_iso()
    with db_connection() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            get_store(conn, store_id, active_only=True)
            ensure_counter(conn, store_id, queue_type)
            ticket = conn.execute(
                """
                SELECT * FROM tickets
                WHERE store_id = ? AND queue_type = ? AND status = 'WAITING'
                ORDER BY id ASC
                LIMIT 1
                """,
                (store_id, queue_type),
            ).fetchone()
            if ticket is None:
                raise ApiError("대기 중인 고객이 없습니다.", 409)
            ticket_number = int(ticket["ticket_number"])
            conn.execute(
                "UPDATE tickets SET status = 'CALLED', called_at = ? WHERE id = ?",
                (timestamp, ticket["id"]),
            )
            conn.execute(
                "UPDATE queue_counters SET current_call_number = ?, updated_at = ? WHERE store_id = ? AND queue_type = ?",
                (ticket_number, timestamp, store_id, queue_type),
            )
            event = create_call_event(conn, store_id, queue_type, ticket_number, "NEXT")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return json_response({"ok": True, "call": event})


def api_recall(environ: dict[str, Any]) -> Response:
    require_admin(environ)
    payload = get_request_body(environ)
    store_id = parse_int(payload.get("store_id"), "store_id", 1)
    queue_type = validate_queue_type(payload.get("queue_type"))
    with db_connection() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            get_store(conn, store_id, active_only=True)
            counter = ensure_counter(conn, store_id, queue_type)
            if counter["current_call_number"] is None:
                raise ApiError("재호출할 번호가 없습니다.", 409)
            ticket_number = int(counter["current_call_number"])
            event = create_call_event(conn, store_id, queue_type, ticket_number, "RECALL")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return json_response({"ok": True, "call": event})


def api_direct_call(environ: dict[str, Any]) -> Response:
    require_admin(environ)
    payload = get_request_body(environ)
    store_id = parse_int(payload.get("store_id"), "store_id", 1)
    queue_type = validate_queue_type(payload.get("queue_type"))
    ticket_number = parse_int(payload.get("ticket_number"), "ticket_number", 1)
    timestamp = now_iso()
    with db_connection() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            get_store(conn, store_id, active_only=True)
            ensure_counter(conn, store_id, queue_type)
            conn.execute(
                """
                UPDATE tickets
                SET status = 'CALLED', called_at = COALESCE(called_at, ?)
                WHERE store_id = ? AND queue_type = ? AND ticket_number = ? AND status = 'WAITING'
                """,
                (timestamp, store_id, queue_type, ticket_number),
            )
            conn.execute(
                "UPDATE queue_counters SET current_call_number = ?, updated_at = ? WHERE store_id = ? AND queue_type = ?",
                (ticket_number, timestamp, store_id, queue_type),
            )
            event = create_call_event(conn, store_id, queue_type, ticket_number, "DIRECT")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return json_response({"ok": True, "call": event})


def api_reset(environ: dict[str, Any]) -> Response:
    require_admin(environ)
    payload = get_request_body(environ)
    store_id = parse_int(payload.get("store_id"), "store_id", 1)
    queue_type = validate_queue_type(payload.get("queue_type"))
    timestamp = now_iso()
    with db_connection() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            get_store(conn, store_id, active_only=True)
            ensure_counter(conn, store_id, queue_type)
            conn.execute(
                """
                UPDATE tickets
                SET status = 'RESET', reset_at = ?
                WHERE store_id = ? AND queue_type = ? AND status IN ('WAITING', 'CALLED')
                """,
                (timestamp, store_id, queue_type),
            )
            conn.execute(
                "UPDATE queue_counters SET last_number = 0, current_call_number = NULL, updated_at = ? WHERE store_id = ? AND queue_type = ?",
                (timestamp, store_id, queue_type),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return json_response({"ok": True, "reset": {"store_id": store_id, "queue_type": queue_type, "reset_at": timestamp}})


def api_call_events(environ: dict[str, Any]) -> Response:
    query = get_query(environ)
    store_id = parse_int(query_first(query, "store_id"), "store_id", 1)
    after = parse_int(query_first(query, "after", "0"), "after", 0)
    with db_connection() as conn:
        get_store(conn, store_id, active_only=False)
        rows = conn.execute(
            """
            SELECT * FROM call_events
            WHERE store_id = ? AND id > ?
            ORDER BY id ASC
            LIMIT 20
            """,
            (store_id, after),
        ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        item = row_to_dict(row) or {}
        item["meta"] = QUEUE_META[item["queue_type"]]
        events.append(item)
    return json_response({"ok": True, "events": events})


def api_admin_create_store(environ: dict[str, Any]) -> Response:
    require_admin(environ)
    payload = get_request_body(environ)
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ApiError("매장명을 입력하세요.", 400)
    sort_order = parse_int(payload.get("sort_order", 0), "sort_order", 0)
    is_active = 1 if bool(payload.get("is_active", True)) else 0
    timestamp = now_iso()
    with db_connection() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "INSERT INTO stores(name, is_active, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (name, is_active, sort_order, timestamp, timestamp),
            )
            store_id = cursor.lastrowid
            for queue_type in sorted(QUEUE_TYPES):
                conn.execute(
                    "INSERT INTO queue_counters(store_id, queue_type, last_number, current_call_number, updated_at) VALUES (?, ?, 0, NULL, ?)",
                    (store_id, queue_type, timestamp),
                )
            store = conn.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return json_response({"ok": True, "store": row_to_dict(store)})


def api_admin_update_store(environ: dict[str, Any], store_id: int) -> Response:
    require_admin(environ)
    payload = get_request_body(environ)
    with db_connection() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            get_store(conn, store_id, active_only=False)
            name = str(payload.get("name", "")).strip()
            if not name:
                raise ApiError("매장명을 입력하세요.", 400)
            sort_order = parse_int(payload.get("sort_order", 0), "sort_order", 0)
            is_active = 1 if bool(payload.get("is_active", True)) else 0
            conn.execute(
                "UPDATE stores SET name = ?, sort_order = ?, is_active = ?, updated_at = ? WHERE id = ?",
                (name, sort_order, is_active, now_iso(), store_id),
            )
            store = conn.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return json_response({"ok": True, "store": row_to_dict(store)})


def api_admin_delete_store(environ: dict[str, Any], store_id: int) -> Response:
    require_admin(environ)
    with db_connection() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            get_store(conn, store_id, active_only=False)
            conn.execute("UPDATE stores SET is_active = 0, updated_at = ? WHERE id = ?", (now_iso(), store_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return json_response({"ok": True, "deleted": {"store_id": store_id}})


def route_api(environ: dict[str, Any], method: str, path: str) -> Response:
    if path == "/api/health" and method == "GET":
        return api_health(environ)
    if path == "/api/config" and method == "GET":
        return api_config(environ)
    if path == "/api/stores" and method == "GET":
        return api_stores(environ)
    if path == "/api/tickets" and method == "POST":
        return api_create_ticket(environ)
    if path == "/api/state" and method == "GET":
        return api_state(environ)
    if path == "/api/calls" and method == "GET":
        return api_call_events(environ)
    if path == "/api/admin/call/next" and method == "POST":
        return api_call_next(environ)
    if path == "/api/admin/call/recall" and method == "POST":
        return api_recall(environ)
    if path == "/api/admin/call/direct" and method == "POST":
        return api_direct_call(environ)
    if path == "/api/admin/reset" and method == "POST":
        return api_reset(environ)
    if path == "/api/admin/stores" and method == "POST":
        return api_admin_create_store(environ)
    if path.startswith("/api/admin/stores/"):
        raw_id = path.rsplit("/", 1)[-1]
        store_id = parse_int(raw_id, "store_id", 1)
        if method == "PUT":
            return api_admin_update_store(environ, store_id)
        if method == "DELETE":
            return api_admin_delete_store(environ, store_id)
    return json_response({"ok": False, "error": "API 경로를 찾을 수 없습니다."}, 404)


def serve_static(path: str) -> Response:
    relative = path.removeprefix("/static/")
    safe_path = (STATIC_DIR / relative).resolve()
    if not str(safe_path).startswith(str(STATIC_DIR.resolve())) or not safe_path.is_file():
        return html_response("Not found", 404)
    content_type = mimetypes.guess_type(str(safe_path))[0] or "application/octet-stream"
    return bytes_response(safe_path.read_bytes(), content_type)


def serve_index() -> Response:
    index_file = TEMPLATE_DIR / "index.html"
    return html_response(index_file.read_text(encoding="utf-8"), 200)


def application(environ: dict[str, Any], start_response: Callable[[str, list[tuple[str, str]]], None]):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/") or "/"
    query = get_query(environ)
    try:
        init_db()
        guarded = app_only_guard(environ, path, query)
        if guarded is not None:
            response = guarded
        elif path.startswith("/static/") and method == "GET":
            response = serve_static(path)
        elif path.startswith("/api/"):
            response = route_api(environ, method, path)
        elif method == "GET" and path in {"/", "/display", "/admin", "/customer"}:
            response = serve_index()
        else:
            response = html_response("Not found", 404)
    except ApiError as exc:
        response = json_response({"ok": False, "error": exc.message}, exc.status)
    except Exception as exc:  # pragma: no cover - defensive production fallback
        if env_bool("DEBUG", False):
            response = json_response({"ok": False, "error": str(exc), "traceback": traceback.format_exc()}, 500)
        else:
            response = json_response({"ok": False, "error": "서버 오류가 발생했습니다."}, 500)
    start_response(response.status, response.headers)
    return [response.body]


# Gunicorn reads this WSGI callable. Some hosts look for app by convention.
app = application


if __name__ == "__main__":
    from wsgiref.simple_server import make_server

    port = int(os.environ.get("PORT", "8000"))
    print(f"CodeNote queue server running on http://127.0.0.1:{port}")
    with make_server("0.0.0.0", port, application) as server:
        server.serve_forever()
