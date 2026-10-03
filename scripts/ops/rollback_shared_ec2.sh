#!/usr/bin/env bash
# Return the shared EC2 backend to a known release after a hand-deployed branch test.
#
# Run ON the EC2 host, from the application checkout:
#   scripts/ops/rollback_shared_ec2.sh <backup-dir> [--restore-db] [--restore-qdrant]
#
# <backup-dir> holds deployed_sha.txt and alembic_version.txt written before the
# branch was deployed (see docs/barq_agentic_platform_design.md, section 18).
#
# Order matters: the schema is downgraded with the CURRENT image, because only the
# branch's code knows how to undo the branch's migrations. Then the previous release
# is checked out and rebuilt, exactly like the CI deploy does.
set -euo pipefail

backup_dir="${1:?usage: rollback_shared_ec2.sh <backup-dir> [--restore-db] [--restore-qdrant]}"
shift
restore_db=false
restore_qdrant=false
for arg in "$@"; do
  case "$arg" in
    --restore-db) restore_db=true ;;
    --restore-qdrant) restore_qdrant=true ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

target_sha="$(cat "$backup_dir/deployed_sha.txt")"
target_revision="$(cat "$backup_dir/alembic_version.txt")"
echo "==> Rolling back to $target_sha (schema $target_revision)"

# Neither the old nor the new application may write while tables are being changed.
# Keep a fresh recovery point: downgrade removes branch-only chat/feedback data and
# the event actor column even when it preserves the incident event rows themselves.
recovery_dir="$HOME/barq-backups/pre-rollback-$(date -u +%Y%m%dT%H%M%SZ)"
umask 077
mkdir -p "$recovery_dir"
git rev-parse HEAD > "$recovery_dir/deployed_sha.txt"
cp .env "$recovery_dir/env.backup"
docker compose stop -t 180 api celery-worker
docker exec barq-postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "$recovery_dir/current.dump"
echo "==> Fresh recovery snapshot saved to $recovery_dir"

if [ "$restore_db" = true ]; then
  echo "==> Restoring PostgreSQL from $backup_dir/barq_incident_dev.dump"
  docker exec -i barq-postgres pg_restore -U postgres -d barq_incident_dev --clean --if-exists \
    < "$backup_dir/barq_incident_dev.dump"
else
  echo "==> Downgrading the schema to $target_revision with the current image"
  docker compose run --rm --no-deps -T api alembic downgrade "$target_revision" < /dev/null
fi

if [ "$restore_qdrant" = true ]; then
  snapshot="$(cat "$backup_dir/qdrant_snapshot_name.txt")"
  echo "==> Restoring Qdrant collection from snapshot $snapshot"
  curl --fail --silent -X PUT \
    "http://127.0.0.1:16333/collections/incident_knowledge_base/snapshots/recover" \
    -H 'Content-Type: application/json' \
    -d "{\"location\": \"file:///qdrant/snapshots/incident_knowledge_base/$snapshot\"}" > /dev/null
fi

if [ -f "$backup_dir/env.backup" ]; then
  echo "==> Restoring .env from the backup"
  cp "$backup_dir/env.backup" .env
fi

echo "==> Checking out $target_sha and rebuilding"
git fetch --no-tags origin "$target_sha"
git checkout --detach "$target_sha"
docker compose build api celery-worker
docker compose up -d --no-build api celery-worker

for i in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:8000/ready > /dev/null; then break; fi
  echo "Waiting for API readiness ($i/30)..."
  sleep 2
done
curl --fail --silent http://127.0.0.1:8000/ready > /dev/null
docker compose exec -T celery-worker celery -A app.workers.celery_app inspect ping --timeout 10
current_revision="$(docker exec barq-postgres psql -U postgres -d barq_incident_dev -Atc 'select version_num from alembic_version')"
echo "==> Rolled back: code $(git rev-parse --short HEAD), schema $current_revision"
test "$current_revision" = "$target_revision"
