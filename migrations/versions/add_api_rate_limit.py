"""Add api_rate_limit to User model

Revision ID: add_api_rate_limit
Revises: 0a45465c6eee
Create Date: 2024-01-14 16:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_api_rate_limit'
down_revision = '0a45465c6eee'
branch_labels = None
depends_on = None


def upgrade():
    # Add api_rate_limit column to user table with default value of 100
    op.add_column('user', sa.Column('api_rate_limit', sa.Integer(), nullable=True, server_default='100'))


def downgrade():
    # Remove api_rate_limit column from user table
    op.drop_column('user', 'api_rate_limit') 