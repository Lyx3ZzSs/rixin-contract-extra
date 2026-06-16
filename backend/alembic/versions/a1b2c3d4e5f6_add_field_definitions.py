"""add_field_definitions

Revision ID: a1b2c3d4e5f6
Revises: 520bece3496b
Create Date: 2026-06-16 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '520bece3496b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'field_definitions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('field_key', sa.String(100), nullable=False),
        sa.Column('field_name', sa.String(200), nullable=False),
        sa.Column('field_category', sa.String(50), nullable=False, server_default='party'),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('value_type', sa.String(20), nullable=False, server_default='string'),
        sa.Column('required', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column(
            'created_at',
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('field_key'),
    )
    op.create_index(op.f('ix_field_definitions_field_key'), 'field_definitions', ['field_key'])
    op.create_index(op.f('ix_field_definitions_is_active'), 'field_definitions', ['is_active'])


def downgrade() -> None:
    op.drop_index(op.f('ix_field_definitions_is_active'), table_name='field_definitions')
    op.drop_index(op.f('ix_field_definitions_field_key'), table_name='field_definitions')
    op.drop_table('field_definitions')
