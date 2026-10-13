"""운영 콘솔 데이터. 기존 DATABASE_URL과 같은 데이터베이스를 사용한다."""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, backref
from .database import Base
from .models import Store, Ticket, utc_now


class StoreProfile(Base):
    __tablename__ = 'console_store_profiles'
    store_id: Mapped[int] = mapped_column(ForeignKey('stores.id', ondelete='CASCADE'), primary_key=True)
    sales_team: Mapped[str] = mapped_column(String(80), default='', nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)
    store: Mapped[Store] = relationship(Store, backref=backref('console_profile', uselist=False, cascade='all, delete-orphan'))


class ConsoleAccount(Base):
    __tablename__ = 'console_accounts'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    store_id: Mapped[int | None] = mapped_column(ForeignKey('stores.id', ondelete='CASCADE'), unique=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    session_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)
    store: Mapped[Store | None] = relationship(Store, backref=backref('console_branch_account', uselist=False, cascade='all, delete-orphan'))


class ConsoleGrant(Base):
    __tablename__ = 'console_grants'
    account_id: Mapped[int] = mapped_column(ForeignKey('console_accounts.id', ondelete='CASCADE'), primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey('stores.id', ondelete='CASCADE'), primary_key=True)
    account: Mapped[ConsoleAccount] = relationship(ConsoleAccount, backref=backref('grants', cascade='all, delete-orphan'))
    store: Mapped[Store] = relationship(Store, backref=backref('console_grants', cascade='all, delete-orphan'))


class TrafficEvent(Base):
    __tablename__ = 'console_traffic_events'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey('stores.id', ondelete='CASCADE'), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(120), default='direct', nullable=False)
    path: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)
    store: Mapped[Store] = relationship(Store, backref=backref('console_traffic', cascade='all, delete-orphan'))


class TicketAttribution(Base):
    __tablename__ = 'console_ticket_attribution'
    ticket_id: Mapped[int] = mapped_column(ForeignKey('tickets.id', ondelete='CASCADE'), primary_key=True)
    source: Mapped[str] = mapped_column(String(120), default='direct', nullable=False)
    ticket: Mapped[Ticket] = relationship(Ticket, backref=backref('attribution', uselist=False, cascade='all, delete-orphan'))


class ConsoleAudit(Base):
    __tablename__ = 'console_audit'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default='', nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False, index=True)
