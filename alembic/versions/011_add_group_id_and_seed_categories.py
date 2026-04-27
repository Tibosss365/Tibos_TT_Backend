"""Add group_id to categories and seed IT/MSP support-topic categories

Revision ID: 011
Revises: 010
Create Date: 2026-04-13 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add group_id column to categories (idempotent)
    op.execute("ALTER TABLE categories ADD COLUMN IF NOT EXISTS group_id VARCHAR(80)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_categories_group_id ON categories (group_id)")

    # 2. Seed the 35 support-topic categories across 6 groups
    op.execute("""
        INSERT INTO categories (id, slug, name, color, description, is_builtin, sort_order, group_id, created_at)
        VALUES
          -- Microsoft 365 (group: microsoft-365)
          (gen_random_uuid(), 'exchange-online',          'Exchange Online',           '#0078D4', 'Exchange mailbox, mail flow, calendar and contacts in Microsoft 365',      FALSE,  10, 'microsoft-365',       NOW()),
          (gen_random_uuid(), 'teams-collaboration',      'Teams & Collaboration',     '#464EB8', 'Microsoft Teams meetings, channels, calls and collaboration issues',       FALSE,  20, 'microsoft-365',       NOW()),
          (gen_random_uuid(), 'sharepoint-onedrive',      'SharePoint & OneDrive',     '#038387', 'Document libraries, SharePoint sites and OneDrive sync issues',            FALSE,  30, 'microsoft-365',       NOW()),
          (gen_random_uuid(), 'microsoft-365-apps',       'Microsoft 365 Apps',        '#D83B01', 'Office desktop apps — Word, Excel, PowerPoint, Visio and Project',         FALSE,  40, 'microsoft-365',       NOW()),
          (gen_random_uuid(), 'licensing-subscriptions',  'Licensing & Subscriptions', '#0078D4', 'Licence assignment, subscription changes and Microsoft billing',           FALSE,  50, 'microsoft-365',       NOW()),
          (gen_random_uuid(), 'power-platform',           'Power Platform',            '#742774', 'Power Automate, Power Apps, Power BI and Power Virtual Agents',            FALSE,  60, 'microsoft-365',       NOW()),
          (gen_random_uuid(), 'intune-mdm',               'Intune & MDM',              '#00A4EF', 'Device enrolment, compliance policies and mobile device management',       FALSE,  70, 'microsoft-365',       NOW()),
          (gen_random_uuid(), 'admin-centre',             'Admin Centre',              '#5C2D91', 'Microsoft 365 Admin Centre, tenant settings and global admin tasks',        FALSE,  80, 'microsoft-365',       NOW()),

          -- Migration Services (group: migration-services)
          (gen_random_uuid(), 'email-migration',              'Email Migration',              '#7C3AED', 'Mailbox migrations to or from Microsoft 365 / Exchange Online',         FALSE, 110, 'migration-services',  NOW()),
          (gen_random_uuid(), 'tenant-to-tenant',             'Tenant-to-Tenant',             '#6D28D9', 'Cross-tenant migrations including users, data and licences',            FALSE, 120, 'migration-services',  NOW()),
          (gen_random_uuid(), 'onedrive-sharepoint-migration','OneDrive / SharePoint Migration','#5B21B6','File share and on-premise content migrations to SharePoint Online',   FALSE, 130, 'migration-services',  NOW()),
          (gen_random_uuid(), 'identity-aad',                 'Identity & Azure AD',          '#4C1D95', 'Azure AD setup, directory sync, ADFS federation and Entra ID',         FALSE, 140, 'migration-services',  NOW()),
          (gen_random_uuid(), 'hybrid-exchange',              'Hybrid Exchange',              '#8B5CF6', 'On-premise and cloud Exchange co-existence and hybrid configuration',  FALSE, 150, 'migration-services',  NOW()),
          (gen_random_uuid(), 'post-migration-support',       'Post-Migration Support',       '#A78BFA', 'Issues and cleanup work after a completed migration project',           FALSE, 160, 'migration-services',  NOW()),

          -- Security & Compliance (group: security-compliance)
          (gen_random_uuid(), 'microsoft-defender',      'Microsoft Defender',       '#DC2626', 'Endpoint protection, threat detection and Defender for M365 alerts',        FALSE, 210, 'security-compliance', NOW()),
          (gen_random_uuid(), 'conditional-access',      'Conditional Access',       '#B91C1C', 'MFA enforcement, named locations and Conditional Access policy issues',      FALSE, 220, 'security-compliance', NOW()),
          (gen_random_uuid(), 'data-loss-prevention',    'Data Loss Prevention',     '#991B1B', 'DLP policy configuration, false positives and sensitive data protection',    FALSE, 230, 'security-compliance', NOW()),
          (gen_random_uuid(), 'purview-ediscovery',      'Purview & eDiscovery',     '#7F1D1D', 'Microsoft Purview compliance portal, content search and eDiscovery holds',   FALSE, 240, 'security-compliance', NOW()),
          (gen_random_uuid(), 'phishing-threat-response','Phishing & Threat Response','#EF4444','Phishing incident response, compromised accounts and threat investigation',  FALSE, 250, 'security-compliance', NOW()),
          (gen_random_uuid(), 'audit-reporting',         'Audit & Reporting',        '#F87171', 'Unified audit log, compliance reports and security posture reviews',          FALSE, 260, 'security-compliance', NOW()),

          -- Infrastructure & Network (group: infrastructure-network)
          (gen_random_uuid(), 'active-directory',   'Active Directory',    '#059669', 'On-premise AD, Group Policy, DNS, DHCP and domain controller issues',          FALSE, 310, 'infrastructure-network', NOW()),
          (gen_random_uuid(), 'vpn-remote-access',  'VPN & Remote Access', '#047857', 'VPN client issues, remote desktop and secure remote connectivity',              FALSE, 320, 'infrastructure-network', NOW()),
          (gen_random_uuid(), 'networking',         'Networking',          '#065F46', 'Switches, routers, wireless access points and general connectivity',             FALSE, 330, 'infrastructure-network', NOW()),
          (gen_random_uuid(), 'server-storage',     'Server & Storage',    '#10B981', 'Windows Server, file servers, NAS devices and storage management',              FALSE, 340, 'infrastructure-network', NOW()),
          (gen_random_uuid(), 'backup-recovery',    'Backup & Recovery',   '#34D399', 'Backup job failures, restore requests and disaster recovery tests',              FALSE, 350, 'infrastructure-network', NOW()),
          (gen_random_uuid(), 'virtualisation',     'Virtualisation',      '#6EE7B7', 'Hyper-V, VMware vSphere and virtualised workload issues',                       FALSE, 360, 'infrastructure-network', NOW()),

          -- End User Support L1 (group: end-user-support)
          (gen_random_uuid(), 'account-password',      'Account & Password',     '#D97706', 'Account unlocks, password resets and MFA enrolment for end users',         FALSE, 410, 'end-user-support',    NOW()),
          (gen_random_uuid(), 'hardware-peripherals',  'Hardware & Peripherals', '#B45309', 'Laptops, desktops, monitors, printers and docking station issues',          FALSE, 420, 'end-user-support',    NOW()),
          (gen_random_uuid(), 'software-installation', 'Software Installation',  '#92400E', 'Application deployment requests and software install or update issues',     FALSE, 430, 'end-user-support',    NOW()),
          (gen_random_uuid(), 'email-calendar',        'Email & Calendar',       '#F59E0B', 'Outlook client, mailbox configuration and calendar sharing for end users',  FALSE, 440, 'end-user-support',    NOW()),
          (gen_random_uuid(), 'general-it-request',    'General IT Request',     '#FCD34D', 'Miscellaneous IT requests not covered by another category',                 FALSE, 450, 'end-user-support',    NOW()),

          -- Azure & Cloud (group: azure-cloud)
          (gen_random_uuid(), 'azure-infrastructure', 'Azure Infrastructure', '#2563EB', 'Azure VMs, virtual networks, resource groups and IaaS workloads',        FALSE, 510, 'azure-cloud', NOW()),
          (gen_random_uuid(), 'azure-ad-entra',       'Azure AD / Entra ID',  '#1D4ED8', 'Azure Active Directory, Entra ID tenants and identity platform issues',   FALSE, 520, 'azure-cloud', NOW()),
          (gen_random_uuid(), 'azure-backup-dr',      'Azure Backup & DR',    '#1E40AF', 'Azure Recovery Services vault, backup jobs and disaster recovery plans',  FALSE, 530, 'azure-cloud', NOW()),
          (gen_random_uuid(), 'azure-cost-billing',   'Azure Cost & Billing', '#1E3A8A', 'Azure subscription costs, budget alerts and billing management',          FALSE, 540, 'azure-cloud', NOW())

        ON CONFLICT (slug) DO NOTHING
    """)


def downgrade() -> None:
    # Remove the seeded categories then drop the column
    op.execute("""
        DELETE FROM categories WHERE slug IN (
          'exchange-online','teams-collaboration','sharepoint-onedrive','microsoft-365-apps',
          'licensing-subscriptions','power-platform','intune-mdm','admin-centre',
          'email-migration','tenant-to-tenant','onedrive-sharepoint-migration','identity-aad',
          'hybrid-exchange','post-migration-support',
          'microsoft-defender','conditional-access','data-loss-prevention','purview-ediscovery',
          'phishing-threat-response','audit-reporting',
          'active-directory','vpn-remote-access','networking','server-storage',
          'backup-recovery','virtualisation',
          'account-password','hardware-peripherals','software-installation','email-calendar',
          'general-it-request',
          'azure-infrastructure','azure-ad-entra','azure-backup-dr','azure-cost-billing'
        )
    """)
    op.drop_index("ix_categories_group_id", table_name="categories")
    op.drop_column("categories", "group_id")
