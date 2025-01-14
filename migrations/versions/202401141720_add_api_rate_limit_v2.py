"""add api rate limit to user model v2

Revision ID: 202401141720
Revises: 202401141710
Create Date: 2024-01-14 17:20:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '202401141720'
down_revision = '202401141710'
branch_labels = None
depends_on = None


def upgrade():
    # Get database connection
    connection = op.get_bind()
    
    # Check if we're using PostgreSQL
    is_postgresql = connection.dialect.name == 'postgresql'
    
    if is_postgresql:
        # PostgreSQL-specific implementation
        op.execute("""
            DO $$ 
            BEGIN
                BEGIN
                    ALTER TABLE "user" ADD COLUMN api_rate_limit INTEGER;
                    ALTER TABLE "user" ALTER COLUMN api_rate_limit SET DEFAULT 100;
                    UPDATE "user" SET api_rate_limit = 100 WHERE api_rate_limit IS NULL;
                EXCEPTION
                    WHEN duplicate_column THEN 
                        NULL;
                END;
            END $$;
        """)
    else:
        # SQLite implementation
        try:
            op.add_column('user', sa.Column('api_rate_limit', sa.Integer(), nullable=True, server_default='100'))
        except Exception:
            pass  # Column might already exist


def downgrade():
    # Get database connection
    connection = op.get_bind()
    
    # Check if we're using PostgreSQL
    is_postgresql = connection.dialect.name == 'postgresql'
    
    if is_postgresql:
        # PostgreSQL-specific implementation
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
    else:
        # SQLite implementation
        try:
            op.drop_column('user', 'api_rate_limit')
        except Exception:
            pass  # Column might not exist 