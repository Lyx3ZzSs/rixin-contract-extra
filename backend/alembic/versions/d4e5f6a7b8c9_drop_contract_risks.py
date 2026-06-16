"""drop_contract_risks

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-16 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('ix_cr_contract_level', table_name='contract_risks')
    op.drop_index(op.f('ix_contract_risks_contract_id'), table_name='contract_risks')
    op.drop_table('contract_risks')


def downgrade() -> None:
    op.create_table(
        'contract_risks',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('contract_id', sa.Uuid(), nullable=False),
        sa.Column('field_id', sa.Uuid(), nullable=True),
        sa.Column('clause_id', sa.Uuid(), nullable=True),
        sa.Column('risk_level', sa.String(length=20), nullable=False),
        sa.Column('risk_type', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('evidence', sa.Text(), nullable=True),
        sa.Column('suggestion', sa.Text(), nullable=True),
        sa.Column('source_text', sa.Text(), nullable=True),
        sa.Column('page_no', sa.Integer(), nullable=True),
        sa.Column('review_status', sa.String(length=20), nullable=False),
        sa.Column('reviewer_id', sa.String(length=100), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['clause_id'], ['contract_clauses.id']),
        sa.ForeignKeyConstraint(['contract_id'], ['contracts.id']),
        sa.ForeignKeyConstraint(['field_id'], ['extracted_fields.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_contract_risks_contract_id'), 'contract_risks', ['contract_id'])
    op.create_index('ix_cr_contract_level', 'contract_risks', ['contract_id', 'risk_level'])
