"""Initial migration

Revision ID: e622feabd4c9
Revises: 
Create Date: 2024-01-16 16:01:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e622feabd4c9'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create user table with all columns including api_rate_limit
    op.create_table('user',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=120), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('password_hash', sa.String(length=128), nullable=False),
        sa.Column('user_type', sa.String(length=20), nullable=False),
        sa.Column('api_rate_limit', sa.Integer(), nullable=False, server_default='100'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )

    # Create product table
    op.create_table('product',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('api_spec', sa.Text(), nullable=True),
        sa.Column('reward_percentage', sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Create template table
    op.create_table('template',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Create api_key table
    op.create_table('api_key',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('requests_count', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key')
    )


def downgrade():
    op.drop_table('api_key')
    op.drop_table('template')
    op.drop_table('product')
    op.drop_table('user')
