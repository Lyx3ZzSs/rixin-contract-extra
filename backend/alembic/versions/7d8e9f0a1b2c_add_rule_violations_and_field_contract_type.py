"""add_rule_violations_and_field_contract_type

Revision ID: 7d8e9f0a1b2c
Revises: 0a1b2c3d4e5f
Create Date: 2026-06-25 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7d8e9f0a1b2c'
down_revision: Union[str, None] = '0a1b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'rule_violations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('contract_id', sa.Uuid(), nullable=False),
        sa.Column('field_key', sa.String(length=100), nullable=True),
        sa.Column('rule_key', sa.String(length=100), nullable=False),
        sa.Column('severity', sa.String(length=20), nullable=False, server_default='warning'),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('ignored_at', sa.DateTime(), nullable=True),
        sa.Column('ignored_by', sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(['contract_id'], ['contracts.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_rule_violations_contract_id', 'rule_violations', ['contract_id'])
    op.create_index('ix_rv_contract_rule_field', 'rule_violations', ['contract_id', 'rule_key', 'field_key'])

    op.add_column(
        'field_definitions',
        sa.Column('contract_type', sa.String(length=50), nullable=True),
    )
    op.create_index('ix_field_definitions_contract_type', 'field_definitions', ['contract_type'])


def downgrade() -> None:
    op.drop_index('ix_field_definitions_contract_type', table_name='field_definitions')
    op.drop_column('field_definitions', 'contract_type')

    op.drop_index('ix_rv_contract_rule_field', table_name='rule_violations')
    op.drop_index('ix_rule_violations_contract_id', table_name='rule_violations')
    op.drop_table('rule_violations')
