# Sourced by backup-db.sh and restore-db.sh from the repository root.
# Sets COMPOSE for the production stack. Not an application setting.
#
# Default (generic Caddy):
#   docker compose -f docker-compose.prod.yml --env-file .env.production
# Hostinger (existing Traefik):
#   AKRASIA_DEPLOYMENT=hostinger
#   adds -f docker-compose.hostinger.yml so Caddy stays omitted and
#   Traefik labels/network on web survive stop/start.

ENV_FILE=${ENV_FILE:-.env.production}

_compose_files="-f docker-compose.prod.yml"
case "${AKRASIA_DEPLOYMENT:-generic}" in
    generic)
        ;;
    hostinger)
        if [ ! -f docker-compose.hostinger.yml ]; then
            echo "missing docker-compose.hostinger.yml" >&2
            exit 1
        fi
        _compose_files="${_compose_files} -f docker-compose.hostinger.yml"
        ;;
    *)
        echo "unknown AKRASIA_DEPLOYMENT=${AKRASIA_DEPLOYMENT} (use generic or hostinger)" >&2
        exit 1
        ;;
esac

COMPOSE="docker compose ${_compose_files} --env-file ${ENV_FILE}"
unset _compose_files
