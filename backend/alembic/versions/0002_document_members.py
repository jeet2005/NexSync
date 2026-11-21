"""Add document membership permissions."""
from alembic import op
import sqlalchemy as sa
revision='0002_document_members'
down_revision='0001_initial'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('document_members', sa.Column('document_id', sa.String(36), sa.ForeignKey('documents.id', ondelete='CASCADE'), primary_key=True), sa.Column('user_id', sa.String(120), primary_key=True), sa.Column('role', sa.String(16), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_document_members_user_id','document_members',['user_id'])

def downgrade():
    op.drop_index('ix_document_members_user_id',table_name='document_members')
    op.drop_table('document_members')
