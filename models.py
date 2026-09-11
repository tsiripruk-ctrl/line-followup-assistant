from datetime import datetime
from sqlalchemy import String, Text, DateTime, Float, Integer, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from db import Base


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_message_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    group_id: Mapped[str] = mapped_column(String(128), index=True)
    source_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    project: Mapped[str | None] = mapped_column(String(255), nullable=True)
    assignee_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    assignee_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="OPEN", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    next_reminder_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_created: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OutboundTaskMessage(Base):
    """LINE message IDs sent by the assistant and linked to a task.

    This lets us resolve a user's LINE quote-reply directly back to the exact task.
    """
    __tablename__ = "outbound_task_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_message_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[str] = mapped_column(String(128), index=True)
    message_kind: Mapped[str] = mapped_column(String(64), default="REMINDER")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class TaskEvent(Base):
    """Append-only timeline for a task."""
    __tablename__ = "task_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    actor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    old_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class Person(Base):
    """Canonical assignee identity."""
    __tablename__ = "people"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    line_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PersonAlias(Base):
    """Alias -> canonical person mapping. normalized_alias is used for matching."""
    __tablename__ = "person_aliases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(255))
    normalized_alias: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SystemEvent(Base):
    """Small idempotency log for recurring jobs such as daily briefs."""
    __tablename__ = "system_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
