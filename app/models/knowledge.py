"""SQLAlchemy ORM models for the Knowledge Base module."""
from __future__ import annotations

import uuid
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, DateTime,
    ForeignKey, Integer, String, Table, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import relationship

from app.database import Base


# ── Many-to-many: articles ↔ tags ─────────────────────────────────────────────
kb_article_tags = Table(
    "kb_article_tags",
    Base.metadata,
    Column("article_id", UUID(as_uuid=True), ForeignKey("kb_articles.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id",     UUID(as_uuid=True), ForeignKey("kb_tags.id",     ondelete="CASCADE"), primary_key=True),
)


class KBCategory(Base):
    __tablename__ = "kb_categories"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_id  = Column(UUID(as_uuid=True), ForeignKey("kb_categories.id", ondelete="CASCADE"), nullable=True)
    slug       = Column(String(255), nullable=False, unique=True)
    icon       = Column(String(100))
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    parent       = relationship("KBCategory", remote_side="KBCategory.id", back_populates="children")
    children     = relationship("KBCategory", back_populates="parent", cascade="all, delete-orphan", order_by="KBCategory.sort_order")
    translations = relationship("KBCategoryTranslation", back_populates="category", cascade="all, delete-orphan")
    articles     = relationship("KBArticle", back_populates="category")


class KBCategoryTranslation(Base):
    __tablename__ = "kb_category_translations"
    __table_args__ = (UniqueConstraint("category_id", "language"),)

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category_id = Column(UUID(as_uuid=True), ForeignKey("kb_categories.id", ondelete="CASCADE"), nullable=False)
    language    = Column(String(10), nullable=False)
    name        = Column(String(255), nullable=False)
    description = Column(Text)

    category = relationship("KBCategory", back_populates="translations")


class KBArticle(Base):
    __tablename__ = "kb_articles"
    __table_args__ = (
        CheckConstraint("status IN ('draft','review','approved','published','archived')", name="chk_kb_status"),
        CheckConstraint("visibility IN ('public','internal','agent_only','customer_specific')", name="chk_kb_visibility"),
    )

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category_id      = Column(UUID(as_uuid=True), ForeignKey("kb_categories.id", ondelete="SET NULL"), nullable=True)
    author_id        = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    slug             = Column(String(600), nullable=False, unique=True)
    status           = Column(String(20), nullable=False, default="draft")
    visibility       = Column(String(30), nullable=False, default="public")
    default_language = Column(String(10), nullable=False, default="en")
    reference_url    = Column(Text)
    sort_order       = Column(Integer, nullable=False, default=0)
    view_count       = Column(Integer, nullable=False, default=0)
    helpful_yes      = Column(Integer, nullable=False, default=0)
    helpful_no       = Column(Integer, nullable=False, default=0)
    published_at     = Column(DateTime(timezone=True))
    reviewed_by      = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    reviewed_at      = Column(DateTime(timezone=True))
    created_at       = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at       = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    category     = relationship("KBCategory", back_populates="articles")
    author       = relationship("User", foreign_keys=[author_id])
    reviewer     = relationship("User", foreign_keys=[reviewed_by])
    translations = relationship("KBArticleTranslation", back_populates="article", cascade="all, delete-orphan")
    versions     = relationship(
        "KBArticleVersion", back_populates="article",
        cascade="all, delete-orphan",
        order_by="KBArticleVersion.version_number.desc()",
    )
    tags         = relationship("KBTag", secondary=kb_article_tags, back_populates="articles")
    ticket_links = relationship("KBTicketArticleLink", back_populates="article", cascade="all, delete-orphan")
    feedback     = relationship("KBArticleFeedback", back_populates="article", cascade="all, delete-orphan")


class KBArticleTranslation(Base):
    __tablename__ = "kb_article_translations"
    __table_args__ = (UniqueConstraint("article_id", "language"),)

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    article_id    = Column(UUID(as_uuid=True), ForeignKey("kb_articles.id", ondelete="CASCADE"), nullable=False)
    language      = Column(String(10), nullable=False)
    title         = Column(String(500), nullable=False)
    content       = Column(Text, nullable=False)
    excerpt       = Column(Text)
    search_vector = Column(TSVECTOR)
    created_at    = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    article = relationship("KBArticle", back_populates="translations")


class KBArticleVersion(Base):
    __tablename__ = "kb_article_versions"
    __table_args__ = (UniqueConstraint("article_id", "version_number", "language"),)

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    article_id     = Column(UUID(as_uuid=True), ForeignKey("kb_articles.id", ondelete="CASCADE"), nullable=False)
    version_number = Column(Integer, nullable=False)
    language       = Column(String(10), nullable=False, default="en")
    title          = Column(String(500), nullable=False)
    content        = Column(Text, nullable=False)
    change_summary = Column(Text)
    changed_by     = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at     = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    article = relationship("KBArticle", back_populates="versions")
    author  = relationship("User", foreign_keys=[changed_by])


class KBTag(Base):
    __tablename__ = "kb_tags"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name       = Column(String(100), nullable=False, unique=True)
    color      = Column(String(20))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    articles = relationship("KBArticle", secondary=kb_article_tags, back_populates="tags")


class KBTicketArticleLink(Base):
    __tablename__ = "kb_ticket_article_links"
    __table_args__ = (
        UniqueConstraint("ticket_id", "article_id"),
        CheckConstraint("link_type IN ('related','resolved_by','referenced')", name="chk_kb_link_type"),
    )

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id  = Column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    article_id = Column(UUID(as_uuid=True), ForeignKey("kb_articles.id", ondelete="CASCADE"), nullable=False)
    linked_by  = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    link_type  = Column(String(30), nullable=False, default="related")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    article = relationship("KBArticle", back_populates="ticket_links")
    linker  = relationship("User", foreign_keys=[linked_by])


class KBArticleFeedback(Base):
    __tablename__ = "kb_article_feedback"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    article_id = Column(UUID(as_uuid=True), ForeignKey("kb_articles.id", ondelete="CASCADE"), nullable=False)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    is_helpful = Column(Boolean, nullable=False)
    comment    = Column(Text)
    ip_hash    = Column(String(64))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    article = relationship("KBArticle", back_populates="feedback")
