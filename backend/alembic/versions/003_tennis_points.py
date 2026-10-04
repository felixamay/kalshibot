"""Durable live tennis point events and last-two-point analysis."""
from alembic import op
import sqlalchemy as sa

revision = '003_tennis_points'
down_revision = '002_service_blocks'
branch_labels = None
depends_on = None


def upgrade():
    for name in ('tennis_points', 'tennis_point_states'):
        op.create_table(name, sa.Column('id', sa.String(512), primary_key=True),
                        sa.Column('match_id', sa.String(128), nullable=False),
                        sa.Column('payload', sa.Text, nullable=False),
                        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
        op.create_index(f'ix_{name}_match_id', name, ['match_id'])


def downgrade():
    for name in ('tennis_point_states', 'tennis_points'):
        op.drop_table(name)
