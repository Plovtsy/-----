"""Initial PostgreSQL schema for isolated demo and sandbox state.

Revision ID: 0001
Revises:
"""
from alembic import op
from kontur.infrastructure.database import Base
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    Base.metadata.create_all(bind=op.get_bind())

def downgrade():
    raise RuntimeError('Destructive downgrade is intentionally unsupported; restore from backup instead')
