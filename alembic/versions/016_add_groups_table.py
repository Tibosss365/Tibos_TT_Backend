"""Add groups table with default IT/MSP support team groups

Groups are the top-level containers for categories (e.g. 'Microsoft 365',
'Security & Compliance'). The id is a URL-safe slug that is stored as a
plain-text foreign key on categories.group_id and tickets.group_id.

Revision ID: 016
Revises: 015
Create Date: 2026-04-17 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("id",          sa.String(80),              primary_key=True, nullable=False),
        sa.Column("name",        sa.String(100),             nullable=False),
        sa.Column("description", sa.Text(),                  nullable=True),
        sa.Column("color",       sa.String(7),               nullable=False, server_default="#6B7280"),
        sa.Column("is_builtin",  sa.Boolean(),               nullable=False, server_default="false"),
        sa.Column("created_at",  sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # Seed the six default groups that match category group_ids already in the DB
    op.execute("""
        INSERT INTO groups (id, name, description, color, is_builtin, created_at)
        VALUES
          ('microsoft-365',         'Microsoft 365',           'Exchange Online, Teams, SharePoint, Intune and all M365 workloads', '#0078D4', false, NOW()),
          ('migration-services',    'Migration Services',      'Mailbox, tenant-to-tenant and file-share migration projects',        '#7C3AED', false, NOW()),
          ('security-compliance',   'Security & Compliance',   'Defender, Conditional Access, DLP, Purview and threat response',     '#DC2626', false, NOW()),
          ('infrastructure-network','Infrastructure & Network','Active Directory, networking, servers and virtualisation',            '#059669', false, NOW()),
          ('end-user-support',      'End User Support L1',     'First-line support for accounts, hardware, software and email',      '#D97706', false, NOW()),
          ('azure-cloud',           'Azure & Cloud',           'Azure infrastructure, Entra ID, backup and cloud cost management',   '#2563EB', false, NOW())
        ON CONFLICT (id) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("groups")
