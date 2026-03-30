"""
Seed the database with default agents, SLA config, email config, and sample tickets.

Usage:
    python seed.py
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.security import hash_password
from app.database import AsyncSessionLocal, engine, Base
from app.models.admin import EmailConfig, SLAConfig, EmailType, SMTPSecurity
from app.models.notification import Notification, NotificationType
from app.models.ticket import (
    Ticket,
    TicketCategory,
    TicketPriority,
    TicketStatus,
    TicketTimeline,
    TimelineType,
)
from app.models.user import User, UserRole

# ── Seed data ──────────────────────────────────────────────────────────────

DEFAULT_AGENTS = [
    {"name": "Sarah Chen",   "initials": "SC", "group": "Security",     "username": "siva",  "password": "siva",  "role": UserRole.technician},
    {"name": "Marcus Webb",  "initials": "MW", "group": "Network",      "username": "marcus", "password": "marc", "role": UserRole.technician},
    {"name": "Priya Nair",   "initials": "PN", "group": "L1 Support",   "username": "priya",  "password": "priya123",  "role": UserRole.technician},
    {"name": "Tom Bradley",  "initials": "TB", "group": "Application",  "username": "tom",    "password": "tom123",    "role": UserRole.technician},
    {"name": "John Doe",     "initials": "JD", "group": "IT Admin",     "username": "admin",  "password": "admin",     "role": UserRole.admin},
]

now = datetime.now(timezone.utc)

SEED_TICKETS = [
    {"subject": "Laptop won't boot after Windows update", "category": "software", "priority": TicketPriority.high, "status": TicketStatus.open, "submitter_name": "Alice Johnson", "company": "Acme Corp", "contact_name": "Alice Johnson", "email": "alice@acme.com", "description": "Laptop displays blue screen on startup after the latest Windows update."},
    {"subject": "VPN disconnects every 30 minutes", "category": "network", "priority": TicketPriority.medium, "status": TicketStatus.in_progress, "submitter_name": "Bob Smith", "company": "TechStart", "contact_name": "Bob Smith", "email": "bob@techstart.com", "description": "Remote VPN drops connection exactly every 30 minutes requiring manual reconnect."},
    {"subject": "Printer offline – 3rd floor", "category": "hardware", "priority": TicketPriority.low, "status": TicketStatus.resolved, "submitter_name": "Carol White", "company": "Globex", "contact_name": "Carol White", "email": "carol@globex.com", "description": "HP LaserJet on 3rd floor showing offline. Tried power cycling but still offline."},
    {"subject": "Ransomware alert on workstation WS-042", "category": "security", "priority": TicketPriority.critical, "status": TicketStatus.in_progress, "submitter_name": "Dan Martinez", "company": "Initech", "contact_name": "Dan Martinez", "email": "dan@initech.com", "description": "Multiple files encrypted with .locked extension. Workstation isolated from network."},
    {"subject": "Cannot access SharePoint site", "category": "access", "priority": TicketPriority.medium, "status": TicketStatus.open, "submitter_name": "Eve Davis", "company": "Umbrella Ltd", "contact_name": "Eve Davis", "email": "eve@umbrella.com", "description": "Getting 403 Forbidden on the Marketing SharePoint site since yesterday."},
    {"subject": "Outlook not syncing emails", "category": "email", "priority": TicketPriority.high, "status": TicketStatus.open, "submitter_name": "Frank Wilson", "company": "Aperture", "contact_name": "Frank Wilson", "email": "frank@aperture.com", "description": "Outlook 365 stuck on 'Sending/Receiving'. New emails not arriving since this morning."},
    {"subject": "Monitor flickering on Dell OptiPlex", "category": "hardware", "priority": TicketPriority.low, "status": TicketStatus.open, "submitter_name": "Grace Lee", "company": "Acme Corp", "contact_name": "Grace Lee", "email": "grace@acme.com", "description": "24\" Dell monitor flickers intermittently, usually after 1-2 hours of use."},
    {"subject": "Software license expired – AutoCAD", "category": "software", "priority": TicketPriority.high, "status": TicketStatus.on_hold, "submitter_name": "Hank Brown", "company": "Blueprint Co", "contact_name": "Hank Brown", "email": "hank@blueprint.com", "description": "AutoCAD 2024 showing license expired. Engineering team blocked from working."},
    {"subject": "Wi-Fi drops in conference room B", "category": "network", "priority": TicketPriority.medium, "status": TicketStatus.in_progress, "submitter_name": "Iris Taylor", "company": "TechStart", "contact_name": "Iris Taylor", "email": "iris@techstart.com", "description": "Wi-Fi signal very weak in conference room B. Drops during video calls."},
    {"subject": "Password reset – account locked", "category": "access", "priority": TicketPriority.medium, "status": TicketStatus.resolved, "submitter_name": "Jack Anderson", "company": "Globex", "contact_name": "Jack Anderson", "email": "jack@globex.com", "description": "AD account locked after too many failed login attempts. Needs password reset."},
    {"subject": "Suspicious email with attachment received", "category": "security", "priority": TicketPriority.high, "status": TicketStatus.open, "submitter_name": "Karen White", "company": "Initech", "contact_name": "Karen White", "email": "karen@initech.com", "description": "Received email claiming to be from IT with .exe attachment. Did not open. Forwarding to security."},
    {"subject": "Excel macro not running after update", "category": "software", "priority": TicketPriority.medium, "status": TicketStatus.open, "submitter_name": "Liam Clark", "company": "Aperture", "contact_name": "Liam Clark", "email": "liam@aperture.com", "description": "Finance team macros stopped running after Office update KB5002567."},
    {"subject": "New employee workstation setup", "category": "hardware", "priority": TicketPriority.low, "status": TicketStatus.closed, "submitter_name": "Maria Garcia", "company": "Umbrella Ltd", "contact_name": "Maria Garcia", "email": "maria@umbrella.com", "description": "Need workstation configured for new hire starting Monday. Standard software install."},
    {"subject": "Phishing email reported by multiple users", "category": "security", "priority": TicketPriority.high, "status": TicketStatus.in_progress, "submitter_name": "Paul Zhang", "company": "Blueprint Co", "contact_name": "Paul Zhang", "email": "paul@blueprint.com", "description": "Multiple users received fake IT helpdesk email asking for credentials. Investigating origin."},
    {"subject": "CRM application slow performance", "category": "software", "priority": TicketPriority.medium, "status": TicketStatus.open, "submitter_name": "Nina Patel", "company": "Acme Corp", "contact_name": "Nina Patel", "email": "nina@acme.com", "description": "Salesforce CRM taking 10+ seconds to load pages. Affecting sales team productivity."},
]


async def seed():
    print("Seeding database...")

    async with AsyncSessionLocal() as db:
        # Check if already seeded
        existing = await db.execute(select(User))
        if existing.scalars().first():
            print("Database already seeded. Skipping.")
            return

        # Create agents
        agents: dict[str, User] = {}
        for data in DEFAULT_AGENTS:
            user = User(
                name=data["name"],
                initials=data["initials"],
                group=data["group"],
                username=data["username"],
                hashed_password=hash_password(data["password"]),
                role=data["role"],
            )
            db.add(user)
            agents[data["username"]] = user

        await db.flush()

        # SLA config
        sla = SLAConfig(critical_hours=1, high_hours=4, medium_hours=8, low_hours=24)
        db.add(sla)

        # Email config
        email_cfg = EmailConfig(
            type=EmailType.smtp,
            trigger_new=True,
            trigger_assign=True,
            trigger_resolve=True,
        )
        db.add(email_cfg)

        # Inbound email config — default category slug = "email"
        from app.models.inbound_email import InboundEmailConfig
        inbound_cfg = InboundEmailConfig(
            enabled=False,
            default_category="email",
        )
        db.add(inbound_cfg)

        await db.flush()

        # Assign agents to tickets in rotation (all non-admin users)
        agent_list = [
            agents[d["username"]]
            for d in DEFAULT_AGENTS
            if d["role"] == UserRole.technician
        ]
        admin_username = next(d["username"] for d in DEFAULT_AGENTS if d["role"] == UserRole.admin)
        admin_user = agents[admin_username]

        for idx, t_data in enumerate(SEED_TICKETS):
            assignee = agent_list[idx % len(agent_list)]
            created_at = now - timedelta(days=14 - idx, hours=idx * 2)
            updated_at = created_at + timedelta(hours=idx + 1)

            ticket = Ticket(
                subject=t_data["subject"],
                category=t_data["category"],
                priority=t_data["priority"],
                status=t_data["status"],
                submitter_name=t_data["submitter_name"],
                company=t_data["company"],
                contact_name=t_data["contact_name"],
                email=t_data["email"],
                description=t_data["description"],
                assignee_id=assignee.id,
                created_at=created_at,
                updated_at=updated_at,
            )
            db.add(ticket)
            await db.flush()

            # Timeline entries
            db.add(TicketTimeline(
                ticket_id=ticket.id,
                type=TimelineType.created,
                text=f"Ticket created by <strong>{t_data['submitter_name']}</strong>",
                created_at=created_at,
            ))
            db.add(TicketTimeline(
                ticket_id=ticket.id,
                type=TimelineType.assign,
                text=f"Assigned to <strong>{assignee.name}</strong> by <strong>{admin_user.name}</strong>",
                author_id=admin_user.id,
                created_at=created_at + timedelta(minutes=5),
            ))

            if t_data["status"] in (TicketStatus.in_progress, TicketStatus.resolved, TicketStatus.closed):
                db.add(TicketTimeline(
                    ticket_id=ticket.id,
                    type=TimelineType.status,
                    text=f"Status changed to <strong>{t_data['status'].value}</strong> by <strong>{assignee.name}</strong>",
                    author_id=assignee.id,
                    created_at=created_at + timedelta(minutes=30),
                ))

            if t_data["status"] in (TicketStatus.resolved, TicketStatus.closed):
                db.add(TicketTimeline(
                    ticket_id=ticket.id,
                    type=TimelineType.resolved,
                    text=f"Ticket resolved by <strong>{assignee.name}</strong>",
                    author_id=assignee.id,
                    created_at=updated_at,
                ))

        await db.commit()
        print(f"[OK] Created {len(DEFAULT_AGENTS)} agents")
        print(f"[OK] Created {len(SEED_TICKETS)} sample tickets")
        print("[OK] SLA config created")
        print("[OK] Email config created")
        print("Seeding complete!")


if __name__ == "__main__":
    asyncio.run(seed())
