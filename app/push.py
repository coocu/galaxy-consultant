"""브라우저 Web Push 알림 저장/발송 유틸리티."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import PushSubscription, Store, Ticket
from .services import SERVICE_META, ticket_to_dict, validate_service_type


@dataclass(frozen=True)
class PushSendResult:
    ok: bool
    reason: str = ""


def push_enabled(settings: Settings) -> bool:
    return bool(settings.vapid_public_key and settings.vapid_private_key)


def push_config_payload(settings: Settings) -> dict[str, object]:
    enabled = push_enabled(settings)
    return {
        "enabled": enabled,
        "publicKey": settings.vapid_public_key if enabled else "",
        "message": "푸시 알림을 사용할 수 있습니다" if enabled else "VAPID_PUBLIC_KEY/VAPID_PRIVATE_KEY 환경변수가 필요합니다",
    }


def normalize_subscription_payload(subscription: dict[str, Any]) -> dict[str, str]:
    endpoint = str(subscription.get("endpoint") or "").strip()
    keys = subscription.get("keys") or {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        raise ValueError("푸시 구독 정보가 올바르지 않습니다")
    return {"endpoint": endpoint, "p256dh": p256dh, "auth": auth}


def subscription_to_webpush_info(subscription: PushSubscription) -> dict[str, object]:
    return {
        "endpoint": subscription.endpoint,
        "keys": {
            "p256dh": subscription.p256dh,
            "auth": subscription.auth,
        },
    }


def save_push_subscription(
    db: Session,
    *,
    store_id: int,
    subscription: dict[str, Any],
    user_agent: str = "",
) -> PushSubscription:
    store = db.get(Store, store_id)
    if store is None:
        raise ValueError("매장을 찾을 수 없습니다")
    if not store.is_active:
        raise ValueError("현재 사용할 수 없는 매장입니다")

    normalized = normalize_subscription_payload(subscription)
    row = db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == normalized["endpoint"])
    ).scalar_one_or_none()
    if row is None:
        row = PushSubscription(
            store_id=store_id,
            endpoint=normalized["endpoint"],
            p256dh=normalized["p256dh"],
            auth=normalized["auth"],
            user_agent=user_agent[:255],
            is_active=True,
        )
        db.add(row)
    else:
        row.store_id = store_id
        row.p256dh = normalized["p256dh"]
        row.auth = normalized["auth"]
        row.user_agent = user_agent[:255]
        row.is_active = True
    db.commit()
    db.refresh(row)
    return row


def disable_push_subscription(db: Session, endpoint: str) -> bool:
    endpoint = str(endpoint or "").strip()
    if not endpoint:
        raise ValueError("푸시 구독 endpoint가 필요합니다")
    row = db.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint)).scalar_one_or_none()
    if row is None:
        return False
    row.is_active = False
    db.commit()
    return True


def build_ticket_push_payload(ticket: Ticket, store_name: str) -> dict[str, Any]:
    validate_service_type(ticket.service_type)
    service = SERVICE_META[ticket.service_type]
    customer_label = service["customer_label"]
    body = f"{store_name} · {customer_label} {ticket.ticket_number}번 번호표가 발급되었습니다."
    return {
        "title": "새 대기번호 발급",
        "body": body,
        "tag": f"ticket-{ticket.store_id}-{ticket.service_type}",
        "renotify": True,
        "data": {
            "url": f"/admin?store_id={ticket.store_id}&service_type={ticket.service_type}",
            "store_id": ticket.store_id,
            "service_type": ticket.service_type,
            "ticket_number": ticket.ticket_number,
            "ticket": ticket_to_dict(ticket),
        },
    }


def default_push_sender(subscription_info: dict[str, object], payload: dict[str, Any], settings: Settings) -> dict[str, object]:
    if not push_enabled(settings):
        return {"ok": False, "reason": "push_not_configured"}
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return {"ok": False, "reason": "pywebpush_not_installed"}

    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},
        )
        return {"ok": True, "reason": "sent"}
    except WebPushException as exc:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        return {"ok": False, "reason": "webpush_error", "status_code": status_code, "message": str(exc)}
    except Exception as exc:  # pragma: no cover - 외부 푸시 서비스 예외 안전망
        return {"ok": False, "reason": "webpush_error", "message": str(exc)}


def send_ticket_push_notifications(app: Any, db: Session, ticket: Ticket) -> dict[str, object]:
    settings: Settings = app.state.settings
    store = db.get(Store, ticket.store_id)
    if store is None:
        return {"enabled": push_enabled(settings), "sent": 0, "failed": 0, "results": []}

    rows = list(
        db.execute(
            select(PushSubscription).where(
                PushSubscription.store_id == ticket.store_id,
                PushSubscription.is_active.is_(True),
            )
        ).scalars()
    )
    if not rows:
        return {"enabled": push_enabled(settings), "sent": 0, "failed": 0, "results": []}

    sender = getattr(app.state, "push_sender", None)
    if not callable(sender):
        sender = default_push_sender

    payload = build_ticket_push_payload(ticket, store.name)
    results: list[dict[str, object]] = []
    sent = 0
    failed = 0
    disabled_endpoints: list[str] = []

    for row in rows:
        subscription_info = subscription_to_webpush_info(row)
        result = sender(subscription_info, payload, settings)
        if not isinstance(result, dict):
            result = {"ok": bool(result)}
        ok = bool(result.get("ok"))
        if ok:
            sent += 1
        else:
            failed += 1
            if result.get("status_code") in {404, 410}:
                row.is_active = False
                disabled_endpoints.append(row.endpoint)
        results.append({
            "endpoint": row.endpoint,
            "ok": ok,
            "reason": result.get("reason", ""),
            "status_code": result.get("status_code"),
        })

    if disabled_endpoints:
        db.commit()

    return {
        "enabled": push_enabled(settings),
        "sent": sent,
        "failed": failed,
        "results": results,
    }
