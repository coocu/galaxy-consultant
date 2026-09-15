"""SQLAlchemy 모델."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    counters: Mapped[list["ServiceCounter"]] = relationship("ServiceCounter", back_populates="store", cascade="all, delete-orphan")
    tickets: Mapped[list["Ticket"]] = relationship("Ticket", back_populates="store", cascade="all, delete-orphan")
    call_logs: Mapped[list["CallLog"]] = relationship("CallLog", back_populates="store", cascade="all, delete-orphan")


class ServiceCounter(Base):
    __tablename__ = "service_counters"
    __table_args__ = (UniqueConstraint("store_id", "service_type", name="uq_store_service_counter"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    service_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    next_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    store: Mapped[Store] = relationship("Store", back_populates="counters")


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("store_id", "service_type", "round_no", "ticket_number", name="uq_ticket_number_per_round"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    service_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    ticket_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="waiting", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    called_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    store: Mapped[Store] = relationship("Store", back_populates="tickets")


class CallLog(Base):
    __tablename__ = "call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    service_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    ticket_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    call_type: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, index=True)

    store: Mapped[Store] = relationship("Store", back_populates="call_logs")

