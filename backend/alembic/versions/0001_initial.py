"""initial collaboration schema"""
from alembic import op
import sqlalchemy as sa
revision='0001_initial'
down_revision=None
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('documents',sa.Column('id',sa.String(36),primary_key=True),sa.Column('title',sa.String(240),nullable=False),sa.Column('owner_id',sa.String(120),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('version',sa.Integer(),nullable=False))
    op.create_index('ix_documents_owner_id','documents',['owner_id'])
    op.create_table('document_updates',sa.Column('id',sa.Integer(),primary_key=True,autoincrement=True),sa.Column('document_id',sa.String(36),sa.ForeignKey('documents.id',ondelete='CASCADE'),nullable=False),sa.Column('client_id',sa.String(120),nullable=False),sa.Column('update_id',sa.String(64),nullable=False),sa.Column('payload',sa.LargeBinary(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('document_id','update_id'))
    op.create_index('ix_document_updates_document_id','document_updates',['document_id'])
    op.create_table('revisions',sa.Column('id',sa.Integer(),primary_key=True,autoincrement=True),sa.Column('document_id',sa.String(36),sa.ForeignKey('documents.id',ondelete='CASCADE'),nullable=False),sa.Column('author_id',sa.String(120),nullable=False),sa.Column('kind',sa.String(32),nullable=False),sa.Column('summary',sa.String(500),nullable=False),sa.Column('snapshot',sa.Text(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_revisions_document_id','revisions',['document_id'])
def downgrade():
    op.drop_table('revisions');op.drop_table('document_updates');op.drop_table('documents')
