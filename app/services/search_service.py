"""Full-text search service for the Knowledge Base using PostgreSQL tsvector."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KBArticle, KBArticleTranslation
from app.schemas.knowledge import SearchHit, SearchResult


async def search(
    db: AsyncSession,
    query: str,
    language: str = "en",
    visible_visibilities: list[str] | None = None,
    category_id: Optional[UUID] = None,
    status: str = "published",
    limit: int = 20,
    offset: int = 0,
) -> SearchResult:
    if visible_visibilities is None:
        visible_visibilities = ["public"]

    # plainto_tsquery (not to_tsquery) — it tokenizes arbitrary free text safely
    # (ANDing terms) instead of requiring tsquery syntax, so a search string
    # containing punctuation like "can't reach :8080 (VPN)" doesn't 500.
    stmt = (
        select(
            KBArticle.id,
            KBArticle.slug,
            KBArticle.category_id,
            KBArticle.status,
            KBArticle.view_count,
            KBArticleTranslation.title,
            KBArticleTranslation.excerpt,
            func.ts_rank(
                func.to_tsvector("english", KBArticleTranslation.title + " " + KBArticleTranslation.content),
                func.plainto_tsquery("english", query),
            ).label("rank"),
        )
        .join(KBArticleTranslation, KBArticleTranslation.article_id == KBArticle.id)
        .where(
            KBArticleTranslation.language == language,
            KBArticle.visibility.in_(visible_visibilities),
            KBArticle.status == status,
            func.to_tsvector("english", KBArticleTranslation.title + " " + KBArticleTranslation.content)
            .op("@@")(func.plainto_tsquery("english", query)),
        )
    )
    if category_id:
        stmt = stmt.where(KBArticle.category_id == category_id)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_res = await db.execute(count_stmt)
    total = total_res.scalar_one()

    stmt = stmt.order_by(text("rank DESC")).offset(offset).limit(limit)
    result = await db.execute(stmt)
    rows = result.all()

    hits = [
        SearchHit(
            id=row.id,
            slug=row.slug,
            title=row.title,
            excerpt=row.excerpt,
            category_id=row.category_id,
            status=row.status,
            view_count=row.view_count,
            rank=float(row.rank),
        )
        for row in rows
    ]
    return SearchResult(hits=hits, total=total, query=query)


async def suggest(
    db: AsyncSession,
    prefix: str,
    language: str = "en",
    visible_visibilities: list[str] | None = None,
) -> list[str]:
    if visible_visibilities is None:
        visible_visibilities = ["public"]

    stmt = (
        select(KBArticleTranslation.title)
        .join(KBArticle, KBArticle.id == KBArticleTranslation.article_id)
        .where(
            KBArticleTranslation.language == language,
            KBArticleTranslation.title.ilike(f"{prefix}%"),
            KBArticle.visibility.in_(visible_visibilities),
            KBArticle.status == "published",
        )
        .order_by(KBArticle.view_count.desc())
        .limit(10)
    )
    result = await db.execute(stmt)
    return [row[0] for row in result.all()]
