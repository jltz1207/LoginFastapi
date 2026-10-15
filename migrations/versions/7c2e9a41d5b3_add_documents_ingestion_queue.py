"""add documents ingestion queue columns

Revision ID: 7c2e9a41d5b3
Revises: 31f17fb47de8
Create Date: 2026-10-14

Upload now only inserts a PENDING row; an in-process worker claims it with
`FOR UPDATE SKIP LOCKED` (app/ingestion/repository.py).

- attempt_count: bumped on every claim; also the fencing token for status writes.
- processing_started_at: lease start, so a PROCESSING row whose worker died is requeued.
- ix_documents_status_created_dt: the claim query is `status = 'PENDING' ORDER BY created_dt`.

Hand-written: autogenerate would also try to drop the LangGraph `checkpoint_*` tables
and rewrite the native ENUMs (see plans/finished/tenant-id-fixes-20261014.md).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c2e9a41d5b3'
down_revision: Union[str, Sequence[str], None] = '31f17fb47de8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'documents',
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'documents',
        sa.Column('processing_started_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        'ix_documents_status_created_dt', 'documents', ['status', 'created_dt'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_documents_status_created_dt', table_name='documents')
    op.drop_column('documents', 'processing_started_at')
    op.drop_column('documents', 'attempt_count')
