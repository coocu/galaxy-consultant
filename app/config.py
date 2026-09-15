"""환경설정 유틸리티."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    seed_default_stores: bool
    testing: bool
    poket_auth_check_url: str


def normalize_database_url(raw_url: str | None) -> str:
    """Render가 제공하는 postgres:// URL을 SQLAlchemy 2.x에서 쓰기 좋게 보정한다."""
    url = raw_url or "sqlite:///./staff_call.db"
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and "+" not in url.split("://", 1)[0]:
        return "postgresql+psycopg2://" + url[len("postgresql://") :]
    return url


def load_settings(overrides: dict | None = None) -> Settings:
    overrides = overrides or {}
    testing = bool(overrides.get("TESTING", False))
    database_url = normalize_database_url(
        overrides.get("DATABASE_URL")
        or overrides.get("SQLALCHEMY_DATABASE_URI")
        or os.environ.get("DATABASE_URL")
    )

    seed_default = overrides.get("SEED_DEFAULT_STORES")
    if seed_default is None:
        seed_default = not testing

    poket_auth_check_url = str(
        overrides.get("POKET_AUTH_CHECK_URL")
        or os.environ.get("POKET_AUTH_CHECK_URL")
        or "https://poketserver.onrender.com/app/check"
    )

    return Settings(
        database_url=database_url,
        seed_default_stores=bool(seed_default),
        testing=testing,
        poket_auth_check_url=poket_auth_check_url,
    )
