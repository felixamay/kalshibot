"""Durable GPT pattern predictions and observed outcomes."""
from alembic import op
import sqlalchemy as sa
revision = '004_gpt_patterns'
down_revision = '003_tennis_points'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('gpt_pattern_analyses', sa.Column('id', sa.String(512), primary_key=True),
                    sa.Column('match_id', sa.String(128), nullable=False),
                    sa.Column('payload', sa.Text, nullable=False),
                    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index('ix_gpt_pattern_analyses_match_id', 'gpt_pattern_analyses', ['match_id'])


def downgrade():
    op.drop_table('gpt_pattern_analyses')
