"""remove_field_category

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-16 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('extracted_fields', 'field_category')
    op.drop_column('field_definitions', 'field_category')


def downgrade() -> None:
    op.add_column(
        'field_definitions',
        sa.Column('field_category', sa.String(50), nullable=False, server_default='basic'),
    )
    op.add_column(
        'extracted_fields',
        sa.Column('field_category', sa.String(50), nullable=False, server_default='basic'),
    )
