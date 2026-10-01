"""Additive tables; originals stay in DATABASE_URL, not ephemeral local disk."""
from datetime import datetime
from sqlalchemy import String, Text, Integer, DateTime, LargeBinary, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from db import Base


class KnowledgeDocument(Base):
    __tablename__ = 'knowledge_documents'
    __table_args__ = (UniqueConstraint('project', 'sha256', name='uq_document_project_hash'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    project: Mapped[str] = mapped_column(String(255), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    original: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    source_message_id: Mapped[str] = mapped_column(String(128), unique=True)
    uploader_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default='EXTRACTING', index=True)
    summary: Mapped[str] = mapped_column(Text, default='')
    warnings: Mapped[str] = mapped_column(Text, default='[]')
    error: Mapped[str] = mapped_column(Text, default='')
    effective_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    replaces_id: Mapped[int | None] = mapped_column(ForeignKey('knowledge_documents.id'), nullable=True, unique=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class KnowledgeChunk(Base):
    __tablename__ = 'knowledge_chunks'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey('knowledge_documents.id', ondelete='CASCADE'), index=True)
    location: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    cells: Mapped[str] = mapped_column(Text, default='{}')


class DocumentImportSession(Base):
    __tablename__ = 'document_import_sessions'
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class DocumentCommandReceipt(Base):
    __tablename__ = 'document_command_receipts'
    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128))
    response: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DocumentProjectReview(Base):
    """Additive proposal table, leaves v0.6.55 document schema unchanged."""
    __tablename__ = 'document_project_reviews'
    document_id: Mapped[int] = mapped_column(ForeignKey('knowledge_documents.id', ondelete='CASCADE'), primary_key=True)
    proposed_project: Mapped[str | None] = mapped_column(String(255), nullable=True)
    candidates: Mapped[str] = mapped_column(Text, default='[]')
    evidence: Mapped[str] = mapped_column(Text, default='')


class DocumentAnalysis(Base):
    __tablename__ = 'document_analyses'
    document_id: Mapped[int] = mapped_column(ForeignKey('knowledge_documents.id', ondelete='CASCADE'), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DocumentChatContext(Base):
    __tablename__ = 'document_chat_contexts'
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey('knowledge_documents.id'), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    ambiguous: Mapped[bool] = mapped_column(default=False)
