"""add documents.summary

Revision ID: 31f17fb47de8
Revises: b6019841bba0
Create Date: 2026-10-14

`Document.summary` exists in the model and is read by the GLOBAL branch
(`fetch_document_summaries`), but the init migration never created it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '31f17fb47de8'
down_revision: Union[str, Sequence[str], None] = 'b6019841bba0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('documents', sa.Column('summary', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('documents', 'summary')
