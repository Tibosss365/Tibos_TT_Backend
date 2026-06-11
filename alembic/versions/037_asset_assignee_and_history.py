"""Asset assignee details (name/email/employee code) + assignment history.

Revision ID: 037
Revises: 036
"""
from alembic import op

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE assets ADD COLUMN IF NOT EXISTS assigned_to_name VARCHAR(150)")
    op.execute("ALTER TABLE assets ADD COLUMN IF NOT EXISTS assigned_to_email VARCHAR(255)")
    op.execute("ALTER TABLE assets ADD COLUMN IF NOT EXISTS employee_code VARCHAR(50)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS asset_history (
            id UUID PRIMARY KEY,
            asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            action VARCHAR(20) NOT NULL,
            assigned_to_name VARCHAR(150),
            assigned_to_email VARCHAR(255),
            employee_code VARCHAR(50),
            note TEXT,
            changed_by_name VARCHAR(150),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_asset_history_asset_id ON asset_history (asset_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS asset_history")
    op.execute("ALTER TABLE assets DROP COLUMN IF EXISTS employee_code")
    op.execute("ALTER TABLE assets DROP COLUMN IF EXISTS assigned_to_email")
    op.execute("ALTER TABLE assets DROP COLUMN IF EXISTS assigned_to_name")
