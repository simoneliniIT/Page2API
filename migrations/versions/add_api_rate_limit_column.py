"""add api_rate_limit column

Revision ID: 2024011602
Revises: e622feabd4c9
Create Date: 2024-01-16 10:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2024011602'
down_revision = 'e622feabd4c9'
branch_labels = None
depends_on = None


def upgrade():
    # Add api_rate_limit column with a default value of 100
    op.add_column('user', sa.Column('api_rate_limit', sa.Integer(), nullable=False, server_default='100'))


def downgrade():
    # Remove api_rate_limit column
    op.drop_column('user', 'api_rate_limit') 