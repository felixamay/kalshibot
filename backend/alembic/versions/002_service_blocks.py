"""Persist completed service games and full block reasoning."""
from alembic import op
import sqlalchemy as sa
revision = '002_service_blocks'
down_revision = '001_initial'
branch_labels = None
depends_on = None


def upgrade():
    for name in ('service_games', 'serve_blocks', 'serve_block_features', 'serve_block_reasoning', 'tennis_market_divergence', 'reasoning_history'):
        op.create_table(name, sa.Column('id', sa.String(512), primary_key=True),
            sa.Column('match_id', sa.String(128), nullable=False), sa.Column('payload', sa.Text, nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
        op.create_index(f'ix_{name}_match_id', name, ['match_id'])


def downgrade():
    for name in ('reasoning_history', 'tennis_market_divergence', 'serve_block_reasoning', 'serve_block_features', 'serve_blocks', 'service_games'):
        op.drop_table(name)
