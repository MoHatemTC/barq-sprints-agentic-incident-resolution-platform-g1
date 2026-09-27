#!/usr/bin/env bash
# Source from the repository root before running the local PDI demo.
# Values stay in the existing git-ignored credentials file and Docker containers.
if [ ! -f ../.secrets/barq-g1.env ]; then
    echo "Missing ../.secrets/barq-g1.env" >&2
    return 1
fi
set -a
. ../.secrets/barq-g1.env
set +a

export SERVICENOW_INSTANCE_URL="$PDI_INSTANCE_URL"
export SERVICENOW_CLIENT_ID="$PDI_SERVICENOW_CLIENT_ID"
export SERVICENOW_CLIENT_SECRET="$PDI_SERVICENOW_CLIENT_SECRET"
export SERVICENOW_USERNAME="$PDI_SERVICENOW_USERNAME"
export SERVICENOW_PASSWORD="$PDI_SERVICENOW_PASSWORD"
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432
export REDIS_HOST=127.0.0.1 REDIS_PORT=6379
export POSTGRES_PASSWORD="$(docker exec barq-postgres printenv POSTGRES_PASSWORD)"
export REDIS_PASSWORD="$(docker exec barq-redis printenv REDIS_PASSWORD)"
unset SN_ADMIN_USER SN_ADMIN_PASS SN407364_ADMIN_USER SN407364_ADMIN_PASS

if [ -z "$POSTGRES_PASSWORD" ] || [ -z "$REDIS_PASSWORD" ]; then
    echo "Missing a running local PostgreSQL or Redis container credential" >&2
    return 1
fi
