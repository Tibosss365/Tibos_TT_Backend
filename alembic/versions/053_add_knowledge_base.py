"""Add Knowledge Base tables.

Ported from an old, never-merged branch (claude/jovial-lehmann-350942) that
built the full KB backend behind the already-shipped frontend — it was
written but never wired up, so /kb/* has been 404ing since the KB UI shipped
and NewTicket's "Similar Articles" suggestions have never actually worked.

Revision ID: 053
Revises: 052
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, TSVECTOR

revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "kb_categories",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("parent_id", UUID(as_uuid=True), sa.ForeignKey("kb_categories.id", ondelete="CASCADE"), nullable=True),
        sa.Column("slug", sa.String(255), nullable=False, unique=True),
        sa.Column("icon", sa.String(100), nullable=True),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "kb_category_translations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("category_id", UUID(as_uuid=True), sa.ForeignKey("kb_categories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.UniqueConstraint("category_id", "language", name="uq_kb_cat_trans"),
    )

    op.create_table(
        "kb_tags",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("color", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "kb_articles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("category_id", UUID(as_uuid=True), sa.ForeignKey("kb_categories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("author_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("slug", sa.String(600), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("visibility", sa.String(30), nullable=False, server_default="public"),
        sa.Column("default_language", sa.String(10), nullable=False, server_default="en"),
        sa.Column("reference_url", sa.Text, nullable=True),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("view_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("helpful_yes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("helpful_no", sa.Integer, nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('draft','review','approved','published','archived')", name="chk_kb_status"),
        sa.CheckConstraint("visibility IN ('public','internal','agent_only','customer_specific')", name="chk_kb_visibility"),
    )

    op.create_table(
        "kb_article_tags",
        sa.Column("article_id", UUID(as_uuid=True), sa.ForeignKey("kb_articles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag_id", UUID(as_uuid=True), sa.ForeignKey("kb_tags.id", ondelete="CASCADE"), primary_key=True),
    )

    op.create_table(
        "kb_article_translations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("article_id", UUID(as_uuid=True), sa.ForeignKey("kb_articles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("excerpt", sa.Text, nullable=True),
        sa.Column("search_vector", TSVECTOR, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("article_id", "language", name="uq_kb_article_trans"),
    )
    op.create_index("ix_kb_article_trans_search", "kb_article_translations", ["search_vector"], postgresql_using="gin")

    op.create_table(
        "kb_article_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("article_id", UUID(as_uuid=True), sa.ForeignKey("kb_articles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("language", sa.String(10), nullable=False, server_default="en"),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("change_summary", sa.Text, nullable=True),
        sa.Column("changed_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("article_id", "version_number", "language", name="uq_kb_article_version"),
    )

    op.create_table(
        "kb_ticket_article_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("ticket_id", UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("article_id", UUID(as_uuid=True), sa.ForeignKey("kb_articles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("linked_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("link_type", sa.String(30), nullable=False, server_default="related"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("ticket_id", "article_id", name="uq_kb_ticket_article"),
        sa.CheckConstraint("link_type IN ('related','resolved_by','referenced')", name="chk_kb_link_type"),
    )

    op.create_table(
        "kb_article_feedback",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("article_id", UUID(as_uuid=True), sa.ForeignKey("kb_articles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("is_helpful", sa.Boolean, nullable=False),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("kb_article_feedback")
    op.drop_table("kb_ticket_article_links")
    op.drop_table("kb_article_versions")
    op.drop_index("ix_kb_article_trans_search", table_name="kb_article_translations")
    op.drop_table("kb_article_translations")
    op.drop_table("kb_article_tags")
    op.drop_table("kb_articles")
    op.drop_table("kb_tags")
    op.drop_table("kb_category_translations")
    op.drop_table("kb_categories")
