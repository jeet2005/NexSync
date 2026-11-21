"""Add compacted Yjs snapshots."""
from alembic import op
import sqlalchemy as sa
revision='0003_document_snapshots'
down_revision='0002_document_members'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('document_snapshots', sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True), sa.Column('document_id', sa.String(36), sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False), sa.Column('watermark_id', sa.Integer(), nullable=False), sa.Column('payload', sa.LargeBinary(), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint('document_id','watermark_id'))
    op.create_index('ix_document_snapshots_document_id','document_snapshots',['document_id'])
def downgrade():
    op.drop_index('ix_document_snapshots_document_id',table_name='document_snapshots')
    op.drop_table('document_snapshots')
