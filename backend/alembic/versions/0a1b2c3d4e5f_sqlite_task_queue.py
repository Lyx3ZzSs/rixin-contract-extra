"""sqlite_task_queue

Revision ID: 0a1b2c3d4e5f
Revises: f6a7b8c9d0e1
Create Date: 2026-06-17 15:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0a1b2c3d4e5f'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('contract_tasks') as batch_op:
        batch_op.add_column(sa.Column('stage', sa.String(50), nullable=True))
        batch_op.add_column(sa.Column('error_code', sa.String(100), nullable=True))
        batch_op.add_column(sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('max_attempts', sa.Integer(), nullable=False, server_default='3'))
        batch_op.add_column(sa.Column('priority', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('task_payload', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('queued_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('leased_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('lease_expires_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('worker_id', sa.String(100), nullable=True))
        batch_op.add_column(sa.Column('last_heartbeat_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('timeout_seconds', sa.Integer(), nullable=False, server_default='1800'))
        batch_op.add_column(sa.Column('cancel_requested_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('next_run_at', sa.DateTime(), nullable=True))

    op.create_index('ix_ct_queue_claim', 'contract_tasks', ['status', 'next_run_at', 'priority', 'queued_at'])
    op.create_index('ix_ct_lease', 'contract_tasks', ['status', 'lease_expires_at'])
    op.create_index('ix_ocr_contract_page_order', 'ocr_blocks', ['contract_id', 'page_no', 'sort_order'])
    op.create_index('ux_ef_contract_field', 'extracted_fields', ['contract_id', 'field_key'], unique=True)


def downgrade() -> None:
    op.drop_index('ux_ef_contract_field', table_name='extracted_fields')
    op.drop_index('ix_ocr_contract_page_order', table_name='ocr_blocks')
    op.drop_index('ix_ct_lease', table_name='contract_tasks')
    op.drop_index('ix_ct_queue_claim', table_name='contract_tasks')

    with op.batch_alter_table('contract_tasks') as batch_op:
        batch_op.drop_column('next_run_at')
        batch_op.drop_column('cancel_requested_at')
        batch_op.drop_column('timeout_seconds')
        batch_op.drop_column('last_heartbeat_at')
        batch_op.drop_column('worker_id')
        batch_op.drop_column('lease_expires_at')
        batch_op.drop_column('leased_at')
        batch_op.drop_column('queued_at')
        batch_op.drop_column('task_payload')
        batch_op.drop_column('priority')
        batch_op.drop_column('max_attempts')
        batch_op.drop_column('attempts')
        batch_op.drop_column('error_code')
        batch_op.drop_column('stage')
