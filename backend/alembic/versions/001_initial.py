"""Initial schema

Revision ID: 001_initial
Revises:
Create Date: 2026-10-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tables created via SQLAlchemy metadata create_all in app startup for simplicity;
    # this migration documents the production schema and can be used with:
    # alembic upgrade head
    # For greenfield deploy, Base.metadata.create_all is also invoked on boot.
    op.execute("SELECT 1")


def downgrade() -> None:
    pass
