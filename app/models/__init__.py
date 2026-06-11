from app.models.user import User
from app.models.ticket import Ticket, TicketTimeline, TicketCategory, TicketPriority, TicketStatus, TimelineType, TicketSource, SLAStatus
from app.models.login_session import LoginSession
from app.models.ticket_attachment import TicketAttachment
from app.models.notification import Notification, NotificationType
from app.models.admin import SLAConfig, EmailConfig
from app.models.inbound_email import InboundEmailConfig, EmailTicketLog, InboundAuthType, EmailLogStatus
from app.models.category import Category
from app.models.group import Group
from app.models.sso import SSOConfig
from app.models.feature_models import (
    CustomField,
    TicketTemplate,
    AutomationRule,
    WebhookConfig,
    NotificationChannel,
    Asset,
    EscalationRule,
    RecurringTicketTemplate,
    PortalBranding,
)
from app.models.email_inbox import (
    EmailAccount,
    EmailThread,
    EmailMessage,
    InboxEmailTemplate,
    EmailSignature,
    EmailRoutingRule,
)

__all__ = [
    # Core
    "User",
    "Ticket",
    "TicketTimeline",
    "TicketAttachment",
    "TicketCategory",
    "TicketPriority",
    "TicketStatus",
    "TicketSource",
    "SLAStatus",
    "TimelineType",
    "Notification",
    "NotificationType",
    "SLAConfig",
    "EmailConfig",
    "InboundEmailConfig",
    "EmailTicketLog",
    "InboundAuthType",
    "EmailLogStatus",
    "Category",
    "Group",
    "SSOConfig",
    # Activity / audit
    "LoginSession",
    # Feature models (migration 031)
    "CustomField",
    "TicketTemplate",
    "AutomationRule",
    "WebhookConfig",
    "NotificationChannel",
    "Asset",
    "EscalationRule",
    "RecurringTicketTemplate",
    "PortalBranding",
    # Email inbox (migration 036)
    "EmailAccount",
    "EmailThread",
    "EmailMessage",
    "InboxEmailTemplate",
    "EmailSignature",
    "EmailRoutingRule",
]
