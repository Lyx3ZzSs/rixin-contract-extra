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
    with op.batch_alter_table('contract_clauses') as batch_op:
        batch_op.add_column(sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('level', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('parent_id', sa.Uuid(), nullable=True))
        batch_op.create_foreign_key('fk_clause_parent', 'contract_clauses', ['parent_id'], ['id'])

    # -- extracted_fields: add source tracing columns --
    with op.batch_alter_table('extracted_fields') as batch_op:
        batch_op.add_column(sa.Column('source_paragraph_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('source_block_start', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('source_block_end', sa.Integer(), nullable=True))

    # -- ocr_blocks: add paragraph_id, font_size --
    with op.batch_alter_table('ocr_blocks') as batch_op:
        batch_op.add_column(sa.Column('paragraph_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('font_size', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('ocr_blocks') as batch_op:
        batch_op.drop_column('font_size')
        batch_op.drop_column('paragraph_id')

    with op.batch_alter_table('extracted_fields') as batch_op:
        batch_op.drop_column('source_block_end')
        batch_op.drop_column('source_block_start')
        batch_op.drop_column('source_paragraph_id')

    with op.batch_alter_table('contract_clauses') as batch_op:
        batch_op.drop_constraint('fk_clause_parent', type_='foreignkey')
        batch_op.drop_column('parent_id')
        batch_op.drop_column('level')
        batch_op.drop_column('sort_order')
