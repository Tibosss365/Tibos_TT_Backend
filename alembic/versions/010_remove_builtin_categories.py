"""Remove built-in seeded categories permanently

The 7 default categories (hardware, software, network, access, email,
security, other) were auto-seeded on first run.  Admins now manage their
own support-topic categories grouped by agent team, so the built-ins are
no longer needed and should be removed from the database.

Revision ID: 010
Revises: 009
Create Date: 2026-04-13 00:00:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Delete all built-in categories; tickets that referenced these slugs
    # will retain the slug string on the ticket row — admins can reassign
    # those tickets to the new support-topic categories they create.
    op.execute("DELETE FROM categories WHERE is_builtin = TRUE")


def downgrade() -> None:
    # Re-insert the 7 default built-in categories if rolling back.
    op.execute("""
        INSERT INTO categories (id, slug, name, color, description, is_builtin, sort_order, created_at)
        VALUES
            (gen_random_uuid(), 'hardware', 'Hardware', '#8B5CF6', 'Physical equipment issues',       TRUE,  10, NOW()),
            (gen_random_uuid(), 'software', 'Software', '#3B82F6', 'Application and OS issues',       TRUE,  20, NOW()),
            (gen_random_uuid(), 'network',  'Network',  '#10B981', 'Connectivity and network issues', TRUE,  30, NOW()),
            (gen_random_uuid(), 'access',   'Access',   '#F59E0B', 'Permissions and login issues',    TRUE,  40, NOW()),
            (gen_random_uuid(), 'email',    'Email',    '#EF4444', 'Email and messaging issues',       TRUE,  50, NOW()),
            (gen_random_uuid(), 'security', 'Security', '#EC4899', 'Security incidents and threats',  TRUE,  60, NOW()),
            (gen_random_uuid(), 'other',    'Other',    '#6B7280', 'Uncategorised requests',           TRUE,  70, NOW())
        ON CONFLICT (slug) DO NOTHING
    """)
