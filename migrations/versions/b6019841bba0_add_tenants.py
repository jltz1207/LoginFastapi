"""add tenants table and tenant_id columns

Revision ID: b6019841bba0
Revises: 061f06c4ef78
Create Date: 2026-10-13

Existing rows are backfilled to a 'default' tenant (ACTIVE) before tenant_id
becomes NOT NULL, so this is safe to run on a database that already has data.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6019841bba0'
down_revision: Union[str, Sequence[str], None] = '061f06c4ef78'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_TENANT_SLUG = 'default'
TENANT_SCOPED_TABLES = ('users', 'AsistantKnowledgeBases', 'AsistantMessages', 'documents')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('tenants',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('slug', sa.String(), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=True),
    sa.Column('status', sa.Enum('ACTIVE', 'PENDING', 'SUSPENDED', 'ARCHIVED', name='tenantstatus',
                                native_enum=False, create_constraint=True, length=50),
              server_default='PENDING', nullable=False),
    sa.Column('created_dt', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('created_by', sa.String(length=255), nullable=True),
    sa.Column('modified_dt', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('modified_by', sa.String(length=255), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tenants_name'), 'tenants', ['name'], unique=True)
    op.create_index(op.f('ix_tenants_slug'), 'tenants', ['slug'], unique=True)
    op.create_index(op.f('ix_tenants_deleted_at'), 'tenants', ['deleted_at'], unique=False)

    op.execute(
        sa.text("INSERT INTO tenants (name, slug, status) VALUES ('Default', :slug, 'ACTIVE')")
        .bindparams(slug=DEFAULT_TENANT_SLUG)
    )

    # nullable -> backfill -> NOT NULL, so tables with existing rows don't fail.
    for table in TENANT_SCOPED_TABLES:
        op.add_column(table, sa.Column('tenant_id', sa.UUID(), nullable=True))
        op.execute(
            sa.text(f'UPDATE "{table}" SET tenant_id = (SELECT id FROM tenants WHERE slug = :slug)')
            .bindparams(slug=DEFAULT_TENANT_SLUG)
        )
        op.alter_column(table, 'tenant_id', nullable=False)
        op.create_index(op.f(f'ix_{table}_tenant_id'), table, ['tenant_id'], unique=False)

    # Fails if a tenant already has duplicate usernames; dedupe those rows first.
    op.create_unique_constraint('uq_users_tenant_id_username', 'users', ['tenant_id', 'username'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_users_tenant_id_username', 'users', type_='unique')
    for table in reversed(TENANT_SCOPED_TABLES):
        op.drop_index(op.f(f'ix_{table}_tenant_id'), table_name=table)
        op.drop_column(table, 'tenant_id')
    op.drop_index(op.f('ix_tenants_deleted_at'), table_name='tenants')
    op.drop_index(op.f('ix_tenants_slug'), table_name='tenants')
    op.drop_index(op.f('ix_tenants_name'), table_name='tenants')
    op.drop_table('tenants')
