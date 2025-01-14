"""add api rate limit to user model

Revision ID: 202401141710
Revises: 0a45465c6eee
Create Date: 2024-01-14 17:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '202401141710'
down_revision = '0a45465c6eee'
branch_labels = None
depends_on = None


def upgrade():
    # PostgreSQL version
    op.execute("""
        DO $$ 
        BEGIN
            BEGIN
                ALTER TABLE "user" ADD COLUMN api_rate_limit INTEGER DEFAULT 100;
            EXCEPTION
                WHEN duplicate_column THEN 
                    NULL;
            END;
        END $$;
    """)


def downgrade():
    # PostgreSQL version
    op.execute("""
        DO $$ 
        BEGIN
            BEGIN
                ALTER TABLE "user" DROP COLUMN api_rate_limit;
            EXCEPTION
                WHEN undefined_column THEN 
                    NULL;
            END;
        END $$;
    """) 