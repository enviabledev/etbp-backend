#!/usr/bin/env bash
# post_deploy.sh — Run after deployment to bring the database up to date.
# Usage: bash scripts/post_deploy.sh

set -euo pipefail

echo "=== ETBP Post-Deploy ==="

# 1. Try Alembic migrations first
echo "→ Running Alembic migrations..."
if alembic upgrade head 2>/dev/null; then
    echo "  ✓ Alembic migrations applied."
else
    echo "  ⚠ Alembic upgrade failed (expected on first deploy or missing history)."
    echo "  → Falling back to manual migrations..."

    # 2. Fallback: run the safe, idempotent manual SQL
    if [ -z "${DATABASE_URL:-}" ]; then
        echo "  ✗ DATABASE_URL not set. Cannot run manual migrations."
        exit 1
    fi
    psql "$DATABASE_URL" -f scripts/manual_migrations.sql
    echo "  ✓ Manual migrations applied."
fi

# 3. Sync enum types (idempotent)
echo "→ Syncing enum types..."
python scripts/sync_enums.py 2>/dev/null || echo "  ⚠ sync_enums.py skipped (non-critical)."

echo "=== Post-Deploy Complete ==="
