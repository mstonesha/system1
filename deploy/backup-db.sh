#!/bin/sh
# Logical PostgreSQL backup for the production Compose stack.
# Run from the repository root on the VPS. Does not print credentials.
# Generic:  ./deploy/backup-db.sh
# Hostinger: AKRASIA_DEPLOYMENT=hostinger ./deploy/backup-db.sh
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$REPO_ROOT"

# shellcheck disable=SC1091
. "$REPO_ROOT/deploy/prod-compose.sh"

BACKUP_DIR=${BACKUP_DIR:-"$REPO_ROOT/backups"}

if [ ! -f "$ENV_FILE" ]; then
    echo "missing env file: $ENV_FILE" >&2
    exit 1
fi

if ! $COMPOSE ps db >/dev/null 2>&1; then
    echo "production db service is not available" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR" 2>/dev/null || true

stamp=$(date -u +%Y%m%dT%H%M%SZ)
outfile="$BACKUP_DIR/akrasia-${stamp}.dump"

$COMPOSE exec -T db sh -c \
    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner' \
    >"$outfile"

if [ ! -s "$outfile" ]; then
    echo "backup file is empty: $outfile" >&2
    rm -f "$outfile"
    exit 1
fi

echo "Wrote $outfile" >&2
