"""Replace categories with correct IT/MSP support-topic structure

Removes the provisional categories from migration 011 and inserts
the finalised category list as agreed with the admin.

Revision ID: 012
Revises: 011
Create Date: 2026-04-13 00:00:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Slugs inserted by migration 011 (to be removed)
_011_SLUGS = (
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
    'azure-infrastructure','azure-ad-entra','azure-backup-dr','azure-cost-billing',
)


def upgrade() -> None:
    # Remove old provisional categories
    slugs_csv = ", ".join(f"'{s}'" for s in _011_SLUGS)
    op.execute(f"DELETE FROM categories WHERE slug IN ({slugs_csv})")

    # Insert finalised categories
    op.execute("""
        INSERT INTO categories (id, slug, name, color, description, is_builtin, sort_order, group_id, created_at)
        VALUES
          -- ── Microsoft 365 ──────────────────────────────────────────────────
          (gen_random_uuid(), 'exchange-outlook',         'Exchange & Outlook',       '#0078D4', 'Mailbox issues, email rules, calendar and Outlook client',          FALSE,  10, 'microsoft-365',        NOW()),
          (gen_random_uuid(), 'teams-collaboration',      'Teams & Collaboration',    '#464EB8', 'Teams setup, meetings, channels and collaboration issues',           FALSE,  20, 'microsoft-365',        NOW()),
          (gen_random_uuid(), 'sharepoint-onedrive',      'SharePoint & OneDrive',    '#038387', 'SharePoint sites, document libraries and OneDrive sync issues',      FALSE,  30, 'microsoft-365',        NOW()),
          (gen_random_uuid(), 'licensing-subscriptions',  'Licensing & Subscriptions','#0078D4', 'Licence assignment, renewals and subscription upgrades',             FALSE,  40, 'microsoft-365',        NOW()),
          (gen_random_uuid(), 'intune-device-management', 'Intune & Device Management','#00A4EF','MDM enrolment, device policy and compliance management',             FALSE,  50, 'microsoft-365',        NOW()),
          (gen_random_uuid(), 'azure-ad-entra-id',        'Azure AD / Entra ID',      '#5C2D91', 'User accounts, SSO, Conditional Access and Entra ID settings',       FALSE,  60, 'microsoft-365',        NOW()),
          (gen_random_uuid(), 'm365-admin-centre',        'M365 Admin Centre',        '#D83B01', 'Tenant configuration, domain management and global admin tasks',     FALSE,  70, 'microsoft-365',        NOW()),
          (gen_random_uuid(), 'mobile-apps-m365',         'Mobile Apps (M365)',       '#742774', 'Outlook mobile, Teams mobile, MFA app setup and troubleshooting',    FALSE,  80, 'microsoft-365',        NOW()),

          -- ── Migration Services ─────────────────────────────────────────────
          (gen_random_uuid(), 'imap-email-migration',       'IMAP Email Migration',       '#7C3AED', 'Migrating mailboxes from IMAP servers to Exchange Online',          FALSE, 110, 'migration-services',   NOW()),
          (gen_random_uuid(), 'google-workspace-m365',      'Google Workspace → M365',    '#6D28D9', 'Gmail, Google Drive and Google Calendar migration to Microsoft 365', FALSE, 120, 'migration-services',   NOW()),
          (gen_random_uuid(), 'avepoint-migration',         'AvePoint Migration',         '#5B21B6', 'AvePoint-managed file and content migration projects',               FALSE, 130, 'migration-services',   NOW()),
          (gen_random_uuid(), 'tenant-to-tenant-migration', 'Tenant-to-Tenant Migration', '#4C1D95', 'Cross-tenant mailbox and data moves between M365 tenants',           FALSE, 140, 'migration-services',   NOW()),
          (gen_random_uuid(), 'file-share-migration',       'File Share Migration',       '#8B5CF6', 'On-premises file shares migrated to SharePoint Online or OneDrive',  FALSE, 150, 'migration-services',   NOW()),
          (gen_random_uuid(), 'user-onboarding',            'User Onboarding',            '#A78BFA', 'New user setup, account provisioning and welcome-pack tasks',         FALSE, 160, 'migration-services',   NOW()),

          -- ── Security & Compliance ──────────────────────────────────────────
          (gen_random_uuid(), 'mfa-conditional-access', 'MFA & Conditional Access', '#DC2626', 'Multi-factor authentication setup and Conditional Access policy issues', FALSE, 210, 'security-compliance',  NOW()),
          (gen_random_uuid(), 'microsoft-defender',     'Microsoft Defender',       '#B91C1C', 'Endpoint, email and identity protection via Microsoft Defender',          FALSE, 220, 'security-compliance',  NOW()),
          (gen_random_uuid(), 'permissions-access',     'Permissions & Access',     '#991B1B', 'Role assignments, access reviews and permission management',              FALSE, 230, 'security-compliance',  NOW()),
          (gen_random_uuid(), 'compliance-dlp',         'Compliance & DLP',         '#7F1D1D', 'Retention policies, eDiscovery and data loss prevention configuration',   FALSE, 240, 'security-compliance',  NOW()),
          (gen_random_uuid(), 'security-incidents',     'Security Incidents',       '#EF4444', 'Breach response, suspicious activity and security incident handling',     FALSE, 250, 'security-compliance',  NOW()),
          (gen_random_uuid(), 'azure-ad-identity',      'Azure AD / Identity',      '#F87171', 'Identity governance, self-service password reset and Azure AD issues',    FALSE, 260, 'security-compliance',  NOW()),

          -- ── Infrastructure & Network ───────────────────────────────────────
          (gen_random_uuid(), 'firewall-network',    'Firewall & Network',    '#059669', 'Firewall rules, routing, switching and network infrastructure',       FALSE, 310, 'infrastructure-network', NOW()),
          (gen_random_uuid(), 'vpn-remote-access',   'VPN & Remote Access',   '#047857', 'VPN setup, client issues and secure remote connectivity',             FALSE, 320, 'infrastructure-network', NOW()),
          (gen_random_uuid(), 'dns-domains',         'DNS & Domains',         '#065F46', 'Domain configuration, DNS records and SSL certificate management',    FALSE, 330, 'infrastructure-network', NOW()),
          (gen_random_uuid(), 'servers-storage',     'Servers & Storage',     '#10B981', 'On-premises servers, NAS devices and storage management',             FALSE, 340, 'infrastructure-network', NOW()),
          (gen_random_uuid(), 'backup-recovery',     'Backup & Recovery',     '#34D399', 'Backup job monitoring, restore requests and disaster recovery tests',  FALSE, 350, 'infrastructure-network', NOW()),
          (gen_random_uuid(), 'internet-connectivity','Internet & Connectivity','#6EE7B7','ISP issues, bandwidth problems and office connectivity outages',      FALSE, 360, 'infrastructure-network', NOW()),

          -- ── End User Support L1 ────────────────────────────────────────────
          (gen_random_uuid(), 'password-account-reset', 'Password & Account Reset', '#D97706', 'Password resets, account lockouts and self-service recovery',              FALSE, 410, 'end-user-support',     NOW()),
          (gen_random_uuid(), 'hardware-devices',       'Hardware & Devices',       '#B45309', 'Laptops, desktops, monitors and peripheral hardware issues',                FALSE, 420, 'end-user-support',     NOW()),
          (gen_random_uuid(), 'software-installation',  'Software Installation',    '#92400E', 'Application installs, updates and software licence requests',               FALSE, 430, 'end-user-support',     NOW()),
          (gen_random_uuid(), 'printing-peripherals',   'Printing & Peripherals',   '#F59E0B', 'Printers, scanners, drivers and peripheral device support',                 FALSE, 440, 'end-user-support',     NOW()),
          (gen_random_uuid(), 'how-to-training',        'How-to & Training',        '#FCD34D', 'User guidance, feature walkthroughs and self-service how-to requests',      FALSE, 450, 'end-user-support',     NOW()),

          -- ── Azure & Cloud ──────────────────────────────────────────────────
          (gen_random_uuid(), 'azure-virtual-machines', 'Azure Virtual Machines', '#2563EB', 'VM provisioning, sizing, patching and virtual machine management',     FALSE, 510, 'azure-cloud',          NOW()),
          (gen_random_uuid(), 'azure-storage',          'Azure Storage',          '#1D4ED8', 'Blob storage, Azure Files and Recovery Services vault management',      FALSE, 520, 'azure-cloud',          NOW()),
          (gen_random_uuid(), 'azure-networking',       'Azure Networking',       '#1E40AF', 'Virtual networks, NSGs, ExpressRoute and Azure network configuration',  FALSE, 530, 'azure-cloud',          NOW()),
          (gen_random_uuid(), 'cost-management',        'Cost Management',        '#1E3A8A', 'Azure budgets, cost alerts and resource cost optimisation',             FALSE, 540, 'azure-cloud',          NOW())

        ON CONFLICT (slug) DO NOTHING
    """)


def downgrade() -> None:
    new_slugs = (
        'exchange-outlook','teams-collaboration','sharepoint-onedrive','licensing-subscriptions',
        'intune-device-management','azure-ad-entra-id','m365-admin-centre','mobile-apps-m365',
        'imap-email-migration','google-workspace-m365','avepoint-migration','tenant-to-tenant-migration',
        'file-share-migration','user-onboarding',
        'mfa-conditional-access','microsoft-defender','permissions-access','compliance-dlp',
        'security-incidents','azure-ad-identity',
        'firewall-network','vpn-remote-access','dns-domains','servers-storage',
        'backup-recovery','internet-connectivity',
        'password-account-reset','hardware-devices','software-installation','printing-peripherals',
        'how-to-training',
        'azure-virtual-machines','azure-storage','azure-networking','cost-management',
    )
    slugs_csv = ", ".join(f"'{s}'" for s in new_slugs)
    op.execute(f"DELETE FROM categories WHERE slug IN ({slugs_csv})")
