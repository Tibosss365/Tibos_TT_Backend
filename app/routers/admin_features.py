"""
Admin feature endpoints — CRUD for:
  - Custom Fields
  - Ticket Templates
  - Automation Rules
  - Webhook Configs
  - Notification Channels
  - Assets
  - Escalation Rules
  - Recurring Ticket Templates
  - Portal Branding
  - User settings (self-service TOTP, timezone, password)
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_admin
from app.database import get_db
from app.models.feature_models import (
    CustomField, TicketTemplate, AutomationRule, WebhookConfig,
    NotificationChannel, Asset, AssetHistory, EscalationRule, RecurringTicketTemplate,
    PortalBranding,
)
from app.models.user import User
from app.schemas.feature_schemas import (
    CustomFieldCreate, CustomFieldUpdate, CustomFieldOut,
    TicketTemplateCreate, TicketTemplateUpdate, TicketTemplateOut,
    AutomationRuleCreate, AutomationRuleUpdate, AutomationRuleOut,
    WebhookConfigCreate, WebhookConfigUpdate, WebhookConfigOut,
    NotificationChannelCreate, NotificationChannelUpdate, NotificationChannelOut,
    AssetCreate, AssetUpdate, AssetOut, AssetHistoryOut,
    EscalationRuleCreate, EscalationRuleUpdate, EscalationRuleOut,
    RecurringTicketTemplateCreate, RecurringTicketTemplateUpdate, RecurringTicketTemplateOut,
    PortalBrandingUpdate, PortalBrandingOut,
)
from app.schemas.user import TOTPSetupOut, TOTPVerifyRequest, TOTPVerifyResponse, TOTPDisableRequest, UserSettingsUpdate, UserOut

router = APIRouter(tags=["admin-features"])


# ── Generic CRUD helpers ──────────────────────────────────────────────────────

async def _get_or_404(model, id_: uuid.UUID, db: AsyncSession):
    result = await db.execute(select(model).where(model.id == id_))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail=f"{model.__tablename__} not found")
    return obj


def _apply_updates(obj, data: dict) -> None:
    for k, v in data.items():
        if v is not None:
            setattr(obj, k, v)


# ── Custom Fields ─────────────────────────────────────────────────────────────

@router.get("/admin/custom-fields", response_model=list[CustomFieldOut])
async def list_custom_fields(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(CustomField).order_by(CustomField.display_order))
    return result.scalars().all()


@router.post("/admin/custom-fields", response_model=CustomFieldOut, status_code=201)
async def create_custom_field(
    body: CustomFieldCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = CustomField(**body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.put("/admin/custom-fields/{id}", response_model=CustomFieldOut)
async def update_custom_field(
    id: uuid.UUID,
    body: CustomFieldUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = await _get_or_404(CustomField, id, db)
    _apply_updates(obj, body.model_dump(exclude_none=True))
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/admin/custom-fields/{id}", status_code=204)
async def delete_custom_field(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = await _get_or_404(CustomField, id, db)
    await db.delete(obj)
    await db.commit()


# ── Ticket Templates ──────────────────────────────────────────────────────────

@router.get("/admin/ticket-templates", response_model=list[TicketTemplateOut])
async def list_ticket_templates(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(TicketTemplate).order_by(TicketTemplate.created_at.desc()))
    return result.scalars().all()


@router.post("/admin/ticket-templates", response_model=TicketTemplateOut, status_code=201)
async def create_ticket_template(
    body: TicketTemplateCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = TicketTemplate(**body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.put("/admin/ticket-templates/{id}", response_model=TicketTemplateOut)
async def update_ticket_template(
    id: uuid.UUID,
    body: TicketTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = await _get_or_404(TicketTemplate, id, db)
    _apply_updates(obj, body.model_dump(exclude_none=True))
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/admin/ticket-templates/{id}", status_code=204)
async def delete_ticket_template(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = await _get_or_404(TicketTemplate, id, db)
    await db.delete(obj)
    await db.commit()


# ── Automation Rules ──────────────────────────────────────────────────────────

@router.get("/admin/automation-rules", response_model=list[AutomationRuleOut])
async def list_automation_rules(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(AutomationRule).order_by(AutomationRule.run_order))
    return result.scalars().all()


@router.post("/admin/automation-rules", response_model=AutomationRuleOut, status_code=201)
async def create_automation_rule(
    body: AutomationRuleCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = AutomationRule(**body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.put("/admin/automation-rules/{id}", response_model=AutomationRuleOut)
async def update_automation_rule(
    id: uuid.UUID,
    body: AutomationRuleUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = await _get_or_404(AutomationRule, id, db)
    _apply_updates(obj, body.model_dump(exclude_none=True))
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/admin/automation-rules/{id}", status_code=204)
async def delete_automation_rule(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = await _get_or_404(AutomationRule, id, db)
    await db.delete(obj)
    await db.commit()


# ── Webhook Configs ───────────────────────────────────────────────────────────

@router.get("/admin/webhooks", response_model=list[WebhookConfigOut])
async def list_webhooks(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(WebhookConfig).order_by(WebhookConfig.created_at.desc()))
    return result.scalars().all()


@router.post("/admin/webhooks", response_model=WebhookConfigOut, status_code=201)
async def create_webhook(
    body: WebhookConfigCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = WebhookConfig(**body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.put("/admin/webhooks/{id}", response_model=WebhookConfigOut)
async def update_webhook(
    id: uuid.UUID,
    body: WebhookConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = await _get_or_404(WebhookConfig, id, db)
    _apply_updates(obj, body.model_dump(exclude_none=True))
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/admin/webhooks/{id}", status_code=204)
async def delete_webhook(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = await _get_or_404(WebhookConfig, id, db)
    await db.delete(obj)
    await db.commit()


# ── Notification Channels ─────────────────────────────────────────────────────

@router.get("/admin/notification-channels", response_model=list[NotificationChannelOut])
async def list_notification_channels(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(NotificationChannel).order_by(NotificationChannel.created_at.desc()))
    return result.scalars().all()


@router.post("/admin/notification-channels", response_model=NotificationChannelOut, status_code=201)
async def create_notification_channel(
    body: NotificationChannelCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = NotificationChannel(**body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.put("/admin/notification-channels/{id}", response_model=NotificationChannelOut)
async def update_notification_channel(
    id: uuid.UUID,
    body: NotificationChannelUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = await _get_or_404(NotificationChannel, id, db)
    _apply_updates(obj, body.model_dump(exclude_none=True))
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/admin/notification-channels/{id}", status_code=204)
async def delete_notification_channel(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = await _get_or_404(NotificationChannel, id, db)
    await db.delete(obj)
    await db.commit()


# ── Assets ────────────────────────────────────────────────────────────────────

def _record_assignment_history(
    db: AsyncSession,
    asset: Asset,
    *,
    prev_name: str | None,
    prev_email: str | None,
    changed_by: User,
) -> None:
    """Append an asset_history row when the assignee changed."""
    new_name = asset.assigned_to_name or None
    new_email = asset.assigned_to_email or None
    if (new_name, new_email) == (prev_name, prev_email):
        return
    if new_name or new_email:
        action = "reassigned" if (prev_name or prev_email) else "assigned"
    else:
        action = "unassigned"
    note = None
    if action in ("reassigned", "unassigned") and (prev_name or prev_email):
        note = f"Previously assigned to {prev_name or ''}".strip()
        if prev_email:
            note += f" ({prev_email})"
    db.add(AssetHistory(
        asset_id=asset.id,
        action=action,
        assigned_to_name=new_name,
        assigned_to_email=new_email,
        employee_code=asset.employee_code or None,
        note=note,
        changed_by_name=changed_by.name,
    ))


@router.get("/admin/assets", response_model=list[AssetOut])
async def list_assets(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Asset).order_by(Asset.created_at.desc()))
    return result.scalars().all()


@router.post("/admin/assets", response_model=AssetOut, status_code=201)
async def create_asset(
    body: AssetCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    obj = Asset(**body.model_dump())
    db.add(obj)
    await db.flush()
    _record_assignment_history(
        db, obj, prev_name=None, prev_email=None, changed_by=current_user
    )
    await db.commit()
    await db.refresh(obj)
    return obj


@router.put("/admin/assets/{id}", response_model=AssetOut)
async def update_asset(
    id: uuid.UUID,
    body: AssetUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    obj = await _get_or_404(Asset, id, db)
    prev_name = obj.assigned_to_name or None
    prev_email = obj.assigned_to_email or None
    changes = body.model_dump(exclude_none=True)
    # Empty strings mean "clear this field" (unassign)
    for field in ("assigned_to_name", "assigned_to_email", "employee_code"):
        if changes.get(field) == "":
            changes[field] = None
            setattr(obj, field, None)
    _apply_updates(obj, changes)
    _record_assignment_history(
        db, obj, prev_name=prev_name, prev_email=prev_email, changed_by=current_user
    )
    await db.commit()
    await db.refresh(obj)
    return obj


@router.get("/admin/assets/{id}/history", response_model=list[AssetHistoryOut])
async def asset_history(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await _get_or_404(Asset, id, db)
    result = await db.execute(
        select(AssetHistory)
        .where(AssetHistory.asset_id == id)
        .order_by(AssetHistory.created_at.desc())
    )
    return result.scalars().all()


@router.delete("/admin/assets/{id}", status_code=204)
async def delete_asset(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = await _get_or_404(Asset, id, db)
    await db.delete(obj)
    await db.commit()


# ── Escalation Rules ──────────────────────────────────────────────────────────

@router.get("/admin/escalation-rules", response_model=list[EscalationRuleOut])
async def list_escalation_rules(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(EscalationRule).order_by(EscalationRule.created_at.desc()))
    return result.scalars().all()


@router.post("/admin/escalation-rules", response_model=EscalationRuleOut, status_code=201)
async def create_escalation_rule(
    body: EscalationRuleCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = EscalationRule(**body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.put("/admin/escalation-rules/{id}", response_model=EscalationRuleOut)
async def update_escalation_rule(
    id: uuid.UUID,
    body: EscalationRuleUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = await _get_or_404(EscalationRule, id, db)
    _apply_updates(obj, body.model_dump(exclude_none=True))
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/admin/escalation-rules/{id}", status_code=204)
async def delete_escalation_rule(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = await _get_or_404(EscalationRule, id, db)
    await db.delete(obj)
    await db.commit()


# ── Recurring Ticket Templates ────────────────────────────────────────────────

@router.get("/admin/recurring-templates", response_model=list[RecurringTicketTemplateOut])
async def list_recurring_templates(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(
        select(RecurringTicketTemplate).order_by(RecurringTicketTemplate.created_at.desc())
    )
    return result.scalars().all()


@router.post("/admin/recurring-templates", response_model=RecurringTicketTemplateOut, status_code=201)
async def create_recurring_template(
    body: RecurringTicketTemplateCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = RecurringTicketTemplate(**body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.put("/admin/recurring-templates/{id}", response_model=RecurringTicketTemplateOut)
async def update_recurring_template(
    id: uuid.UUID,
    body: RecurringTicketTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = await _get_or_404(RecurringTicketTemplate, id, db)
    _apply_updates(obj, body.model_dump(exclude_none=True))
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/admin/recurring-templates/{id}", status_code=204)
async def delete_recurring_template(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    obj = await _get_or_404(RecurringTicketTemplate, id, db)
    await db.delete(obj)
    await db.commit()


# ── Portal Branding ───────────────────────────────────────────────────────────

@router.get("/admin/branding", response_model=PortalBrandingOut | None)
async def get_branding(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(PortalBranding).limit(1))
    return result.scalar_one_or_none()


@router.put("/admin/branding", response_model=PortalBrandingOut)
async def upsert_branding(
    body: PortalBrandingUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(PortalBranding).limit(1))
    obj = result.scalar_one_or_none()
    if obj is None:
        obj = PortalBranding()
        db.add(obj)

    _apply_updates(obj, body.model_dump(exclude_none=True))
    await db.commit()
    await db.refresh(obj)
    return obj


# ── TOTP / 2FA ────────────────────────────────────────────────────────────────

@router.post("/auth/totp/setup", response_model=TOTPSetupOut)
async def setup_totp(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new TOTP secret and return the provisioning URI."""
    from app.services.totp_service import generate_secret, get_provisioning_uri

    if current_user.totp_enabled:
        raise HTTPException(status_code=409, detail="2FA is already enabled. Disable first.")

    secret = generate_secret()
    current_user.totp_secret = secret
    await db.commit()

    uri = get_provisioning_uri(secret, current_user.username)
    return TOTPSetupOut(provisioning_uri=uri, secret=secret)


@router.post("/auth/totp/verify", response_model=TOTPVerifyResponse)
async def verify_and_enable_totp(
    body: TOTPVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirm the TOTP code from the authenticator app and enable 2FA."""
    from app.services.totp_service import verify_code, generate_backup_codes

    if not current_user.totp_secret:
        raise HTTPException(status_code=400, detail="Run /auth/totp/setup first")
    if not verify_code(current_user.totp_secret, body.code):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")

    backup_codes = generate_backup_codes()
    current_user.totp_enabled = True
    current_user.totp_backup_codes = backup_codes
    await db.commit()

    return TOTPVerifyResponse(backup_codes=backup_codes)


@router.post("/auth/totp/disable", status_code=204)
async def disable_totp(
    body: TOTPDisableRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disable 2FA after confirming with a current TOTP code."""
    from app.services.totp_service import verify_code

    if not current_user.totp_enabled:
        raise HTTPException(status_code=400, detail="2FA is not enabled")
    if not verify_code(current_user.totp_secret, body.code):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")

    current_user.totp_enabled = False
    current_user.totp_secret = None
    current_user.totp_backup_codes = []
    await db.commit()


# ── User self-service settings ────────────────────────────────────────────────

@router.put("/auth/settings", response_model=UserOut)
async def update_own_settings(
    body: UserSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Allow any authenticated user to update their own name, timezone, and password."""
    from app.core.security import verify_password, get_password_hash

    if body.new_password:
        if not body.current_password:
            raise HTTPException(status_code=400, detail="current_password is required to change password")
        if not verify_password(body.current_password, current_user.hashed_password):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        current_user.hashed_password = get_password_hash(body.new_password)

    if body.name:
        current_user.name = body.name
    if body.initials:
        current_user.initials = body.initials
    if body.preferred_timezone:
        current_user.preferred_timezone = body.preferred_timezone

    await db.commit()
    await db.refresh(current_user)
    return UserOut.model_validate(current_user)
