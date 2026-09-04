"""Knowledge Base API router."""
from __future__ import annotations

import hashlib
import math
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.user import User, UserRole
from app.schemas.knowledge import (
    ArticleCreate, ArticleListOut, ArticleOut, ArticleUpdate,
    CategoryCreate, CategoryOut, CategoryUpdate,
    FeedbackCreate, FeedbackOut, PaginatedArticles, RevertRequest,
    SearchResult, StatusTransitionRequest, TagCreate, TagOut,
    TicketLinkCreate, TicketLinkOut, TranslationCreate, VersionOut,
)
from app.services import knowledge_service as svc
from app.services import search_service as search_svc

router = APIRouter(prefix="/kb", tags=["knowledge"])


def _visible_visibilities(role: UserRole) -> list[str]:
    if role == UserRole.admin:
        return ["public", "internal", "agent_only", "customer_specific"]
    if role == UserRole.technician:
        return ["public", "internal", "agent_only"]
    return ["public"]


def _require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _require_agent(user: User = Depends(get_current_user)) -> User:
    if user.role not in (UserRole.admin, UserRole.technician):
        raise HTTPException(status_code=403, detail="Agent access required")
    return user


# ══════════════════════════════════════════════════════════════════════════════
# CATEGORIES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return await svc.get_category_tree(db)


@router.post("/categories", response_model=CategoryOut, status_code=201)
async def create_category(
    data: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_require_admin),
):
    return await svc.create_category(db, data)


@router.patch("/categories/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: UUID,
    data: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_require_admin),
):
    cat = await svc.update_category(db, category_id, data)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    return cat


@router.delete("/categories/{category_id}", status_code=204)
async def delete_category(
    category_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_require_admin),
):
    if not await svc.delete_category(db, category_id):
        raise HTTPException(status_code=404, detail="Category not found")


# ══════════════════════════════════════════════════════════════════════════════
# ARTICLES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/articles", response_model=PaginatedArticles)
async def list_articles(
    category_id: Optional[UUID] = Query(None),
    status: Optional[str]       = Query(None),
    tag_id: Optional[UUID]      = Query(None),
    language: str               = Query("en"),
    search: Optional[str]       = Query(None),
    page: int                   = Query(1, ge=1),
    page_size: int              = Query(20, ge=1, le=100),
    db: AsyncSession            = Depends(get_db),
    current_user: User          = Depends(get_current_user),
):
    # If search query is present, delegate to search service
    if search:
        vis = _visible_visibilities(current_user.role)
        result = await search_svc.search(
            db, query=search, language=language,
            visible_visibilities=vis,
            category_id=category_id,
            status=status or "published",
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        # Convert search hits to ArticleListOut-compatible format
        items = [
            ArticleListOut(
                id=h.id, category_id=h.category_id, author_id=h.id,
                slug=h.slug, status=h.status, visibility="public",
                default_language=language, view_count=h.view_count,
                helpful_yes=0, helpful_no=0, published_at=None,
                created_at=__import__("datetime").datetime.utcnow(),
                updated_at=__import__("datetime").datetime.utcnow(),
                title=h.title, excerpt=h.excerpt,
            )
            for h in result.hits
        ]
        return PaginatedArticles(
            items=items,
            total=result.total,
            page=page,
            page_size=page_size,
            pages=max(1, math.ceil(result.total / page_size)),
        )

    vis = _visible_visibilities(current_user.role)
    rows, total = await svc.list_articles(
        db, visible_visibilities=vis, category_id=category_id,
        status=status, tag_id=tag_id, language=language,
        page=page, page_size=page_size,
    )
    items: list[ArticleListOut] = []
    for row in rows:
        trans = next((t for t in row.translations if t.language == language), None) or \
                next((t for t in row.translations if t.language == row.default_language), None)
        item = ArticleListOut.model_validate(row)
        if trans:
            item.title   = trans.title
            item.excerpt = trans.excerpt
        items.append(item)

    return PaginatedArticles(
        items=items, total=total, page=page, page_size=page_size,
        pages=max(1, math.ceil(total / page_size)),
    )


@router.post("/articles", response_model=ArticleOut, status_code=201)
async def create_article(
    data: ArticleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_require_agent),
):
    return await svc.create_article(db, data, author_id=current_user.id)


@router.get("/articles/slug/{slug}", response_model=ArticleOut)
async def get_article_by_slug(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    vis = _visible_visibilities(current_user.role)
    article = await svc.get_article_by_slug(db, slug, vis)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    await svc.increment_view(db, article.id)
    return article


@router.get("/articles/{article_id}", response_model=ArticleOut)
async def get_article(
    article_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    vis = _visible_visibilities(current_user.role)
    article = await svc.get_article(db, article_id, vis)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    await svc.increment_view(db, article_id)
    return article


@router.patch("/articles/{article_id}", response_model=ArticleOut)
async def update_article(
    article_id: UUID,
    data: ArticleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_require_agent),
):
    vis = _visible_visibilities(current_user.role)
    article = await svc.get_article(db, article_id, vis)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if current_user.role != UserRole.admin and article.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot edit another agent's article")
    return await svc.update_article(db, article, data, editor_id=current_user.id)


@router.delete("/articles/{article_id}", status_code=204)
async def delete_article(
    article_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_require_agent),
):
    vis = _visible_visibilities(current_user.role)
    article = await svc.get_article(db, article_id, vis)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if current_user.role != UserRole.admin and article.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot delete another agent's article")
    await svc.delete_article(db, article)


@router.post("/articles/{article_id}/status", response_model=ArticleOut)
async def transition_status(
    article_id: UUID,
    body: StatusTransitionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_require_agent),
):
    vis = _visible_visibilities(current_user.role)
    article = await svc.get_article(db, article_id, vis)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    # Only admins can publish/approve
    if body.status in ("approved", "published") and current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Only admins can approve or publish articles")
    return await svc.update_article(db, article, ArticleUpdate(status=body.status, change_summary=body.comment), editor_id=current_user.id)


@router.put("/articles/{article_id}/translations/{language}", response_model=ArticleOut)
async def upsert_translation(
    article_id: UUID,
    language: str,
    data: TranslationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_require_agent),
):
    vis = _visible_visibilities(current_user.role)
    article = await svc.get_article(db, article_id, vis)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    update_data = ArticleUpdate(
        translations=[TranslationCreate(language=language, title=data.title, content=data.content, excerpt=data.excerpt)]
    )
    return await svc.update_article(db, article, update_data, editor_id=current_user.id)


# ── Versions ──────────────────────────────────────────────────────────────────

@router.get("/articles/{article_id}/versions", response_model=list[VersionOut])
async def list_versions(
    article_id: UUID,
    language: Optional[str] = Query(None),
    db: AsyncSession        = Depends(get_db),
    current_user: User      = Depends(_require_agent),
):
    vis = _visible_visibilities(current_user.role)
    article = await svc.get_article(db, article_id, vis)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    versions = await svc.get_versions(db, article_id, language)
    out = []
    for v in versions:
        vo = VersionOut.model_validate(v)
        vo.changed_by_name = v.author.name if v.author else None
        out.append(vo)
    return out


@router.post("/articles/{article_id}/versions/{version_number}/revert", response_model=ArticleOut)
async def revert_version(
    article_id: UUID,
    version_number: int,
    language: str = Query("en"),
    body: RevertRequest = RevertRequest(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_require_agent),
):
    vis = _visible_visibilities(current_user.role)
    article = await svc.get_article(db, article_id, vis)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    try:
        return await svc.revert_to_version(db, article, version_number, language, current_user.id, body.change_summary)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Ticket Links ──────────────────────────────────────────────────────────────

@router.get("/articles/{article_id}/ticket-links", response_model=list[TicketLinkOut])
async def get_ticket_links(
    article_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_require_agent),
):
    vis = _visible_visibilities(current_user.role)
    if not await svc.get_article(db, article_id, vis):
        raise HTTPException(status_code=404, detail="Article not found")
    return await svc.get_ticket_links(db, article_id)


@router.post("/articles/{article_id}/ticket-links", response_model=TicketLinkOut, status_code=201)
async def link_ticket(
    article_id: UUID,
    data: TicketLinkCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_require_agent),
):
    return await svc.link_ticket(db, article_id, data, user_id=current_user.id)


@router.delete("/articles/{article_id}/ticket-links/{ticket_id}", status_code=204)
async def unlink_ticket(
    article_id: UUID,
    ticket_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_require_agent),
):
    if not await svc.unlink_ticket(db, article_id, ticket_id):
        raise HTTPException(status_code=404, detail="Link not found")


@router.get("/tickets/{ticket_id}/articles")
async def articles_for_ticket(
    ticket_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return await svc.get_articles_for_ticket(db, ticket_id)


# ── Search ────────────────────────────────────────────────────────────────────

@router.get("/search", response_model=SearchResult)
async def search_articles(
    q: str                      = Query(..., min_length=1, max_length=200),
    language: str               = Query("en"),
    category_id: Optional[UUID] = Query(None),
    status: str                 = Query("published"),
    limit: int                  = Query(20, ge=1, le=100),
    offset: int                 = Query(0, ge=0),
    db: AsyncSession            = Depends(get_db),
    current_user: User          = Depends(get_current_user),
):
    vis = _visible_visibilities(current_user.role)
    return await search_svc.search(
        db, query=q, language=language,
        visible_visibilities=vis, category_id=category_id,
        status=status, limit=limit, offset=offset,
    )


@router.get("/search/suggest", response_model=list[str])
async def search_suggest(
    q: str           = Query(..., min_length=2, max_length=100),
    language: str    = Query("en"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    vis = _visible_visibilities(current_user.role)
    return await search_svc.suggest(db, prefix=q, language=language, visible_visibilities=vis)


# ── Tags ──────────────────────────────────────────────────────────────────────

@router.get("/tags", response_model=list[TagOut])
async def list_tags(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return await svc.list_tags(db)


@router.post("/tags", response_model=TagOut, status_code=201)
async def create_tag(
    data: TagCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_require_agent),
):
    return await svc.create_tag(db, data.name, data.color)


# ── Feedback ──────────────────────────────────────────────────────────────────

@router.post("/articles/{article_id}/feedback", response_model=FeedbackOut, status_code=201)
async def submit_feedback(
    article_id: UUID,
    data: FeedbackCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ip = request.client.host if request.client else "unknown"
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()
    return await svc.submit_feedback(db, article_id, data, user_id=current_user.id, ip_hash=ip_hash)
