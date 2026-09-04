"""Knowledge Base service — database operations."""
from __future__ import annotations

import math
import re
import uuid
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.knowledge import (
    KBArticle, KBArticleFeedback, KBArticleTranslation, KBArticleVersion,
    KBCategory, KBCategoryTranslation, KBTag, KBTicketArticleLink, kb_article_tags,
)
from app.schemas.knowledge import (
    ArticleCreate, ArticleUpdate, CategoryCreate, CategoryUpdate,
    FeedbackCreate, TicketLinkCreate,
)


def _slug_from_title(title: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:200] or "article"


def _article_query():
    return (
        select(KBArticle)
        .options(
            selectinload(KBArticle.translations),
            selectinload(KBArticle.tags),
            selectinload(KBArticle.author),
        )
    )


# ── Categories ────────────────────────────────────────────────────────────────

async def get_category_tree(db: AsyncSession) -> list[KBCategory]:
    result = await db.execute(
        select(KBCategory)
        .options(
            selectinload(KBCategory.translations),
            selectinload(KBCategory.children).selectinload(KBCategory.translations),
        )
        .where(KBCategory.parent_id == None)  # noqa: E711
        .order_by(KBCategory.sort_order)
    )
    return list(result.scalars().unique().all())


async def create_category(db: AsyncSession, data: CategoryCreate) -> KBCategory:
    cat = KBCategory(
        id=uuid.uuid4(),
        parent_id=data.parent_id,
        slug=data.slug,
        icon=data.icon,
        sort_order=data.sort_order,
    )
    db.add(cat)
    await db.flush()
    for t in data.translations:
        db.add(KBCategoryTranslation(category_id=cat.id, language=t.language, name=t.name, description=t.description))
    await db.commit()
    await db.refresh(cat)
    return cat


async def update_category(db: AsyncSession, category_id: UUID, data: CategoryUpdate) -> KBCategory | None:
    result = await db.execute(select(KBCategory).where(KBCategory.id == category_id))
    cat = result.scalar_one_or_none()
    if not cat:
        return None
    for field in ("parent_id", "slug", "icon", "sort_order"):
        val = getattr(data, field, None)
        if val is not None:
            setattr(cat, field, val)
    if data.translations is not None:
        await db.execute(
            KBCategoryTranslation.__table__.delete().where(KBCategoryTranslation.category_id == category_id)
        )
        for t in data.translations:
            db.add(KBCategoryTranslation(category_id=cat.id, language=t.language, name=t.name, description=t.description))
    await db.commit()
    await db.refresh(cat)
    return cat


async def delete_category(db: AsyncSession, category_id: UUID) -> bool:
    result = await db.execute(select(KBCategory).where(KBCategory.id == category_id))
    cat = result.scalar_one_or_none()
    if not cat:
        return False
    await db.delete(cat)
    await db.commit()
    return True


# ── Articles ──────────────────────────────────────────────────────────────────

async def list_articles(
    db: AsyncSession,
    visible_visibilities: list[str],
    category_id: Optional[UUID] = None,
    status: Optional[str] = None,
    tag_id: Optional[UUID] = None,
    language: str = "en",
    page: int = 1,
    page_size: int = 20,
):
    stmt = _article_query().where(KBArticle.visibility.in_(visible_visibilities))
    if category_id:
        stmt = stmt.where(KBArticle.category_id == category_id)
    if status:
        stmt = stmt.where(KBArticle.status == status)
    if tag_id:
        stmt = stmt.where(KBArticle.tags.any(KBTag.id == tag_id))

    count_res = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = count_res.scalar_one()

    stmt = stmt.order_by(KBArticle.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    rows = list(result.scalars().unique().all())
    return rows, total


async def get_article(db: AsyncSession, article_id: UUID, visible_visibilities: list[str]) -> KBArticle | None:
    result = await db.execute(
        _article_query().where(KBArticle.id == article_id, KBArticle.visibility.in_(visible_visibilities))
    )
    return result.scalar_one_or_none()


async def get_article_by_slug(db: AsyncSession, slug: str, visible_visibilities: list[str]) -> KBArticle | None:
    result = await db.execute(
        _article_query().where(KBArticle.slug == slug, KBArticle.visibility.in_(visible_visibilities))
    )
    return result.scalar_one_or_none()


async def increment_view(db: AsyncSession, article_id: UUID) -> None:
    await db.execute(
        KBArticle.__table__.update()
        .where(KBArticle.id == article_id)
        .values(view_count=KBArticle.view_count + 1)
    )
    await db.commit()


async def create_article(db: AsyncSession, data: ArticleCreate, author_id: UUID) -> KBArticle:
    slug = _slug_from_title(data.title) + "-" + str(uuid.uuid4())[:8]

    article = KBArticle(
        id=uuid.uuid4(),
        category_id=data.category_id,
        author_id=author_id,
        slug=slug,
        status=data.status,
        visibility=data.visibility,
        default_language=data.language,
        reference_url=data.reference_url,
        sort_order=data.sort_order,
    )
    db.add(article)
    await db.flush()

    # Primary translation
    db.add(KBArticleTranslation(
        article_id=article.id,
        language=data.language,
        title=data.title,
        content=data.content,
        excerpt=data.excerpt,
    ))
    # Extra translations
    for t in data.translations:
        if t.language != data.language:
            db.add(KBArticleTranslation(article_id=article.id, language=t.language, title=t.title, content=t.content, excerpt=t.excerpt))

    # Tags
    if data.tag_ids:
        tag_res = await db.execute(select(KBTag).where(KBTag.id.in_(data.tag_ids)))
        article.tags = list(tag_res.scalars().all())

    # First version snapshot
    db.add(KBArticleVersion(
        article_id=article.id,
        version_number=1,
        language=data.language,
        title=data.title,
        content=data.content,
        change_summary="Initial version",
        changed_by=author_id,
    ))

    await db.commit()
    result = await db.execute(_article_query().where(KBArticle.id == article.id))
    return result.scalar_one()


async def update_article(db: AsyncSession, article: KBArticle, data: ArticleUpdate, editor_id: UUID) -> KBArticle:
    update_data = data.model_dump(exclude_unset=True)

    simple_fields = {"category_id", "status", "visibility", "reference_url", "sort_order"}
    for field in simple_fields:
        if field in update_data:
            setattr(article, field, update_data[field])

    if data.tag_ids is not None:
        tag_res = await db.execute(select(KBTag).where(KBTag.id.in_(data.tag_ids)))
        article.tags = list(tag_res.scalars().all())

    if data.translations:
        for t in data.translations:
            trans_res = await db.execute(
                select(KBArticleTranslation).where(
                    KBArticleTranslation.article_id == article.id,
                    KBArticleTranslation.language == t.language,
                )
            )
            existing = trans_res.scalar_one_or_none()
            if existing:
                existing.title = t.title
                existing.content = t.content
                if t.excerpt is not None:
                    existing.excerpt = t.excerpt
                # Version snapshot
                max_ver_res = await db.execute(
                    select(func.max(KBArticleVersion.version_number))
                    .where(KBArticleVersion.article_id == article.id, KBArticleVersion.language == t.language)
                )
                max_ver = max_ver_res.scalar_one_or_none() or 0
                db.add(KBArticleVersion(
                    article_id=article.id,
                    version_number=max_ver + 1,
                    language=t.language,
                    title=t.title,
                    content=t.content,
                    change_summary=data.change_summary or "Updated",
                    changed_by=editor_id,
                ))
            else:
                db.add(KBArticleTranslation(article_id=article.id, language=t.language, title=t.title, content=t.content, excerpt=t.excerpt))
                db.add(KBArticleVersion(
                    article_id=article.id,
                    version_number=1,
                    language=t.language,
                    title=t.title,
                    content=t.content,
                    change_summary=data.change_summary or "Initial translation",
                    changed_by=editor_id,
                ))

    await db.commit()
    result = await db.execute(_article_query().where(KBArticle.id == article.id))
    return result.scalar_one()


async def delete_article(db: AsyncSession, article: KBArticle) -> None:
    await db.delete(article)
    await db.commit()


# ── Versions ──────────────────────────────────────────────────────────────────

async def get_versions(db: AsyncSession, article_id: UUID, language: Optional[str] = None) -> list[KBArticleVersion]:
    stmt = (
        select(KBArticleVersion)
        .options(selectinload(KBArticleVersion.author))
        .where(KBArticleVersion.article_id == article_id)
    )
    if language:
        stmt = stmt.where(KBArticleVersion.language == language)
    stmt = stmt.order_by(KBArticleVersion.version_number.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def revert_to_version(
    db: AsyncSession, article: KBArticle, version_number: int, language: str, user_id: UUID, change_summary: Optional[str]
) -> KBArticle:
    ver_res = await db.execute(
        select(KBArticleVersion).where(
            KBArticleVersion.article_id == article.id,
            KBArticleVersion.version_number == version_number,
            KBArticleVersion.language == language,
        )
    )
    version = ver_res.scalar_one_or_none()
    if not version:
        raise ValueError(f"Version {version_number} ({language}) not found")

    trans_res = await db.execute(
        select(KBArticleTranslation).where(
            KBArticleTranslation.article_id == article.id,
            KBArticleTranslation.language == language,
        )
    )
    trans = trans_res.scalar_one_or_none()
    if trans:
        trans.title = version.title
        trans.content = version.content

    max_ver_res = await db.execute(
        select(func.max(KBArticleVersion.version_number))
        .where(KBArticleVersion.article_id == article.id, KBArticleVersion.language == language)
    )
    new_ver = (max_ver_res.scalar_one_or_none() or 0) + 1
    db.add(KBArticleVersion(
        article_id=article.id,
        version_number=new_ver,
        language=language,
        title=version.title,
        content=version.content,
        change_summary=change_summary or f"Reverted to version {version_number}",
        changed_by=user_id,
    ))
    await db.commit()
    result = await db.execute(_article_query().where(KBArticle.id == article.id))
    return result.scalar_one()


# ── Ticket Links ──────────────────────────────────────────────────────────────

async def get_ticket_links(db: AsyncSession, article_id: UUID) -> list[KBTicketArticleLink]:
    result = await db.execute(
        select(KBTicketArticleLink).where(KBTicketArticleLink.article_id == article_id)
    )
    return list(result.scalars().all())


async def link_ticket(db: AsyncSession, article_id: UUID, data: TicketLinkCreate, user_id: UUID) -> KBTicketArticleLink:
    link = KBTicketArticleLink(
        ticket_id=data.ticket_id,
        article_id=article_id,
        linked_by=user_id,
        link_type=data.link_type,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return link


async def unlink_ticket(db: AsyncSession, article_id: UUID, ticket_id: UUID) -> bool:
    result = await db.execute(
        select(KBTicketArticleLink).where(
            KBTicketArticleLink.article_id == article_id,
            KBTicketArticleLink.ticket_id == ticket_id,
        )
    )
    link = result.scalar_one_or_none()
    if not link:
        return False
    await db.delete(link)
    await db.commit()
    return True


async def get_articles_for_ticket(db: AsyncSession, ticket_id: UUID) -> list[KBArticle]:
    result = await db.execute(
        _article_query()
        .join(KBTicketArticleLink, KBTicketArticleLink.article_id == KBArticle.id)
        .where(KBTicketArticleLink.ticket_id == ticket_id)
    )
    return list(result.scalars().unique().all())


# ── Tags ──────────────────────────────────────────────────────────────────────

async def list_tags(db: AsyncSession) -> list[KBTag]:
    result = await db.execute(select(KBTag).order_by(KBTag.name))
    return list(result.scalars().all())


async def create_tag(db: AsyncSession, name: str, color: Optional[str] = None) -> KBTag:
    tag = KBTag(name=name, color=color)
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag


# ── Feedback ──────────────────────────────────────────────────────────────────

async def submit_feedback(
    db: AsyncSession, article_id: UUID, data: FeedbackCreate,
    user_id: Optional[UUID], ip_hash: Optional[str]
) -> KBArticleFeedback:
    fb = KBArticleFeedback(
        article_id=article_id,
        user_id=user_id,
        is_helpful=data.is_helpful,
        comment=data.comment,
        ip_hash=ip_hash,
    )
    db.add(fb)
    # Update counters
    if data.is_helpful:
        await db.execute(KBArticle.__table__.update().where(KBArticle.id == article_id).values(helpful_yes=KBArticle.helpful_yes + 1))
    else:
        await db.execute(KBArticle.__table__.update().where(KBArticle.id == article_id).values(helpful_no=KBArticle.helpful_no + 1))
    await db.commit()
    await db.refresh(fb)
    return fb
