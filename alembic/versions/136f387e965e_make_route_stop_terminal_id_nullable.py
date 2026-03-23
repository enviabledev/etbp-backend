"""add missing columns to route_stops and make terminal_id nullable

Revision ID: 136f387e965e
Revises:
Create Date: 2026-03-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '136f387e965e'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('route_stops', 'terminal_id',
                    existing_type=sa.UUID(),
                    nullable=True)
    op.add_column('route_stops', sa.Column('name', sa.String(255), nullable=True))
    op.add_column('route_stops', sa.Column('city', sa.String(255), nullable=True))
    op.add_column('route_stops', sa.Column('latitude', sa.Float, nullable=True))
    op.add_column('route_stops', sa.Column('longitude', sa.Float, nullable=True))
    op.add_column('route_stops', sa.Column('stop_duration_minutes', sa.Integer, server_default='0', nullable=False))
    op.add_column('route_stops', sa.Column('is_rest_stop', sa.Boolean, server_default='true', nullable=False))
    op.add_column('route_stops', sa.Column('notes', sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column('route_stops', 'notes')
    op.drop_column('route_stops', 'is_rest_stop')
    op.drop_column('route_stops', 'stop_duration_minutes')
    op.drop_column('route_stops', 'longitude')
    op.drop_column('route_stops', 'latitude')
    op.drop_column('route_stops', 'city')
    op.drop_column('route_stops', 'name')
    op.alter_column('route_stops', 'terminal_id',
                    existing_type=sa.UUID(),
                    nullable=False)
