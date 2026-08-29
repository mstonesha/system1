#!/bin/sh
# Restore a custom-format pg_dump into the production database.
# Destructive. Stops the web service, restores, then starts web.
# Usage: deploy/restore-db.sh backups/akrasia-YYYYMMDDTHHMMSSZ.dump
# Hostinger: AKRASIA_DEPLOYMENT=hostinger deploy/restore-db.sh <dump>
# Uses start, not up, so the existing web container is not recreated.
set -eu

if [ "${1:-}" = "" ]; then
    echo "usage: $0 <dump-file>" >&2
    exit 1
fi

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$REPO_ROOT"

# shellcheck disable=SC1091
. "$REPO_ROOT/deploy/prod-compose.sh"

dump=$1
case "$dump" in
    /*) ;;
    *) dump="$REPO_ROOT/$dump" ;;
esac

if [ ! -s "$dump" ]; then
    echo "missing or empty dump: $dump" >&2
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "missing env file: $ENV_FILE" >&2
    exit 1
fi

$COMPOSE stop web
$COMPOSE exec -T db sh -c \
    'pg_restore --clean --if-exists --no-owner --exit-on-error -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    <"$dump"
$COMPOSE start web

echo "Restored $dump" >&2
