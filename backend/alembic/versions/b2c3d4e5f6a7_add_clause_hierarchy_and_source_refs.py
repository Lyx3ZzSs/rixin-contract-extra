"""add_clause_hierarchy_and_source_refs

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-16 13:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- contract_clauses: add level, parent_id, sort_order --
    op.add_column('contract_clauses', sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('contract_clauses', sa.Column('level', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('contract_clauses', sa.Column('parent_id', sa.Uuid(), nullable=True))
    op.create_foreign_key('fk_clause_parent', 'contract_clauses', 'contract_clauses', ['parent_id'], ['id'])

    # -- extracted_fields: add source tracing columns --
    op.add_column('extracted_fields', sa.Column('source_paragraph_id', sa.Integer(), nullable=True))
    op.add_column('extracted_fields', sa.Column('source_block_start', sa.Integer(), nullable=True))
    op.add_column('extracted_fields', sa.Column('source_block_end', sa.Integer(), nullable=True))

    # -- ocr_blocks: add paragraph_id, font_size --
    op.add_column('ocr_blocks', sa.Column('paragraph_id', sa.Integer(), nullable=True))
    op.add_column('ocr_blocks', sa.Column('font_size', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('ocr_blocks', 'font_size')
    op.drop_column('ocr_blocks', 'paragraph_id')

    op.drop_column('extracted_fields', 'source_block_end')
    op.drop_column('extracted_fields', 'source_block_start')
    op.drop_column('extracted_fields', 'source_paragraph_id')

    op.drop_constraint('fk_clause_parent', 'contract_clauses', type_='foreignkey')
    op.drop_column('contract_clauses', 'parent_id')
    op.drop_column('contract_clauses', 'level')
    op.drop_column('contract_clauses', 'sort_order')
